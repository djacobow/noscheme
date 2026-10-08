#include "noscheme/lsp_rename.hpp"
#include "noscheme/circuit.hpp"
#include "noscheme/lsp_completion.hpp"
#include "noscheme/validation.hpp"
#include <set>
namespace noscheme {
namespace {
std::string spelling(const Json &span, const Sources &sources) {
  return sources.at(span.at("filename").get<std::string>())
      .substr(span.at("start_pos"),
              span.at("end_pos").get<std::size_t>() -
                  span.at("start_pos").get<std::size_t>());
}
std::string dotted(const Json &names) {
  std::string result;
  for (const auto &name : names) {
    if (!result.empty())
      result += '.';
    result += name.at("text").get<std::string>();
  }
  return result;
}
using Origin = std::pair<std::string, std::string>;
class Bindings {
  const Project &project;
  std::map<Origin, Origin> cache;

public:
  explicit Bindings(const Project &project) : project(project) {}
  Origin origin(const std::string &module, const std::string &name) {
    Origin key{module, name};
    if (cache.contains(key))
      return cache.at(key);
    cache[key] =
        key; // Imports are acyclic, but keep lookup bounded defensively.
    auto found = project.modules.find(module);
    if (found == project.modules.end())
      return key;
    for (const auto &item : found->second.ast->at("items")) {
      if (item.at("node") == "Definition" && item.at("name").at("text") == name)
        return key;
      if (item.at("node") != "Import")
        continue;
      auto target = dotted(item.at("module"));
      if (item.at("names").empty() && name.starts_with(target + "."))
        return cache[key] = origin(target, name.substr(target.size() + 1));
      for (const auto &binding : item.at("names")) {
        auto local = binding.at("alias").is_null() ? binding.at("name")
                                                   : binding.at("alias");
        if (local.at("text") == name)
          return cache[key] =
                     binding.at("alias").is_null()
                         ? origin(target, binding.at("name").at("text"))
                         : key;
      }
    }
    return key;
  }
  Origin occurrence(const LspReference &reference, const Sources &sources) {
    for (const auto &[name, module] : project.modules)
      if (module.path == reference.span.at("filename").get<std::string>()) {
        for (const auto &item : module.ast->at("items"))
          if (item.at("node") == "Import")
            for (const auto &binding : item.at("names")) {
              if (binding.at("name").at("span") == reference.span)
                return origin(dotted(item.at("module")),
                              binding.at("name").at("text"));
              if (!binding.at("alias").is_null() &&
                  binding.at("alias").at("span") == reference.span)
                return {name, binding.at("alias").at("text")};
            }
        return origin(name, spelling(reference.span, sources));
      }
    return {};
  }
};
Json signature(const Json &circuit) {
  auto parts = Json::object();
  std::map<std::string, std::set<std::string>> nets;
  for (const auto &part : circuit.at("parts")) {
    auto id = part.at("identity").get<std::string>();
    const auto &metadata = part.at("metadata");
    auto values = Json::array(
        {part.at("definition"), part.at("parameters"), part.at("value")});
    for (auto name : {"footprint", "ref", "ref_prefix"})
      values.push_back(metadata.contains(name) ? metadata.at(name).at("value")
                                               : Json());
    parts[id] = values;
    for (const auto &[number, pin] : part.at("pins").items())
      if (!pin.at("net").is_null())
        nets[pin.at("net")].insert(Json::array({id, number}).dump());
  }
  std::set<std::set<std::string>> connectivity;
  for (const auto &[net, endpoints] : nets)
    connectivity.insert(endpoints);
  return object("parts", parts, "connectivity", connectivity);
}
void identity_edit(const Json &instance, const Sources &sources, Json &edits) {
  auto span = instance.at("span");
  auto filename = span.at("filename").get<std::string>();
  const auto &text = sources.at(filename);
  Source source(text, filename);
  auto newline = text.find("\r\n") == std::string::npos ? "\n" : "\r\n";
  auto identity = "identity: " + instance.at("name").at("text").dump();
  std::size_t base = span.at("column").get<std::size_t>() - 1,
              section_indent = base + 2;
  for (const auto &section : instance.at("sections")) {
    if (section.at("span").at("line") != span.at("line")) {
      section_indent = section.at("span").at("column").get<std::size_t>() - 1;
      break;
    }
  }
  for (const auto &section : instance.at("sections"))
    if (section.at("name") == "metadata") {
      for (const auto &field : section.at("entries"))
        if (field.at("key").at("first").at("text") == "identity")
          return;
      const auto &first = section.at("entries").front().at("key").at("span");
      auto start =
          text.rfind('\n', first.at("start_pos").get<std::size_t>() - 1);
      start = start == std::string::npos ? 0 : start + 1;
      edits.push_back(
          object("span", source.span(start, start), "newText",
                 std::string(first.at("column").get<std::size_t>() - 1, ' ') +
                     identity + newline));
      return;
    }
  auto masked = code_mask(text, true);
  auto last = std::min(text.size(), span.at("end_pos").get<std::size_t>());
  while (last && std::isspace(static_cast<unsigned char>(masked[last - 1])))
    --last;
  auto finish = text.find('\n', last);
  if (finish == std::string::npos)
    finish = text.size();
  else if (finish && text[finish - 1] == '\r')
    --finish;
  edits.push_back(object(
      "span", source.span(finish, finish), "newText",
      std::string(newline) + std::string(section_indent, ' ') + "metadata:" +
          newline + std::string(section_indent * 2 - base, ' ') + identity));
}
void waiver_edits(const Json &owner, const std::string &old,
                  const std::string &name, Json &edits) {
  std::function<void(const Json &)> walk = [&](const Json &node) {
    if (node.is_array()) {
      for (const auto &v : node)
        walk(v);
    } else if (node.is_object()) {
      if (node.value("node", "") == "Field" &&
          node.at("key").at("first").at("text") == "waivers") {
        for (const auto &waiver : node.at("fields"))
          for (const auto &entry : waiver.at("fields"))
            if (entry.at("key").at("first").at("text") == "pins" &&
                entry.at("value").value("node", "") == "ListExpr")
              for (const auto &pin : entry.at("value").at("items"))
                if (pin.at("node") == "String") {
                  auto path = pin.at("value").get<std::string>();
                  if (path == old || path.starts_with(old + ".") ||
                      path.starts_with(old + "["))
                    edits.push_back(
                        object("span", pin.at("span"), "newText",
                               Json(name + path.substr(old.size())).dump()));
                }
        return;
      }
      for (const auto &[k, v] : node.items())
        if (k != "span")
          walk(v);
    }
  };
  // Only waiver fields of the containing board's nets/ports have these paths.
  for (const auto &node : owner.at("body")) {
    if (node.at("node") == "Net")
      walk(node.at("fields"));
    else if (node.at("node") == "Section" && node.at("name") == "ports")
      walk(node.at("entries"));
  }
}
} // namespace
Json rename_plan(const RenameContext &context, const Sources &sources,
                 const std::vector<LspReference> &references,
                 const fs::path &path, std::size_t offset,
                 const std::optional<std::string> &new_name) {
  const LspReference *selected = nullptr;
  for (const auto &r : references)
    if (r.span.at("filename") == path.string() &&
        offset >= r.span.at("start_pos").get<std::size_t>() &&
        offset < r.span.at("end_pos").get<std::size_t>() &&
        (!selected ||
         r.span.at("end_pos").get<std::size_t>() -
                 r.span.at("start_pos").get<std::size_t>() <
             selected->span.at("end_pos").get<std::size_t>() -
                 selected->span.at("start_pos").get<std::size_t>()))
      selected = &r;
  if (!selected || selected->target.is_null())
    throw std::runtime_error("No renameable symbol at this position.");
  bool allowed = false, named = false;
  const Json *instance = nullptr, *owner = nullptr;
  for (const auto &[module_name, module] : context.project.modules)
    for (const auto &item : module.ast->at("items"))
      if (item.at("node") == "Definition") {
        if (item.at("kind") != "board" &&
            item.at("name").at("span") == selected->target)
          allowed = named = true;
        for (const auto &node : item.at("body")) {
          auto kind = node.at("node").get<std::string>();
          if ((kind == "Net" ||
               (kind == "Instance" && item.at("kind") == "board")) &&
              node.at("name").at("span") == selected->target) {
            allowed = true;
            if (kind == "Instance") {
              instance = &node;
              owner = &item;
            }
          }
          auto field_allowed = [&](const Json &field) {
            if (field.at("key").at("items").empty() &&
                field.at("key").at("last").is_null() &&
                field.at("key").at("first").at("span") == selected->target)
              allowed = true;
          };
          if (kind == "Field" && item.at("kind") == "bundle")
            field_allowed(node);
          if (kind == "Section" &&
              (node.at("name") == "params" ||
               (node.at("name") == "ports" && item.at("kind") == "block")))
            for (const auto &field : node.at("entries"))
              field_allowed(field);
        }
      }
  if (!allowed)
    throw std::runtime_error(
        "Rename supports nets, parameters, components, blocks, bundles, ports, "
        "bundle fields, and board-level instances. Physical pins, aliases, "
        "board names, and internal instances cannot be renamed.");
  Bindings bindings(context.project);
  auto origin = bindings.occurrence(*selected, sources);
  Json spans = Json::array();
  Json selected_span;
  for (const auto &r : references)
    if (r.target == selected->target &&
        (!named || bindings.occurrence(r, sources) == origin)) {
      auto text = spelling(r.span, sources);
      auto dot = text.rfind('.');
      auto prefix = dot == std::string::npos ? 0 : dot + 1;
      for (std::size_t begin = 0; begin < text.size();) {
        auto stop = text.find('.', begin);
        if (stop == std::string::npos)
          stop = text.size();
        if (!is_identifier(text.substr(begin, stop - begin)))
          throw std::runtime_error(
              "This source occurrence cannot be renamed safely.");
        begin = stop + 1;
      }
      auto span = r.span;
      Source source(sources.at(span.at("filename").get<std::string>()),
                    span.at("filename"));
      span = source.span(span.at("start_pos").get<std::size_t>() + prefix,
                         span.at("end_pos"));
      if (std::find(spans.begin(), spans.end(), span) == spans.end())
        spans.push_back(span);
      if (r.span == selected->span)
        selected_span = span;
    }
  if (selected_span.is_null() ||
      offset < selected_span.at("start_pos").get<std::size_t>())
    throw std::runtime_error(
        "Place the cursor on the symbol name, not its module qualifier.");
  auto old = spelling(selected_span, sources);
  auto result =
      object("span", selected_span, "placeholder", old, "edits", Json::array());
  if (!new_name)
    return result;
  const std::set<std::string> reserved{"no_connect", "global", "true",
                                       "false",      "number", "string"};
  if (!is_identifier(*new_name) || reserved.contains(*new_name))
    throw std::runtime_error(
        "The new name must be a NoScheme identifier, not a reserved value.");
  if (*new_name == old)
    return result;
  auto &edits = result["edits"];
  for (const auto &span : spans)
    edits.push_back(object("span", span, "newText", *new_name));
  if (instance) {
    if (context.circuit.is_null())
      throw std::runtime_error(
          "Board-instance rename requires a successfully elaborated circuit.");
    identity_edit(*instance, sources, edits);
    waiver_edits(*owner, old, *new_name, edits);
  }
  auto edited = sources;
  std::sort(edits.begin(), edits.end(), [](const auto &a, const auto &b) {
    const auto &x = a.at("span"), &y = b.at("span");
    return x.at("filename") == y.at("filename")
               ? x.at("start_pos") > y.at("start_pos")
               : x.at("filename") > y.at("filename");
  });
  for (const auto &edit : edits) {
    const auto &s = edit.at("span");
    auto &text = edited.at(s.at("filename").get<std::string>());
    text.replace(s.at("start_pos"),
                 s.at("end_pos").get<std::size_t>() -
                     s.at("start_pos").get<std::size_t>(),
                 edit.at("newText").get<std::string>());
  }
  auto project = load_project(
      context.project.config.path, edited, nullptr,
      [&](const ProjectConfig &) { return context.registry.exports(); });
  auto definitions = resolve_all(project, context.registry);
  if (instance &&
      signature(context.circuit) !=
          signature(elaborate_project(project, context.registry, definitions)))
    throw std::runtime_error(
        "Rename would change physical identities, parts, or connectivity.");
  return result;
}
} // namespace noscheme
