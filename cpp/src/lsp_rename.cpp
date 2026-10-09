#include "noscheme/lsp_rename.hpp"
#include "noscheme/circuit.hpp"
#include "noscheme/lsp_completion.hpp"
#include "noscheme/validation.hpp"
#include <set>
namespace noscheme {
namespace {
using ast::as;
std::string spelling(const std::string &filename, std::size_t start,
                     std::size_t end, const Sources &sources) {
  return sources.at(filename).substr(start, end - start);
}
std::string spelling(const Span &span, const Sources &sources) {
  return spelling(span.filename(), span.start, span.end, sources);
}
// Edit spans are in diagnostic form.
std::string spelling(const Json &span, const Sources &sources) {
  return spelling(span.at("filename"), span.at("start_pos"), span.at("end_pos"),
                  sources);
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
    for (const auto &item : found->second.ast->items) {
      if (auto *definition = as<ast::Definition>(item);
          definition && definition->name.text == name)
        return key;
      auto *import = as<ast::Import>(item);
      if (!import)
        continue;
      auto target = ast::dotted(import->module);
      if (import->names.empty() && name.starts_with(target + "."))
        return cache[key] = origin(target, name.substr(target.size() + 1));
      for (const auto &binding : import->names)
        if (binding.local().text == name)
          return cache[key] =
                     binding.alias ? key : origin(target, binding.name.text);
    }
    return key;
  }
  Origin occurrence(const LspReference &reference, const Sources &sources) {
    for (const auto &[name, module] : project.modules)
      if (module.path == reference.span.filename()) {
        for (const auto &item : module.ast->items)
          if (auto *import = as<ast::Import>(item))
            for (const auto &binding : import->names) {
              if (binding.name.span == reference.span)
                return origin(ast::dotted(import->module), binding.name.text);
              if (binding.alias && binding.alias->span == reference.span)
                return {name, binding.alias->text};
            }
        return origin(name, spelling(reference.span, sources));
      }
    return {};
  }
};
Json signature(const Circuit &circuit) {
  auto parts = Json::object();
  std::map<std::string, std::set<std::string>> nets;
  for (const auto &part : circuit.parts) {
    auto params = Json::object();
    for (const auto &[name, value] : part.parameters)
      params[name] = encoded(value);
    auto values =
        Json::array({part.definition, std::move(params),
                     part.value ? encoded(*part.value) : Json(nullptr)});
    for (auto name : {"footprint", "ref", "ref_prefix"}) {
      auto *value = meta_value(part.metadata, name);
      values.push_back(value ? to_json(*value) : Json());
    }
    parts[part.identity] = std::move(values);
    for (const auto &[number, leaf] : part.pins)
      if (auto net = circuit.leaves[leaf].net)
        nets[circuit.nets[*net].name].insert(
            Json::array({part.identity, number}).dump());
  }
  std::set<std::set<std::string>> connectivity;
  for (const auto &[net, endpoints] : nets)
    connectivity.insert(endpoints);
  return object("parts", parts, "connectivity", connectivity);
}
void identity_edit(const ast::Instance &instance, const Sources &sources,
                   Json &edits) {
  const auto &filename = instance.span.filename();
  const auto &text = sources.at(filename);
  Source source(text, filename);
  auto location = [&](const Span &span) {
    return source.span(span.start, span.end);
  };
  auto span = location(instance.span);
  auto newline = text.find("\r\n") == std::string::npos ? "\n" : "\r\n";
  auto identity = "identity: " + Json(instance.name.text).dump();
  std::size_t base = span.at("column").get<std::size_t>() - 1,
              section_indent = base + 2;
  for (const auto &section : instance.sections) {
    auto where = location(section.span);
    if (where.at("line") != span.at("line")) {
      section_indent = where.at("column").get<std::size_t>() - 1;
      break;
    }
  }
  for (const auto &section : instance.sections)
    if (section.name == "metadata") {
      for (const auto *field : ast::field_list(section.entries))
        if (field->key.first.text == "identity")
          return;
      const auto &first = as<ast::Field>(*section.entries.front()).key.span;
      auto start = text.rfind('\n', first.start - 1);
      start = start == std::string::npos ? 0 : start + 1;
      edits.push_back(
          object("span", source.span(start, start), "newText",
                 std::string(
                     location(first).at("column").get<std::size_t>() - 1, ' ') +
                     identity + newline));
      return;
    }
  auto masked = code_mask(text, true);
  auto last = std::min(text.size(), instance.span.end);
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
void waiver_edits(const ast::Definition &owner, const std::string &old,
                  const std::string &name, Json &edits) {
  std::function<void(const ast::Node &)> walk = [&](const ast::Node &node) {
    auto *field = as<ast::Field>(&node);
    if (field && field->key.first.text == "waivers") {
      for (const auto &waiver : field->fields)
        for (const auto &entry : waiver.fields)
          if (auto *list = as<ast::ListExpr>(entry.value);
              list && entry.key.first.text == "pins")
            for (const auto &pin : list->items)
              if (auto *string = as<ast::String>(pin)) {
                const auto &path = string->value;
                if (path == old || path.starts_with(old + ".") ||
                    path.starts_with(old + "["))
                  edits.push_back(
                      object("span", string->span.json(), "newText",
                             Json(name + path.substr(old.size())).dump()));
              }
      return;
    }
    ast::for_each_child(
        node, [&](std::string_view, const ast::Node &child) { walk(child); });
  };
  // Only waiver fields of the containing board's nets/ports have these paths.
  for (const auto &node : owner.body) {
    if (auto *net = as<ast::Net>(node))
      for (const auto &field : net->fields)
        walk(field);
    else if (auto *section = as<ast::Section>(node);
             section && section->name == "ports")
      for (const auto &entry : section->entries)
        walk(*entry);
  }
}
} // namespace
Json rename_plan(const RenameContext &context, const Sources &sources,
                 const std::vector<LspReference> &references,
                 const fs::path &path, std::size_t offset,
                 const std::optional<std::string> &new_name) {
  const LspReference *selected = nullptr;
  for (const auto &r : references)
    if (r.span.filename() == path.string() && offset >= r.span.start &&
        offset < r.span.end &&
        (!selected ||
         r.span.end - r.span.start < selected->span.end - selected->span.start))
      selected = &r;
  if (!selected || !selected->target)
    throw std::runtime_error("No renameable symbol at this position.");
  bool allowed = false, named = false;
  const ast::Instance *instance = nullptr;
  const ast::Definition *owner = nullptr;
  for (const auto &[module_name, module] : context.project.modules)
    for (const auto &entry : module.ast->items)
      if (auto *item = as<ast::Definition>(entry)) {
        bool board = item->keyword == "board";
        if (!board && item->name.span == selected->target)
          allowed = named = true;
        auto field_allowed = [&](const ast::Field &field) {
          if (field.key.items.empty() && !field.key.last &&
              field.key.first.span == selected->target)
            allowed = true;
        };
        for (const auto &node : item->body) {
          if (auto *net = as<ast::Net>(node);
              net && net->name.span == selected->target)
            allowed = true;
          if (auto *declared = as<ast::Instance>(node);
              declared && board && declared->name.span == selected->target) {
            allowed = true;
            instance = declared;
            owner = item;
          }
          if (auto *field = as<ast::Field>(node);
              field && item->keyword == "bundle")
            field_allowed(*field);
          if (auto *section = as<ast::Section>(node);
              section &&
              (section->name == "params" ||
               (section->name == "ports" && item->keyword == "block")))
            for (const auto *field : ast::field_list(section->entries))
              field_allowed(*field);
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
      Source source(sources.at(r.span.filename()), r.span.filename());
      auto span = source.span(r.span.start + prefix, r.span.end);
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
    if (!context.circuit)
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
      signature(*context.circuit) !=
          signature(elaborate_project(project, context.registry, definitions)))
    throw std::runtime_error(
        "Rename would change physical identities, parts, or connectivity.");
  return result;
}
} // namespace noscheme
