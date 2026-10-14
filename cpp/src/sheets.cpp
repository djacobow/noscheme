// Hierarchical KiCad schematics: the native port of noscheme/kicad/sheets.py.
// Output must match the Python exporter byte for byte; the parity tests
// compare them. See that module for the design.
#include "noscheme/kicad.hpp"
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <functional>
#include <map>
#include <memory>
#include <regex>
#include <stdexcept>

namespace noscheme::kicad {
namespace {

const char *const VERSION = "20250114";
const char *const LIB_VERSION = "20241209";
const char *const LIBRARY = "noscheme.kicad_sym";
const double FONT = 1.27, CHAR = 1.05, PITCH = 2.54, PIN = 2.54, MARGIN = 25.4,
             TITLE_BLOCK = 50.8, GAP = 7.62, ROW_WIDTH = 360.0;

double label_extra(const std::string &how) {
  return how == "label" ? 1.0 : how == "hierarchical_label" ? 3.0 : 4.0;
}

double up(double value, double step = PITCH) {
  return std::ceil(value / step - 1e-9) * step;
}

std::string mm(double value) {
  char buffer[64];
  std::snprintf(buffer, sizeof buffer, "%.4f", value);
  std::string text = buffer;
  while (!text.empty() && text.back() == '0')
    text.pop_back();
  if (!text.empty() && text.back() == '.')
    text.pop_back();
  return text == "-0" || text.empty() ? "0" : text;
}

// Python len(): code points, not bytes.
double length(const std::string &text) {
  return static_cast<double>(
      std::count_if(text.begin(), text.end(), [](char c) {
        return (static_cast<unsigned char>(c) & 0xC0) != 0x80;
      }));
}

std::string q(const std::string &text) {
  std::string out = "\"";
  for (char c : text) {
    if (c == '\\')
      out += "\\\\";
    else if (c == '"')
      out += "\\\"";
    else if (c == '\n')
      out += "\\n";
    else
      out += c;
  }
  return out + "\"";
}

// An S-expression: an atom (keyword, number, or quoted string) or a form.
struct Sx {
  std::string atom;
  std::vector<Sx> items; // a form: items[0] is the head atom
  bool form = false;
};

Sx atom(std::string text) { return Sx{std::move(text), {}, false}; }

Sx node(const std::string &name, std::vector<Sx> items = {}) {
  Sx out{"", {atom(name)}, true};
  for (auto &item : items)
    out.items.push_back(std::move(item));
  return out;
}

void add(Sx &form, Sx item) { form.items.push_back(std::move(item)); }

std::string write(const Sx &value, int indent = 0) {
  if (!value.form)
    return value.atom;
  std::string text = "(" + value.items[0].atom;
  std::vector<const Sx *> nested;
  for (size_t i = 1; i < value.items.size(); ++i) {
    if (value.items[i].form)
      nested.push_back(&value.items[i]);
    else
      text += " " + value.items[i].atom;
  }
  if (nested.empty())
    return text + ")";
  std::string pad(indent + 1, '\t');
  for (const auto *item : nested)
    text += "\n" + pad + write(*item, indent + 1);
  return text + "\n" + std::string(indent, '\t') + ")";
}

Sx at(double x, double y) { return node("at", {atom(mm(x)), atom(mm(y))}); }
Sx at(double x, double y, double angle) {
  return node("at", {atom(mm(x)), atom(mm(y)), atom(mm(angle))});
}

Sx effects(const std::string &justify = "", bool hide = false) {
  auto out =
      node("effects",
           {node("font", {node("size", {atom(mm(FONT)), atom(mm(FONT))})})});
  if (!justify.empty()) {
    std::vector<Sx> words;
    size_t start = 0;
    while (start <= justify.size()) {
      auto end = justify.find(' ', start);
      if (end == std::string::npos)
        end = justify.size();
      words.push_back(atom(justify.substr(start, end - start)));
      start = end + 1;
    }
    add(out, node("justify", std::move(words)));
  }
  if (hide)
    add(out, node("hide", {atom("yes")}));
  return out;
}

Sx prop(const std::string &name, const std::string &value, double x, double y,
        bool hide = false, const std::string &justify = "") {
  return node("property", {atom(q(name)), atom(q(value)), at(x, y, 0),
                           effects(justify, hide)});
}

// Python's natural(): digit runs compare as numbers, before other text.
bool natural_less(const std::string &a, const std::string &b) {
  static const std::regex token("\\d+|\\D+");
  auto split = [](const std::string &text) {
    std::vector<std::tuple<int, long long, std::string>> out;
    for (std::sregex_iterator it(text.begin(), text.end(), token), end;
         it != end; ++it) {
      auto t = it->str();
      if (std::isdigit(static_cast<unsigned char>(t[0])))
        out.emplace_back(0, std::stoll(t), "");
      else
        out.emplace_back(1, 0, t);
    }
    return out;
  };
  return split(a) < split(b);
}

std::string show_path(const Json &path) {
  std::string out;
  for (const auto &step : path) {
    if (step.is_number_integer())
      out += "[" + step.dump() + "]";
    else
      out += (out.empty() ? "" : ".") + step.get<std::string>();
  }
  return out;
}

Json relative(const Json &path, const Json &prefix) {
  Json out = Json::array();
  for (size_t i = prefix.size(); i < path.size(); ++i)
    out.push_back(path[i]);
  return out;
}

// Strip trailing array indices: [display, digits, 3] -> ([display, digits],
// [3]).
std::pair<Json, Json> instance_name(const Json &path) {
  size_t end = path.size();
  while (end && path[end - 1].is_number_integer())
    --end;
  Json name = Json::array(), index = Json::array();
  for (size_t i = 0; i < path.size(); ++i)
    (i < end ? name : index).push_back(path[i]);
  return {name, index};
}

struct Order {
  std::string filename;
  long long start = 0;
  std::vector<long long> index;
  bool operator<(const Order &other) const {
    return std::tie(filename, start, index) <
           std::tie(other.filename, other.start, other.index);
  }
};

// hierarchy.declaration_order: source order, then array index order.
Order declaration_order(const Json &item, const Json &path) {
  Order order;
  if (item.contains("source") && item["source"].contains("declaration") &&
      item["source"]["declaration"].is_object()) {
    const auto &span = item["source"]["declaration"];
    order.filename = span.value("filename", std::string());
    order.start = span.value("start_pos", 0LL);
  }
  for (const auto &i : instance_name(path).second)
    order.index.push_back(i.get<long long>());
  return order;
}

struct Node {
  Json path, ports;
  std::string definition;
  Order order;
  std::vector<Node *> children;
  std::vector<const Json *> parts;
};

std::string part_text(const Json &part) {
  const auto &value = part.value("value", Json());
  if (value.is_object() && !value.value("value", Json()).is_null())
    return value["value"].dump();
  std::string kind = part["definition"];
  return kind.substr(kind.rfind('.') + 1);
}

// --------------------------------------------------------------------------
// Symbols

struct Box {
  std::string definition;
  std::vector<std::string> keys; // natural order
  std::map<std::string, std::string> names;
  struct Pin {
    std::string key;
    double x, y;
    char side;
  };
  std::vector<Pin> pins;
  bool small = false;
  double half = PITCH, top = 1.27, bottom = -1.27;

