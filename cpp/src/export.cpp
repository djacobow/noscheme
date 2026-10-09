#include "noscheme/export.hpp"
#include "noscheme/validation.hpp"
#include <algorithm>
#include <array>
#include <cctype>
#include <iomanip>
#include <openssl/evp.h>
#include <regex>
#include <sstream>

namespace noscheme {
namespace {
[[noreturn]] void fail(const fs::path &path, const std::string &code,
                       const std::string &message) {
  Source source("", path.string());
  throw DiagnosticsFailure(
      Json::array({source.diagnostic(Failure(code, message, 0, 0))}));
}
std::string quote(const std::string &text) {
  // KiCad S-expressions use Lisp string escapes, not JSON's \u escapes.
  std::string result = "\"";
  for (char c : text) {
    switch (c) {
    case '\\':
      result += "\\\\";
      break;
    case '\"':
      result += "\\\"";
      break;
    case '\b':
      result += "\\b";
      break;
    case '\f':
      result += "\\f";
      break;
    case '\n':
      result += "\\n";
      break;
    case '\r':
      result += "\\r";
      break;
    case '\t':
      result += "\\t";
      break;
    default:
      result += c;
    }
  }
  return result + "\"";
}
std::string repr(const std::string &text) {
  // Python's repr chooses double quotes only when that avoids escaping a quote.
  char delimiter = text.find('\'') != std::string::npos &&
                           text.find('"') == std::string::npos
                       ? '"'
                       : '\'';
  std::string result(1, delimiter);
  for (char c : text) {
    if (c == delimiter || c == '\\')
      result += '\\';
    if (c == '\n')
      result += "\\n";
    else if (c == '\r')
      result += "\\r";
    else if (c == '\t')
      result += "\\t";
    else
      result += c;
  }
  return result + delimiter;
}
std::string py_list(const std::set<std::string> &values) {
  std::string text = "[";
  for (const auto &v : values)
    text += (text.size() > 1 ? ", " : "") + repr(v);
  return text + "]";
}
std::string lower(std::string value) {
  for (auto &c : value)
    c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
  return value;
}
bool reference_valid(const std::string &value) {
  static const std::regex pattern("[A-Za-z_#][A-Za-z0-9_#]*");
  return std::regex_match(value, pattern);
}
Json attribute(const Json &part, const std::string &name,
               Json fallback = nullptr) {
  const auto &metadata = part.at("metadata");
  return metadata.contains(name) ? metadata.at(name).at("value") : fallback;
}
std::string digest(const std::string &text, const EVP_MD *algorithm) {
  std::array<unsigned char, EVP_MAX_MD_SIZE> bytes{};
  unsigned int length = 0;
  if (EVP_Digest(text.data(), text.size(), bytes.data(), &length, algorithm,
                 nullptr) != 1)
    throw std::runtime_error("cannot compute export digest");
  return std::string(reinterpret_cast<const char *>(bytes.data()), length);
}
std::string hex(const std::string &bytes) {
  std::ostringstream out;
  for (unsigned char c : bytes)
    out << std::hex << std::setw(2) << std::setfill('0')
        << static_cast<unsigned>(c);
  return out.str();
}
std::string uuid_bytes(std::string uuid) {
  if (uuid.starts_with("urn:uuid:"))
    uuid.erase(0, 9);
  if (uuid.starts_with("{"))
    uuid.erase(0, 1);
  if (uuid.ends_with("}"))
    uuid.pop_back();
  std::erase(uuid, '-');
  if (uuid.size() != 32 || !std::ranges::all_of(uuid, [](unsigned char c) {
        return std::isxdigit(c);
      }))
    throw std::invalid_argument("badly formed hexadecimal UUID string");
  std::string result;
  for (std::size_t i = 0; i < uuid.size(); i += 2)
    result += static_cast<char>(std::stoi(uuid.substr(i, 2), nullptr, 16));
  return result;
}
std::string uuid5(const std::string &space, const std::string &identity) {
  auto bytes = digest(uuid_bytes(space) + identity, EVP_sha1()).substr(0, 16);
  bytes[6] =
      static_cast<char>((static_cast<unsigned char>(bytes[6]) & 15) | 0x50);
  bytes[8] =
      static_cast<char>((static_cast<unsigned char>(bytes[8]) & 63) | 0x80);
  auto h = hex(bytes);
  return h.substr(0, 8) + "-" + h.substr(8, 4) + "-" + h.substr(12, 4) + "-" +
         h.substr(16, 4) + "-" + h.substr(20);
}
void validate_state(const Json &state, const fs::path &path) {
  try {
    if (state.at("schema_version") != 1)
      throw std::invalid_argument("unsupported state version");
    (void)uuid_bytes(state.at("namespace"));
    if (!state.at("entries").is_object())
      throw std::invalid_argument("invalid entry fields");
    std::map<std::string, std::string> refs;
    std::set<std::string> uuids;
    for (const auto &[identity, entry] : state.at("entries").items()) {
      if (identity.empty())
        throw std::invalid_argument("invalid identity");
      if (!uuids.insert(uuid_bytes(entry.at("uuid"))).second)
        throw std::invalid_argument("duplicate UUID");
      if (!entry.at("active").is_boolean() ||
          !entry.at("reserved_references").is_array())
        throw std::invalid_argument("invalid entry fields");
      const auto &reserved = entry.at("reserved_references");
      if (std::find(reserved.begin(), reserved.end(), entry.at("reference")) ==
          reserved.end())
        throw std::invalid_argument("current reference is not reserved");
      for (const auto &ref : reserved) {
        if (!ref.is_string() || !reference_valid(ref.get<std::string>()))
          throw std::invalid_argument("invalid reference");
        auto key = lower(ref);
        if (refs.contains(key) && refs.at(key) != identity)
          throw std::invalid_argument("reference collision");
        refs[key] = identity;
      }
    }
    if (!state.value("previous_parts", Json::object()).is_object())
      throw std::invalid_argument("invalid previous design");
  } catch (const std::exception &error) {
    fail(path, "state.invalid", error.what());
  }
}
Json assign(const Json &circuit, Json state, const fs::path &path) {
  auto &entries = state["entries"];
  std::map<std::string, std::string> owners, explicit_refs;
  for (auto &[identity, entry] : entries.items()) {
    entry["active"] = false;
    for (const auto &ref : entry["reserved_references"])
      owners[lower(ref)] = identity;
  }
  std::map<std::string, Json> parts;
  for (const auto &part : circuit["parts"]) {
    std::string id = part["identity"];
    parts[id] = part;
    auto reference = attribute(part, "ref");
    if (reference.is_null())
      continue;
    if (!reference.is_string() || !reference_valid(reference))
      fail(path, "reference.invalid",
           "invalid KiCad reference " +
               (reference.is_string() ? repr(reference.get<std::string>())
                                      : reference.dump()));
    std::string ref = reference, key = lower(ref);
    if (owners.contains(key) && owners.at(key) != id)
      fail(path, "reference.reserved",
           repr(ref) + " is reserved by " + repr(owners.at(key)));
    owners[key] = id;
    explicit_refs[id] = ref;
  }
  for (const auto &[id, part] : parts) {
    std::string ref;
    if (explicit_refs.contains(id))
      ref = explicit_refs.at(id);
    else if (entries.contains(id))
      ref = entries[id]["reference"];
    else {
      std::string prefix = attribute(part, "ref_prefix", "U");
      if (!reference_valid(prefix))
        fail(path, "reference.prefix",
             "invalid reference prefix " + repr(prefix));
      std::size_t n = 1;
      while (owners.contains(lower(prefix + std::to_string(n))))
        ++n;
      ref = prefix + std::to_string(n);
    }
    owners[lower(ref)] = id;
    if (!entries.contains(id))
      entries[id] = object("uuid", uuid5(state["namespace"], id),
                           "reserved_references", Json::array());
    auto &entry = entries[id];
    entry["reference"] = ref;
    entry["active"] = true;
    auto &reserved = entry["reserved_references"];
    if (std::find(reserved.begin(), reserved.end(), ref) == reserved.end())
      reserved.push_back(ref);
  }
  return state;
}
Json verify(const Json &circuit, const Json &snapshot, const fs::path &path) {
  if (!snapshot.is_object() || snapshot.value("schema_version", Json()) != 1 ||
      !snapshot.value("footprints", Json()).is_object())
    fail(path, "footprint.records", "unsupported footprint-record schema");
  auto omitted = Json::array();
  for (const auto &part : circuit["parts"]) {
    auto footprint = attribute(part, "footprint");
    if (footprint.is_null() || footprint == "") {
      omitted.push_back(part["identity"]);
      continue;
    }
    std::string name = footprint, id = part["identity"];
    auto record = snapshot["footprints"].value(name, Json());
    if (!record.is_object() || record.value("exists", Json()) != Json(true))
      fail(path, "footprint.unknown",
           id + ": no converted record for " + repr(name));
    auto pads = record.value("pads", Json());
    if (!pads.is_array() || !std::ranges::all_of(pads, [](const auto &p) {
          return p.is_string() && p != "";
        }))
      fail(path, "footprint.records", "invalid pad record for " + repr(name));
    std::set<std::string> missing, extra = pads.get<std::set<std::string>>();
    for (const auto &[pin, data] : part["pins"].items())
      if (!extra.erase(pin))
        missing.insert(pin);
    if (!missing.empty() || !extra.empty())
      fail(path, "footprint.mismatch",
           id + ": " + name + ": pins without pads " + py_list(missing) +
               ", pads without pins " + py_list(extra));
  }
  return omitted;
}
std::string form(const std::string &tag,
                 const std::vector<std::string> &children) {
  std::string text = "(" + tag;
  for (const auto &child : children)
    text += " " + child;
  return text + ")";
}
std::string field(const std::string &tag, const std::string &value) {
  return form(tag, {quote(value)});
}
std::string part_value(const Json &part) {
  const auto &value = part["value"];
  if (value.is_null()) {
    std::string kind = part["definition"];
    return kind.substr(kind.rfind('.') + 1);
  }
  if (value["value"].is_string())
    return value["value"];
  auto text = value["value"].dump();
  if (text.ends_with(".0"))
    text.resize(text.size() - 2);
  return text;
}
std::string path_name(const Json &path, bool net) {
  std::vector<std::string> segments;
  for (const auto &part : path)
    if (part.is_string())
      segments.push_back(part);
    else
      segments.back() += "[" + part.dump() + "]";
  std::string result = net && segments.size() > 1 ? "/" : "";
  for (const auto &segment : segments) {
    if (!result.empty() && result != "/")
      result += net ? "/" : ".";
    result += segment;
  }
  return result;
}
std::string pin_kind(const Json &pin) {
  static const std::map<std::string, std::string> types{
      {"passive", "passive"},
      {"digital_input", "input"},
      {"analog_input", "input"},
      {"digital_output", "output"},
      {"analog_output", "output"},
      {"digital_bidirectional", "bidirectional"},
      {"analog_bidirectional", "bidirectional"},
      {"power_input", "power_in"},
      {"ground", "power_in"},
      {"power_output", "power_out"},
      {"open_drain", "open_collector"},
      {"open_source", "open_emitter"},
      {"do_not_connect", "no_connect"},
      {"unchecked", "unspecified"}};
  auto roles = attribute(pin, "role", Json::array());
  std::string kind = "unspecified";
  if (roles.size() == 1 && types.contains(roles[0]))
    kind = types.at(roles[0]);
  if (kind == "output" && attribute(pin, "tristate_capable", false) == true)
    kind = "tri_state";
  return kind;
}
struct Rendered {
  std::string netlist;
  Json names = Json::object(), classes = Json::object();
};
Rendered render(const Json &circuit, const Json &state) {
  Rendered result;
  std::map<std::string, std::string> net_names;
  std::set<std::string> reserved;
  for (const auto &net : circuit["nets"]) {
    auto name = path_name(net["path"], true);
    if (result.names.contains(name) &&
        result.names[name]["path"] != net["path"])
      throw std::invalid_argument("exported net-name collision: " + repr(name));
    result.names[name] =
        object("source_name", net["name"], "path", net["path"]);
    net_names[net["name"]] = name;
    reserved.insert(name);
    std::set<std::string> classes;
    for (const auto &decl : net["declarations"]) {
      auto c = attribute(decl, "net_class");
      if (!c.is_null())
        classes.insert(c);
    }
    if (classes.size() > 1)
      throw std::invalid_argument("conflicting classes on net " +
                                  repr(net["name"]));
    if (!classes.empty())
      result.classes[name] = classes;
  }
  std::map<std::string, std::vector<Json>> definitions;
  std::map<std::string, Json> parts;
  for (const auto &part : circuit["parts"])
    if (auto fp = attribute(part, "footprint"); !fp.is_null() && fp != "") {
      definitions[part["definition"]].push_back(part);
      parts[part["identity"]] = part;
    }
  std::vector<std::string> components;
  std::map<std::string, std::string> libparts;
  std::map<std::string, std::vector<std::string>> nets;
  for (const auto &[id, part] : parts) {
    const auto &entry = state["entries"][id];
    std::string ref = entry["reference"], kind = part["definition"];
    std::vector<std::string> component{
        field("ref", ref), field("value", part_value(part)),
        field("footprint", attribute(part, "footprint")),
        form("libsource", {field("lib", "NoScheme"), field("part", kind)})},
        fields;
    for (const auto &[name, value] :
         std::vector<std::pair<std::string, std::string>>{
             {"noscheme_identity", id},
             {"noscheme_path", path_name(part["path"], false)}}) {
      component.push_back(
          form("property", {field("name", name), field("value", value)}));
      fields.push_back(form("field", {field("name", name), quote(value)}));
    }
    component.push_back(form("fields", fields));
    component.push_back(
        form("sheetpath", {field("names", "/"), field("tstamps", "/")}));
    component.push_back(field("tstamps", entry["uuid"]));
    components.push_back(form("comp", component));
    if (!libparts.contains(kind)) {
      std::vector<std::string> pins;
      const auto &group = definitions.at(kind);
      for (const auto &[number, pin] : group[0]["pins"].items()) {
        auto aliases = attribute(pin, "aliases", Json::array());
        std::set<std::string> kinds;
        for (const auto &p : group)
          kinds.insert(pin_kind(p["pins"][number]));
        pins.push_back(form(
            "pin",
            {field("num", number),
             field("name",
                   aliases.empty() ? number : aliases[0].get<std::string>()),
             field("type",
                   kinds.size() == 1 ? *kinds.begin() : "unspecified")}));
      }
      libparts[kind] =
          form("libpart", {field("lib", "NoScheme"), field("part", kind),
                           form("pins", pins)});
    }
    for (const auto &[pin, data] : part["pins"].items()) {
      std::vector<std::string> node{field("ref", ref), field("pin", pin)};
      if (!data["net"].is_null())
        nets[net_names.at(data["net"])].push_back(form("node", node));
      else {
        auto roles = attribute(data, "role", Json::array());
        if (data["binding"] != "no_connect" &&
            std::find(roles.begin(), roles.end(), "do_not_connect") ==
                roles.end())
          continue;
        std::string base = "unconnected-(" + entry["uuid"].get<std::string>() +
                           "-Pad" + pin + ")",
                    name = base;
        for (std::size_t suffix = 1; reserved.contains(name); ++suffix)
          name = base + "_" + std::to_string(suffix);
        reserved.insert(name);
        node.push_back(field("pintype", "no_connect"));
        nets[name] = {form("node", node)};
      }
    }
  }
  std::vector<std::string> libraries, netlist;
  for (const auto &[kind, part] : libparts)
    libraries.push_back(part);
  std::size_t code = 0;
  for (const auto &[name, nodes] : nets) {
    std::vector<std::string> entries{form("code", {std::to_string(++code)}),
                                     field("name", name)};
    entries.insert(entries.end(), nodes.begin(), nodes.end());
    netlist.push_back(form("net", entries));
  }
  result.netlist =
      form("export", {field("version", "E"),
                      form("design", {field("source", circuit["board"]),
                                      field("tool", "NoScheme 0.1")}),
                      form("components", components),
                      form("libparts", libraries), form("nets", netlist)}) +
      "\n";
  return result;
}
std::string display_json(const Json &value) {
  if (!value.is_structured())
    return value.dump(-1, ' ', false);
  std::string text = value.is_array() ? "[" : "{";
  bool first = true;
  for (const auto &[key, child] : value.items()) {
    if (!first)
      text += ", ";
    first = false;
    if (value.is_object())
      text += Json(key).dump(-1, ' ', false) + ": ";
    text += display_json(child);
  }
  return text + (value.is_array() ? "]" : "}");
}
std::string display(Json value) {
  if (value.is_object() && value.contains("type") && value.contains("value"))
    value = value["value"];
  return value.is_null() ? "unconnected" : display_json(value);
}
} // namespace

ExportResult export_kicad(const Project &project, const Circuit &circuit,
                          const ErcResult &erc, Json state,
                          const Json &footprints, const fs::path &records,
                          const std::string &records_text) {
  auto state_path = project.config.path.parent_path() / "noscheme.state.json";
  validate_state(state, state_path);
  auto data = to_json(circuit);
  auto omitted = verify(data, footprints, records);
  auto updated = assign(data, state, state_path);
  ExportResult result{Json::object(), Json::array(), Json::array()};
  for (const auto &part : data["parts"]) {
    std::string reference =
        updated["entries"][part["identity"].get<std::string>()]["reference"];
    if (reference.starts_with("#")) {
      auto span = part["source"]["declaration"];
      for (const auto &key : {"ref_prefix", "ref"})
        if (part["metadata"].contains(key))
          span = part["metadata"][key]["span"];
      result.warnings.push_back(object(
          "code", "reference.leading-hash", "severity", "warning", "message",
          "KiCad reference " + repr(reference) +
              " starts with '#'; downstream BOM or placement tools may treat "
              "it as a virtual symbol and omit it",
          "span", span, "related", Json::array(), "instance_path",
          part["identity"], "configuration", nullptr, "application", nullptr));
    }
  }
  Rendered rendered;
  try {
    rendered = render(data, updated);
  } catch (const std::invalid_argument &error) {
    fail(project.config.path, "export.names", error.what());
  }
  auto current = Json::object();
  for (const auto &part : data["parts"]) {
    auto pins = Json::object();
    for (const auto &[name, pin] : part["pins"].items())
      pins[name] = pin["net"];
    std::string id = part["identity"];
    current[id] =
        object("path", part["path"], "footprint", attribute(part, "footprint"),
               "value", part["value"], "reference",
               updated["entries"][id]["reference"], "pins", pins);
  }
  auto old = state.value("previous_parts", Json::object());
  auto changes = object("added", Json::array(), "removed", Json::array(),
                        "changed", Json::array());
  std::map<std::string, std::vector<std::string>> removed, added;
  auto signature = [](const Json &part) {
    return object("footprint", part.value("footprint", Json()), "value",
                  part.value("value", Json()), "pins",
                  part.value("pins", Json()))
        .dump();
  };
  for (const auto &[id, part] : current.items()) {
    if (!old.contains(id)) {
      changes["added"].push_back(id);
      added[signature(part)].push_back(id);
    } else if (old[id] != part)
      changes["changed"].push_back(
          object("identity", id, "before", old[id], "after", part));
  }
  for (const auto &[id, part] : old.items())
    if (!current.contains(id)) {
      changes["removed"].push_back(id);
      removed[signature(part)].push_back(id);
    }
  auto hints = Json::array();
  for (const auto &[key, ids] : removed)
    if (ids.size() == 1 && added[key].size() == 1)
      hints.push_back(object("before", ids[0], "after", added[key][0]));
  std::sort(hints.begin(), hints.end(), [](const Json &a, const Json &b) {
    return std::pair(a["before"], a["after"]) <
           std::pair(b["before"], b["after"]);
  });
  updated["previous_parts"] = current;
  updated["netlist_sha256"] = hex(digest(rendered.netlist, EVP_sha256()));
  auto report = object(
      "schema_version", 1, "backend", "kicad", "warnings", result.warnings,
      "erc_performed", true, "erc_errors", erc.report["errors"], "erc_warnings",
      erc.report["warnings"], "erc_pending", erc.report["pending"],
      "erc_unchecked_nets", erc.report["unchecked_nets"], "erc_configurations",
      erc.report["configurations"], "omitted_without_footprints", omitted,
      "changes", changes, "probable_renames", hints, "footprint_records_sha256",
      hex(digest(records_text, EVP_sha256())));
  auto table = Json::array();
  std::map<std::string, std::string> exported;
  for (const auto &[name, entry] : rendered.names.items())
    exported[entry["source_name"]] = name;
  std::string nets_text, changes_text;
  for (const auto &net : data["nets"]) {
    std::string name = net["name"];
    table.push_back(object("source", name, "exported", exported.at(name),
                           "endpoints", net["endpoints"]));
    nets_text += name + " -> " + exported.at(name) + ": ";
    bool first = true;
    for (const auto &endpoint : net["endpoints"]) {
      if (!first)
        nets_text += ", ";
      first = false;
      bool head = true;
      for (const auto &part : endpoint["instance"]) {
        if (!head)
          nets_text += ".";
        head = false;
        nets_text += part.is_string() ? part.get<std::string>() : part.dump();
      }
      nets_text += ":" + endpoint["pin"].get<std::string>();
    }
    nets_text += "\n";
  }
  if (nets_text.empty())
    nets_text = "\n";
  for (const auto &id : changes["added"])
    changes_text += "Added " + id.get<std::string>() + "\n";
  for (const auto &id : changes["removed"])
    changes_text += "Removed " + id.get<std::string>() + "\n";
  for (const auto &change : changes["changed"]) {
    changes_text += "Changed " + change["identity"].get<std::string>() + "\n";
    const auto &before = change["before"], &after = change["after"];
    for (const auto &key : {"path", "reference", "value", "footprint"})
      if (before.value(key, Json()) != after.value(key, Json()))
        changes_text += std::string("  ") + key + ": " +
                        display(before.value(key, Json())) + " -> " +
                        display(after.value(key, Json())) + "\n";
    std::set<std::string> pins;
    for (const auto &[pin, data] : before["pins"].items())
      pins.insert(pin);
    for (const auto &[pin, data] : after["pins"].items())
      pins.insert(pin);
    for (const auto &pin : pins) {
      auto a = before["pins"].contains(pin) ? display(before["pins"][pin])
                                            : "absent";
      auto b =
          after["pins"].contains(pin) ? display(after["pins"][pin]) : "absent";
      if (a != b)
        changes_text += "  pin " + pin + ": " + a + " -> " + b + "\n";
    }
  }
  for (const auto &hint : hints)
    changes_text += "Possible rename " + hint["before"].get<std::string>() +
                    " -> " + hint["after"].get<std::string>() +
                    " (same footprint, value, and connections; identity was "
                    "not preserved)\n";
  if (changes_text.empty())
    changes_text = "\n";
  auto out = project.config.output_dir;
  auto add = [&](const fs::path &path, const std::string &text) {
    result.files.push_back(Json::array({path.string(), text}));
  };
  add(out / "board.net", rendered.netlist);
  add(out / "erc.json", erc.json_report);
  add(out / "erc.txt", erc.text_report);
  for (const auto &[name, value] : std::vector<std::pair<std::string, Json>>{
           {"names.json", rendered.names},
           {"netclasses.json", rendered.classes},
           {"export.json", report},
           {"nets.json", table}})
    add(out / name, value.dump(2, ' ', true) + "\n");
  add(out / "nets.txt", nets_text);
  add(out / "changes.txt", changes_text);
  add(state_path, updated.dump(2, ' ', true) + "\n");
  std::set<fs::path> protected_paths{fs::weakly_canonical(project.config.path),
                                     fs::weakly_canonical(records)},
      outputs;
  for (const auto &[name, module] : project.modules)
    protected_paths.insert(fs::weakly_canonical(module.path));
  for (const auto &file : result.files) {
    auto path = fs::weakly_canonical(fs::path(file[0].get<std::string>()));
    if (protected_paths.contains(path))
      fail(project.config.path, "export.overwrite",
           "export paths must not overwrite project inputs");
    if (!outputs.insert(path).second)
      fail(project.config.path, "export.overwrite",
           "export output paths overlap");
  }
  result.summary = report;
  result.summary["output"] = (out / "board.net").string();
  result.summary["state"] = state_path.string();
  return result;
}
} // namespace noscheme