  explicit Box(const Json &part) : definition(part["definition"]) {
    for (const auto &[key, pin] : part["pins"].items())
      keys.push_back(key);
    std::stable_sort(keys.begin(), keys.end(), natural_less);
    for (const auto &key : keys) {
      auto aliases = attribute(part["pins"][key], "aliases", Json::array());
      names[key] = aliases.empty() ? key : aliases[0].get<std::string>();
    }
    small = keys.size() <= 2;
    if (small) {
      half = PITCH;
      const char sides[] = "LR";
      for (size_t i = 0; i < keys.size(); ++i)
        pins.push_back({keys[i], (sides[i] == 'L' ? -1 : 1) * (half + PIN), 0.0,
                        sides[i]});
      top = 1.27, bottom = -1.27;
      return;
    }
    size_t left = (keys.size() + 1) / 2;
    double widest = 0;
    for (const auto &key : keys)
      widest = std::max(widest, length(names[key]));
    half = std::max(up((widest * 2 * CHAR + 2 * PITCH) / 2), 2 * PITCH);
    for (size_t i = 0; i < keys.size(); ++i) {
      bool is_left = i < left;
      size_t row = is_left ? i : i - left;
      double x = (is_left ? -1 : 1) * (half + PIN);
      pins.push_back(
          {keys[i], x, -static_cast<double>(row) * PITCH, is_left ? 'L' : 'R'});
    }
    top = PITCH, bottom = -static_cast<double>(left) * PITCH;
  }

  Sx lib_symbol(const std::map<std::string, std::string> &kinds,
                const std::string &prefix, const std::string &lib) const {
    const auto &name = definition;
    auto body =
        node("rectangle", {node("start", {atom(mm(-half)), atom(mm(top))}),
                           node("end", {atom(mm(half)), atom(mm(bottom))}),
                           node("stroke", {node("width", {atom("0.254")}),
                                           node("type", {atom("default")})}),
                           node("fill", {node("type", {atom("background")})})});
    std::vector<Sx> pin_forms;
    for (const auto &pin : pins)
      pin_forms.push_back(
          node("pin", {atom(kinds.at(pin.key)), atom("line"),
                       at(pin.x, pin.y, pin.side == 'L' ? 0 : 180),
                       node("length", {atom(mm(PIN))}),
                       node("name", {atom(q(names.at(pin.key))), effects()}),
                       node("number", {atom(q(pin.key)), effects()})}));
    auto out = node("symbol", {atom(q(lib + name))});
    if (small)
      add(out, node("pin_numbers", {node("hide", {atom("yes")})}));
    auto pin_names = node("pin_names", {node("offset", {atom("1.016")})});
    if (small)
      add(pin_names, node("hide", {atom("yes")}));
    add(out, pin_names);
    add(out, node("exclude_from_sim", {atom("no")}));
    add(out, node("in_bom", {atom("yes")}));
    add(out, node("on_board", {atom("yes")}));
    add(out, prop("Reference", prefix, 0, top + 1.27, false, "left"));
    add(out, prop("Value", name.substr(name.rfind('.') + 1), 0, bottom - 1.27,
                  false, "left"));
    add(out, prop("Footprint", "", 0, 0, true));
    add(out, prop("Datasheet", "", 0, 0, true));
    add(out, prop("Description", "NoScheme " + name, 0, 0, true));
    add(out, node("symbol", {atom(q(name + "_0_1")), body}));
    auto unit = node("symbol", {atom(q(name + "_1_1"))});
    for (auto &pin : pin_forms)
      add(unit, std::move(pin));
    add(out, unit);
    return out;
  }
};

// KiCad's default pin-conflict matrix: pairs it reports as an error or warning.
bool conflicts(const std::string &a, const std::string &b) {
  static const std::set<std::pair<std::string, std::string>> pairs{
      {"output", "output"},
      {"output", "tri_state"},
      {"output", "power_out"},
      {"output", "open_collector"},
      {"output", "open_emitter"},
      {"bidirectional", "power_out"},
      {"bidirectional", "open_emitter"},
      {"tri_state", "power_in"},
      {"tri_state", "power_out"},
      {"tri_state", "open_collector"},
      {"tri_state", "open_emitter"},
      {"power_out", "power_out"},
      {"power_out", "open_collector"},
      {"power_out", "open_emitter"}};
  return pairs.contains({a, b}) || pairs.contains({b, a});
}

int demote_rank(const std::string &kind) {
  static const std::vector<std::string> order{"output",         "tri_state",
                                              "open_collector", "open_emitter",
                                              "bidirectional",  "power_out"};
  auto it = std::find(order.begin(), order.end(), kind);
  return it == order.end() ? 99 : static_cast<int>(it - order.begin());
}

Sx power_flag(const std::string &name) {
  auto pin = node("pin", {atom("power_out"), atom("line"), at(0, 0, 90),
                          node("length", {atom("0")}),
                          node("name", {atom(q("pwr")), effects()}),
                          node("number", {atom(q("1")), effects()})});
  std::vector<Sx> points;
  for (auto [x, y] : std::vector<std::pair<double, double>>{{0, 0},
                                                            {0, 1.27},
                                                            {-1.016, 1.905},
                                                            {0, 2.54},
                                                            {1.016, 1.905},
                                                            {0, 1.27}})
    points.push_back(node("xy", {atom(mm(x)), atom(mm(y))}));
  auto flag =
      node("polyline", {node("pts", std::move(points)),
                        node("stroke", {node("width", {atom("0")}),
                                        node("type", {atom("default")})}),
                        node("fill", {node("type", {atom("none")})})});
  return node("symbol",
              {atom(q(name)), node("power"),
               node("pin_numbers", {node("hide", {atom("yes")})}),
               node("pin_names",
                    {node("offset", {atom("0")}), node("hide", {atom("yes")})}),
               node("exclude_from_sim", {atom("no")}),
               node("in_bom", {atom("yes")}), node("on_board", {atom("yes")}),
               prop("Reference", "#FLG", 0, 1.905, true),
               prop("Value", "PWR_FLAG", 0, 3.81),
               prop("Footprint", "", 0, 0, true),
               prop("Datasheet", "", 0, 0, true),
               prop("Description",
                    "Special symbol for telling ERC where power comes from", 0,
                    0, true),
               node("symbol", {atom(q("PWR_FLAG_0_0")), pin}),
               node("symbol", {atom(q("PWR_FLAG_0_1")), flag})});
}

// --------------------------------------------------------------------------
// Sheets

class Sheets {
public:
  Sheets(const Json &circuit, const Json &state, std::string project)
      : circuit_(circuit), state_(state), project_(std::move(project)),
        namespace_(state["namespace"]) {
    for (const auto &block : circuit["blocks"])
      blocks_.insert(block["path"]);
    std::set<std::string> rails;
    for (const auto &net : circuit["nets"]) {
      std::string name = net["name"];
      exported_[name] = net_name(net["path"], blocks_);
      paths_[name] = net["path"];
      for (const auto &d : net["declarations"]) {
        auto type = d.value("type", Json());
        if (type == "power" || type == "ground")
          rails.insert(name);
      }
      std::set<std::string> kinds;
      for (const auto &d : net["declarations"])
        if (auto c = attribute(d, "net_class"); !c.is_null())
          kinds.insert(c.get<std::string>());
      if (!kinds.empty())
        classes_[name] = *kinds.begin();
    }
    // Rails with a bare root name are global labels on every sheet.
    for (const auto &name : rails)
      if (!exported_[name].starts_with("/"))
        rail_.insert(name);
    design_rail_ = rails;
    build_tree();
    symbols();
  }

  std::vector<std::pair<std::string, std::string>>
  render(const std::string &stem) {
    auto root_uuid = uid(Json::array({"root"}));
    walk(root_, "/" + root_uuid, stem);
    decorate();
    std::vector<std::pair<std::string, std::string>> out;
    for (const auto &key : order_)
      out.emplace_back(files_.at(key), sheet(key, root_uuid));
    out.emplace_back(LIBRARY, library());
    return out;
  }

private:
  using Anchor = Json; // [kind, rel, key]
  struct Instance {
    Node *node;
    std::string path;
  };

  const Json &circuit_;
  const Json &state_;
  std::string project_, namespace_;
  std::set<Json> blocks_;
  std::map<std::string, std::string> exported_, classes_;
  std::map<std::string, Json> paths_;
  std::set<std::string> rail_, design_rail_;
  std::map<Json, std::unique_ptr<Node>> nodes_;
  Node *root_ = nullptr;
  std::vector<const Json *> parts_;
  std::map<std::string, std::unique_ptr<Box>> boxes_;
  std::map<std::string, std::map<std::string, std::string>> kinds_;
  std::set<std::string> needs_flag_, single_;
  std::map<Json, std::string> keys_, signatures_;
  std::map<std::string, std::string> files_;
  std::vector<std::string> order_; // sheet keys in first-seen order
  std::map<std::string, std::vector<Instance>> instances_;
  std::map<std::string, int> pages_;
  std::map<std::string, std::vector<Anchor>> anchor_ids_;
  std::map<std::string, std::set<std::string>> used_;
  std::map<std::pair<std::string, Json>, std::set<Json>> plan_;
  int flags_ = 0;

  std::string uid(const Json &parts) const {
    return uuid5(namespace_, "kicad-schematic:" + parts.dump(-1, ' ', false));
  }

  static bool fitted(const Json &part) {
    auto fp = attribute(part, "footprint");
    return fp.is_string() && !fp.get<std::string>().empty();
  }

  void build_tree() {
    for (const auto &block : circuit_["blocks"]) {
      auto node = std::make_unique<Node>();
      node->path = block["path"];
      node->ports = block["ports"];
      node->definition = block["definition"];
      node->order = declaration_order(block, block["path"]);
      nodes_[block["path"]] = std::move(node);
    }
    for (const auto &block : circuit_["blocks"]) {
      const Json &path = block["path"];
      if (path.empty())
        continue;
      auto name = instance_name(path).first;
      name.erase(name.size() - 1);
      nodes_.at(name)->children.push_back(nodes_.at(path).get());
    }
    for (const auto &part : circuit_["parts"]) {
      auto name = instance_name(part["path"]).first;
      name.erase(name.size() - 1);
      nodes_.at(name)->parts.push_back(&part);
    }
    for (auto &[path, node] : nodes_) {
      std::stable_sort(node->children.begin(), node->children.end(),
                       [](Node *a, Node *b) { return a->order < b->order; });
      std::stable_sort(node->parts.begin(), node->parts.end(),
                       [](const Json *a, const Json *b) {
                         return declaration_order(*a, (*a)["path"]) <
                                declaration_order(*b, (*b)["path"]);
                       });
    }
    root_ = nodes_.at(Json::array()).get();
  }

  void symbols() {
    for (const auto &part : circuit_["parts"])
      if (fitted(part))
        parts_.push_back(&part);
    std::stable_sort(parts_.begin(), parts_.end(),
                     [](const Json *a, const Json *b) {
                       return (*a)["identity"].get<std::string>() <
                              (*b)["identity"].get<std::string>();
                     });
    for (const auto *part : parts_) {
      std::string definition = (*part)["definition"];
      if (boxes_.contains(definition))
        continue;
      boxes_[definition] = std::make_unique<Box>(*part);
      auto &kinds = kinds_[definition];
      for (const auto &[key, pin] : (*part)["pins"].items()) {
        std::set<std::string> found;
        for (const auto *p : parts_)
          if ((*p)["definition"] == definition)
            found.insert(pin_kind((*p)["pins"][key]));
        auto kind = found.size() == 1 ? *found.begin() : "unspecified";
        kinds[key] = kind == "unspecified" ? "passive" : kind;
      }
    }
    std::map<std::string, std::vector<std::pair<std::string, std::string>>>
        by_net;
    for (const auto *part : parts_)
      for (const auto &[key, pin] : (*part)["pins"].items())
        if (!pin["net"].is_null())
          by_net[pin["net"]].emplace_back((*part)["definition"], key);
    for (bool changed = true; changed;) {
      changed = false;
      for (const auto &[net, pins] : by_net) {
        bool found = false;
        for (size_t i = 0; i < pins.size() && !found; ++i)
          for (size_t j = i + 1; j < pins.size() && !found; ++j) {
            const auto &a = kinds_[pins[i].first][pins[i].second];
            const auto &b = kinds_[pins[j].first][pins[j].second];
            if (!conflicts(a, b))
              continue;
            const auto &pick =
                demote_rank(a) <= demote_rank(b) ? pins[i] : pins[j];
            kinds_[pick.first][pick.second] = "passive";
            found = changed = true;
          }
      }
    }
    for (const auto &[net, pins] : by_net) {
      std::set<std::string> kinds;
      for (const auto &[d, k] : pins)
        kinds.insert(kinds_[d][k]);
      if (kinds.contains("power_in") && !kinds.contains("power_out"))
        needs_flag_.insert(net);
      if (pins.size() == 1)
        single_.insert(net);
    }
  }

  std::string prefix(const std::string &definition) const {
    for (const auto *part : parts_)
      if ((*part)["definition"] == definition)
        return attribute(*part, "ref_prefix", "U").get<std::string>();
    return "U";
  }

  std::vector<Sx> lib_symbols(const std::set<std::string> &definitions,
                              const std::string &lib) const {
    std::vector<Sx> out;
    for (const auto &d : definitions)
      out.push_back(boxes_.at(d)->lib_symbol(kinds_.at(d), prefix(d), lib));
    out.push_back(power_flag(lib + "PWR_FLAG"));
    return out;
  }

  std::string library() const {
    std::set<std::string> all;
    for (const auto &[d, box] : boxes_)
      all.insert(d);
    auto lib =
        node("kicad_symbol_lib", {node("version", {atom(LIB_VERSION)}),
                                  node("generator", {atom(q("noscheme"))}),
                                  node("generator_version", {atom(q("0.1"))})});
    for (auto &symbol : lib_symbols(all, ""))
      add(lib, std::move(symbol));
    return write(lib) + "\n";
  }

  // -- sheet files and instances ----------------------------------------------

  std::string token_signature(const Node &node) {
    // Equal signatures draw identically: same parts, values, and topology.
    if (auto it = signatures_.find(node.path); it != signatures_.end())
      return it->second;
    std::map<std::string, Json> tokens;
    for (const auto &[port, entries] : node.ports.items())
      for (size_t i = 0; i < entries.size(); ++i)
        if (!entries[i]["net"].is_null() &&
            !tokens.contains(entries[i]["net"].get<std::string>()))
          tokens[entries[i]["net"]] = Json::array({"port", port, i});
    auto token = [&](const Json &net) -> Json {
      if (net.is_null())
        return nullptr;
      std::string name = net;
      if (design_rail_.contains(name))
        return Json::array({"rail", name});
      if (!tokens.contains(name))
        tokens[name] = Json::array({"local", tokens.size()});
      return tokens[name];
    };
    Json parts = Json::array();
    for (const auto *part : node.parts) {
      Json pins = Json::array();
      for (const auto &[key, pin] : (*part)["pins"].items())
        pins.push_back(Json::array({key, token(pin["net"]), pin["binding"]}));
      parts.push_back(
          Json::array({relative((*part)["path"], node.path),
                       (*part)["definition"], part_text(*part), pins}));
    }
    Json children = Json::array();
    for (const auto *child : node.children) {
      Json ports = Json::array();
      for (const auto &[port, entries] : child->ports.items()) {
        Json nets = Json::array();
        for (const auto &e : entries)
          nets.push_back(token(e["net"]));
        ports.push_back(Json::array({port, nets}));
      }
      children.push_back(Json::array(
          {relative(child->path, node.path), token_signature(*child), ports}));
    }
    Json own = Json::array();
    for (const auto &[port, entries] : node.ports.items()) {
      Json nets = Json::array();
      for (const auto &e : entries)
        nets.push_back(token(e["net"]));
      own.push_back(Json::array({port, nets}));
    }
    auto text = Json::array({node.definition, own, parts, children}).dump();
    signatures_[node.path] = text;
    return text;
  }

  // Equal keys can share a sheet file: same structure and same fields.
  const std::string &key(const Node &node) {
    if (auto it = keys_.find(node.path); it != keys_.end())
      return it->second;
    Json own = Json::array();
    for (const auto *part : node.parts) {
      Json bom = Json::array();
      for (const auto &[k, v] : attribute(*part, "bom", Json::object()).items())
        bom.push_back(Json::array({k, v.value("value", Json()).dump()}));
      own.push_back(Json::array({relative((*part)["path"], node.path),
                                 attribute(*part, "footprint"), bom}));
    }
    Json children = Json::array();
    for (const auto *child : node.children)
      children.push_back(
          Json::array({relative(child->path, node.path), key(*child)}));
    auto text = node.definition + "#" +
                Json::array({token_signature(node), own, children}).dump();
    return keys_[node.path] = text;
  }

  std::string file_for(const Node &node, const std::string &stem) {
    const auto &k = key(node);
    if (!files_.contains(k)) {
      std::string base = node.path.empty() ? stem : node.definition;
      if (!node.path.empty())
        std::replace(base.begin(), base.end(), '.', '_');
      auto taken = [&](const std::string &name) {
        return std::any_of(files_.begin(), files_.end(),
                           [&](const auto &f) { return f.second == name; });
      };
      std::string name = base + ".kicad_sch";
      for (int n = 2; taken(name); ++n)
        name = base + "_" + std::to_string(n) + ".kicad_sch";
      files_[k] = name;
      order_.push_back(k);
    }
    return files_[k];
  }

  void walk(Node *node, const std::string &path, const std::string &stem) {
    auto file = file_for(*node, stem);
    instances_[key(*node)].push_back({node, path});
    pages_[path] = static_cast<int>(pages_.size()) + 1;
    for (auto *child : node->children)
      walk(child,
           path + "/" +
               uid(Json::array({file, relative(child->path, node->path)})),
           stem);
  }

  // -- labels
  // -------------------------------------------------------------------

  std::map<std::string, std::string> ports(const Node &node) const {
    std::map<std::string, std::string> out;
    for (const auto &[port, entries] : node.ports.items())
      for (const auto &entry : entries) {
        if (entry["net"].is_null())
          continue;
        std::string net = entry["net"];
        if (!rail_.contains(net) && !out.contains(net))
          out[net] = show_path(relative(entry["path"], node.path));
      }
    return out;
  }

  std::pair<std::string, std::string>
  label(const std::string &net, const Node &node,
        const std::map<std::string, std::string> &ports) const {
    const auto &exported = exported_.at(net);
    if (rail_.contains(net))
      return {"global_label", exported};
    if (auto it = ports.find(net); it != ports.end())
      return {"hierarchical_label", it->second};
    const auto &path = paths_.at(net);
    size_t cut = 0;
    for (size_t n = 0; n < path.size(); ++n) {
      Json prefix = Json::array();
      for (size_t i = 0; i < n; ++i)
        prefix.push_back(path[i]);
      if (blocks_.contains(prefix))
        cut = n;
    }
    Json block = Json::array();
    for (size_t i = 0; i < cut; ++i)
      block.push_back(path[i]);
    if (block != node.path)
      throw std::invalid_argument("net '" + net +
                                  "' is neither local to nor a port of " +
                                  show_path(node.path));
    if (node.path.empty() && !exported.starts_with("/"))
      return {"global_label", exported};
    return {"label", joined(segments(path, cut, path.size()), ".")};
  }

  struct Member {
    const Json *part = nullptr;
    Node *block = nullptr;
  };

  std::vector<Member> members(const Node &node) const {
    std::vector<std::pair<Member, Order>> entries;
    for (const auto *part : node.parts)
      if (fitted(*part))
        entries.push_back(
            {{part, nullptr}, declaration_order(*part, (*part)["path"])});
    for (auto *child : node.children)
      entries.push_back({{nullptr, child}, child->order});
    std::stable_sort(
        entries.begin(), entries.end(),
        [](const auto &a, const auto &b) { return a.second < b.second; });
    std::vector<Member> out;
    for (const auto &[member, order] : entries)
      out.push_back(member);
    return out;
  }

  std::vector<std::pair<Anchor, Json>> anchors(Node &node) {
    std::vector<std::pair<Anchor, Json>> out;
    for (const auto &anchor : sheet_anchors(node))
      out.emplace_back(anchor, anchor_net(node, anchor));
    return out;
  }

  const std::vector<Anchor> &sheet_anchors(const Node &node) {
    const auto &k = key(node);
    if (auto it = anchor_ids_.find(k); it != anchor_ids_.end())
      return it->second;
    const auto &instances = instances_.at(k);
    Node &first = *instances[0].node;
    std::vector<Anchor> ids;
    for (const auto &member : members(first)) {
      if (member.part) {
        auto rel = relative((*member.part)["path"], first.path);
        for (const auto &pin : boxes_.at((*member.part)["definition"])->pins) {
          Anchor anchor = Json::array({"pin", rel, pin.key});
          bool open = true;
          for (const auto &instance : instances) {
            auto net = anchor_net(*instance.node, anchor);
            if (!net.is_null() && !single_.contains(net.get<std::string>()))
              open = false;
          }
          if (!open)
            ids.push_back(anchor);
        }
      } else {
        auto rel = relative(member.block->path, first.path);
        for (const auto &[field, net] : sheet_pins(*member.block))
          ids.push_back(Json::array({"sheet", rel, field}));
      }
    }
    return anchor_ids_[k] = ids;
  }

  const std::set<std::string> &used(const Node &node) {
    const auto &k = key(node);
    if (auto it = used_.find(k); it != used_.end())
      return it->second;
    Node &first = *instances_.at(k)[0].node;
    auto port_map = ports(first);
    std::set<std::string> out;
    for (const auto &[anchor, net] : anchors(first))
      if (!net.is_null() &&
          label(net, first, port_map).first == "hierarchical_label")
        out.insert(port_map.at(net));
    return used_[k] = out;
  }

  std::vector<std::pair<std::string, std::string>>
  sheet_pins(const Node &child) {
    const auto &fields = used(child);
    std::vector<std::pair<std::string, std::string>> pins;
    for (const auto &[port, entries] : child.ports.items())
      for (const auto &entry : entries) {
        if (entry["net"].is_null())
          continue;
        std::string net = entry["net"];
        auto field = show_path(relative(entry["path"], child.path));
        if (!rail_.contains(net) && fields.contains(field))
          pins.emplace_back(field, net);
      }
    return pins;
  }

  Json anchor_net(const Node &node, const Anchor &anchor) {
    const auto &rel = anchor[1];
    std::string key_ = anchor[2];
    if (anchor[0] == "pin") {
      for (const auto *part : node.parts)
        if (relative((*part)["path"], node.path) == rel)
          return (*part)["pins"][key_]["net"];
      throw std::logic_error("missing part");
    }
    for (auto *child : node.children)
      if (relative(child->path, node.path) == rel) {
        for (const auto &[field, net] : sheet_pins(*child))
          if (field == key_)
            return net;
        return nullptr;
      }
    throw std::logic_error("missing block");
  }

  void decorate() {
    std::set<Json> flagged, classed;
    auto order = order_;
    std::stable_sort(order.begin(), order.end(),
                     [&](const auto &a, const auto &b) {
                       return instances_.at(a).size() < instances_.at(b).size();
                     });
    for (const auto &k : order) {
      const auto &instances = instances_.at(k);
      for (const auto &[anchor, net0] : anchors(*instances[0].node)) {
        std::vector<Json> nets;
        for (const auto &instance : instances)
          nets.push_back(anchor_net(*instance.node, anchor));
        auto any_in = [&](const std::set<Json> &done) {
          return std::any_of(nets.begin(), nets.end(),
                             [&](const Json &n) { return done.contains(n); });
        };
        std::set<Json> marks;
        bool all_flag =
            std::all_of(nets.begin(), nets.end(), [&](const Json &n) {
              return n.is_string() &&
                     needs_flag_.contains(n.get<std::string>());
            });
        if (all_flag && !any_in(flagged)) {
          marks.insert(Json::array({"flag"}));
          flagged.insert(nets.begin(), nets.end());
        }
        auto class_of = [&](const Json &n) -> std::string {
          if (!n.is_string())
            return "";
          auto it = classes_.find(n.get<std::string>());
          return it == classes_.end() ? "" : it->second;
        };
        auto kind = class_of(nets[0]);
        bool same = !kind.empty() &&
                    std::all_of(nets.begin(), nets.end(), [&](const Json &n) {
                      return class_of(n) == kind;
                    });
        if (same && !any_in(classed)) {
          marks.insert(Json::array({"class", kind}));
          classed.insert(nets.begin(), nets.end());
        }
        if (!marks.empty())
          plan_[{k, anchor}] = marks;
      }
    }
  }

  // -- rendering
  // ------------------------------------------------------------------

  std::string sheet(const std::string &k, const std::string &root_uuid) {
    const auto &file = files_.at(k);
    Node &first = *instances_.at(k)[0].node;
    auto port_map = ports(first);
    struct Item {
      Member member;
      double w, h, left, right;
    };
    std::vector<Item> items;
    for (const auto &member : members(first)) {
      if (member.part) {
        const auto &box = *boxes_.at((*member.part)["definition"]);
        double ext_l = 0.0, ext_r = 0.0;
        for (const auto &pin : box.pins) {
          const auto &net = (*member.part)["pins"][pin.key]["net"];
          if (net.is_null())
            continue;
          auto [how, text] = label(net, first, port_map);
          double e = length(text) * CHAR + label_extra(how) + 1;
          (pin.side == 'L' ? ext_l : ext_r) =
              std::max(pin.side == 'L' ? ext_l : ext_r, e);
        }
        items.push_back({member, 2 * (box.half + PIN), box.top - box.bottom,
                         up(ext_l), up(ext_r)});
      } else {
        auto pins = sheet_pins(*member.block);
        double widest =
            length(show_path(relative(member.block->path, first.path))) * CHAR +
            PITCH;
        for (const auto &[p, net] : pins)
          widest = std::max(widest, length(p) * CHAR + 2 * PITCH);
        widest = std::max(widest, length(files_.at(key(*member.block))) * CHAR);
        double ext = 0.0;
        for (const auto &[p, net] : pins) {
          auto [how, text] = label(net, first, port_map);
          ext = std::max(ext, length(text) * CHAR + label_extra(how) + 1);
        }
        items.push_back({member, up(widest),
                         static_cast<double>(pins.size() + 1) * PITCH,
                         up(ext) + PITCH, 0.0});
      }
    }
    std::vector<Sx> body;
    std::set<std::string> used_definitions;
    double x = MARGIN, y = MARGIN, row_h = 0.0, right = MARGIN;
    for (const auto &item : items) {
      double span = item.left + item.w + item.right;
      if (x > MARGIN && x + span > MARGIN + ROW_WIDTH)
        x = MARGIN, y = y + row_h + GAP, row_h = 0.0;
      double ox = x + item.left;
      if (item.member.part) {
        const auto &box = *boxes_.at((*item.member.part)["definition"]);
        double cx = ox + box.half + PIN, cy = y + up(box.top);
        for (auto &sx : symbol(*item.member.part, box, cx, cy, first, port_map,
                               k, file, used_definitions))
          body.push_back(std::move(sx));
      } else {
        for (auto &sx : sheet_symbol(*item.member.block, ox, y + 2 * PITCH,
                                     item.w, first, port_map, k, file))
          body.push_back(std::move(sx));
      }
      x += span + GAP;
      row_h = std::max(row_h, item.h + 3 * PITCH);
      right = std::max(right, x);
    }
    double width = std::max(up(right + MARGIN, 10), 297.0);
    double height = std::max(up(y + row_h + TITLE_BLOCK, 10), 210.0);
    auto doc = node(
        "kicad_sch",
        {node("version", {atom(VERSION)}),
         node("generator", {atom(q("noscheme"))}),
         node("generator_version", {atom(q("0.1"))}),
         node("uuid",
              {atom(q(first.path.empty() ? root_uuid
                                         : uid(Json::array({"file", file}))))}),
         node("paper", {atom(q("User")), atom(mm(width)), atom(mm(height))}),
         node(
             "title_block",
             {node("title", {atom(q(first.path.empty()
                                        ? circuit_["board"].get<std::string>()
                                        : first.definition))}),
              node("comment",
                   {atom("1"),
                    atom(q("Generated by NoScheme; edits are overwritten"))})}),
         node("lib_symbols", lib_symbols(used_definitions, "noscheme:"))});
    for (auto &sx : body)
      add(doc, std::move(sx));
    if (first.path.empty())
      add(doc,
          node("sheet_instances",
               {node("path", {atom(q("/")), node("page", {atom(q("1"))})})}));
    add(doc, node("embedded_fonts", {atom("no")}));
    return write(doc) + "\n";
  }

  std::vector<Sx> decorations(const std::string &k, const Anchor &anchor,
                              double x, double y, const std::string &file) {
    std::vector<Sx> out;
    auto it = plan_.find({k, anchor});
    if (it == plan_.end())
      return out;
    for (const auto &mark : it->second) {
      auto tag = uid(Json::array({file, "mark", anchor, mark}));
      if (mark[0] == "class") {
        out.push_back(node(
            "netclass_flag",
            {atom(q("")), node("length", {atom(mm(PITCH))}),
             node("shape", {atom("round")}), at(x, y, 270),
             node("effects",
                  {node("font", {node("size", {atom("1.27"), atom("1.27")})})}),
             node("uuid", {atom(q(tag))}),
             prop("Netclass", mark[1], x + 0.7, y + PITCH + 1.27, false,
                  "left")}));
        continue;
      }
      std::vector<std::pair<std::string, std::string>> refs;
      for (const auto &instance : instances_.at(k))
        refs.emplace_back(instance.path, "#FLG" + std::to_string(++flags_));
      out.push_back(node(
          "symbol",
          {node("lib_id", {atom(q("noscheme:PWR_FLAG"))}), at(x, y, 0),
           node("unit", {atom("1")}), node("exclude_from_sim", {atom("no")}),
           node("in_bom", {atom("yes")}), node("on_board", {atom("yes")}),
           node("dnp", {atom("no")}), node("uuid", {atom(q(tag))}),
           prop("Reference", refs[0].second, x, y - 1.905, true),
           prop("Value", "PWR_FLAG", x, y - 3.81),
           prop("Footprint", "", x, y, true), prop("Datasheet", "", x, y, true),
           node("pin", {atom(q("1")),
                        node("uuid", {atom(q(uid(Json::array(
                                         {file, "mark-pin", anchor}))))})}),
           instances_form(refs)}));
    }
    return out;
  }

  Sx instances_form(
      const std::vector<std::pair<std::string, std::string>> &refs) const {
    auto project = node("project", {atom(q(project_))});
    for (const auto &[path, ref] : refs)
      add(project,
          node("path", {atom(q(path)), node("reference", {atom(q(ref))}),
                        node("unit", {atom("1")})}));
    return node("instances", {project});
  }

  std::vector<Sx> labels(const std::string &how, const std::string &text,
                         double x, double y, double angle,
                         const std::string &file, const Json &tag) const {
    std::string justify = angle == 0 ? "left" : "right";
    auto uuid = node("uuid", {atom(q(uid(Json::array({file, "label", tag}))))});
    if (how == "label")
      return {node("label", {atom(q(text)), at(x, y, angle),
                             effects(justify + " bottom"), uuid})};
    auto out = node(how, {atom(q(text)), node("shape", {atom("passive")}),
                          at(x, y, angle)});
    if (how == "global_label")
      add(out, node("fields_autoplaced", {atom("yes")}));
    add(out, effects(justify));
    add(out, uuid);
    if (how == "global_label")
      add(out, prop("Intersheetrefs", "${INTERSHEET_REFS}", x, y, true));
    return {out};
  }

  std::vector<Sx> symbol(const Json &part, const Box &box, double cx, double cy,
                         Node &first,
                         const std::map<std::string, std::string> &port_map,
                         const std::string &k, const std::string &file,
                         std::set<std::string> &used_definitions) {
    used_definitions.insert(part["definition"].get<std::string>());
    auto rel = relative(part["path"], first.path);
    std::vector<std::pair<std::string, std::string>> refs;
    for (const auto &instance : instances_.at(k))
      for (const auto *p : instance.node->parts)
        if (relative((*p)["path"], instance.node->path) == rel) {
          refs.emplace_back(
              instance.path,
              state_["entries"][(*p)["identity"].get<std::string>()]
                    ["reference"]
                        .get<std::string>());
          break;
        }
    auto bom = attribute(part, "bom", Json::object());
    bool dnp = bom.contains("dnp") && bom["dnp"].value("value", false) == true;
    auto out = node(
        "symbol",
        {node("lib_id",
              {atom(q("noscheme:" + part["definition"].get<std::string>()))}),
         at(cx, cy, 0), node("unit", {atom("1")}),
         node("exclude_from_sim", {atom("no")}), node("in_bom", {atom("yes")}),
         node("on_board", {atom("yes")}),
         node("dnp", {atom(dnp ? "yes" : "no")}),
         node("uuid", {atom(q(uid(Json::array({file, rel}))))}),
         prop("Reference", refs[0].second, cx, cy - box.top - 1.27, false,
              "left"),
         prop("Value", part_value(part), cx, cy - box.bottom + 1.27, false,
              "left"),
         prop("Footprint", attribute(part, "footprint").get<std::string>(), cx,
              cy, true),
         prop("Datasheet", "", cx, cy, true),
         prop("noscheme_path", show_path(rel), cx, cy, true)});
    for (const auto &[field, title] :
         std::vector<std::pair<std::string, std::string>>{
             {"manufacturer", "Manufacturer"},
             {"mpn", "MPN"},
             {"supplier", "Supplier"},
             {"supplier_part_number", "Supplier Part Number"}})
      if (bom.contains(field)) {
        const auto &value = bom[field]["value"];
        add(out,
            prop(title,
                 value.is_string() ? value.get<std::string>() : value.dump(),
                 cx, cy, true));
      }
    for (const auto &pin : box.pins)
      add(out,
          node("pin", {atom(q(pin.key)),
                       node("uuid", {atom(q(uid(Json::array(
                                        {file, rel, "pin", pin.key}))))})}));
    add(out, instances_form(refs));
    std::vector<Sx> result{out};
    const auto &ids = sheet_anchors(first);
    for (const auto &pin : box.pins) {
      double x = cx + pin.x, y = cy - pin.y;
      Anchor anchor = Json::array({"pin", rel, pin.key});
      if (std::find(ids.begin(), ids.end(), anchor) == ids.end()) {
        // NoScheme's ERC accepted the open pin; tell KiCad's too.
        auto tag = uid(Json::array({file, rel, "nc", pin.key}));
        result.push_back(
            node("no_connect", {at(x, y), node("uuid", {atom(q(tag))})}));
        continue;
      }
      auto [how, text] = label(part["pins"][pin.key]["net"], first, port_map);
      for (auto &sx : labels(how, text, x, y, pin.side == 'L' ? 180 : 0, file,
                             Json::array({rel, pin.key})))
        result.push_back(std::move(sx));
      for (auto &sx : decorations(k, anchor, x, y, file))
        result.push_back(std::move(sx));
    }
    return result;
  }

  std::vector<Sx>
  sheet_symbol(Node &child, double x, double y, double w, Node &first,
               const std::map<std::string, std::string> &port_map,
               const std::string &k, const std::string &file) {
    auto rel = relative(child.path, first.path);
    auto pins = sheet_pins(child);
    double h = static_cast<double>(pins.size() + 1) * PITCH;
    auto child_file = files_.at(key(child));
    auto sheet_uuid = uid(Json::array({file, rel}));
    auto out = node(
        "sheet",
        {at(x, y), node("size", {atom(mm(w)), atom(mm(h))}),
         node("exclude_from_sim", {atom("no")}), node("in_bom", {atom("yes")}),
         node("on_board", {atom("yes")}), node("dnp", {atom("no")}),
         node("fields_autoplaced", {atom("yes")}),
         node("stroke",
              {node("width", {atom("0.1524")}), node("type", {atom("solid")})}),
         node("fill", {node("color", {atom("0"), atom("0"), atom("0"),
                                      atom("0.0000")})}),
         node("uuid", {atom(q(sheet_uuid))}),
         prop("Sheetname", show_path(rel), x, y - 0.7, false, "left bottom"),
         prop("Sheetfile", child_file, x, y + h + 0.6, false, "left top")});
    std::vector<Sx> extra;
    for (size_t i = 0; i < pins.size(); ++i) {
      const auto &[field, net] = pins[i];
      double py = y + static_cast<double>(i + 1) * PITCH;
      add(out,
          node("pin", {atom(q(field)), atom("passive"), at(x, py, 180),
                       node("uuid", {atom(q(uid(Json::array(
                                        {file, rel, "sheet-pin", field}))))}),
                       effects("left")}));
      if (single_.contains(net) && !port_map.contains(net)) {
        // Its one pin is inside the block: nothing on this sheet joins it.
        auto tag = uid(Json::array({file, rel, "sheet-nc", field}));
        extra.push_back(
            node("no_connect", {at(x, py), node("uuid", {atom(q(tag))})}));
        continue;
      }
      auto [how, text] = label(net, first, port_map);
      for (auto &sx : labels(how, text, x, py, 180, file,
                             Json::array({rel, "sheet-pin", field})))
        extra.push_back(std::move(sx));
      for (auto &sx :
           decorations(k, Json::array({"sheet", rel, field}), x, py, file))
        extra.push_back(std::move(sx));
    }
    auto project = node("project", {atom(q(project_))});
    for (const auto &instance : instances_.at(key(first)))
      add(project,
          node("path",
               {atom(q(instance.path)),
                node("page", {atom(q(std::to_string(pages_.at(
                                 instance.path + "/" + sheet_uuid))))})}));
    add(out, node("instances", {project}));
    std::vector<Sx> result{out};
    for (auto &sx : extra)
      result.push_back(std::move(sx));
    return result;
  }
};

} // namespace

std::vector<std::pair<std::string, std::string>>
render_schematic(const Json &circuit, const Json &state,
                 const std::string &project, const std::string &stem) {
  return Sheets(circuit, state, project).render(stem);
}

} // namespace noscheme::kicad
