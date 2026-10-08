#include "noscheme/resolve.hpp"
#include "noscheme/validation.hpp"
#include <algorithm>

namespace noscheme {
namespace {
[[noreturn]] void fail(const std::string &code, const std::string &message,
                       const Json &span, const Json &related = Json::array()) {
  const Symbols symbols;
  Evaluator(symbols).error(code, message, span, related);
}
template <typename F> void collect(Json &errors, F &&operation) {
  try {
    operation();
  } catch (const DiagnosticsFailure &failure) {
    for (const auto &diagnostic : failure.diagnostics)
      errors.push_back(diagnostic);
  }
}
} // namespace
const Json &section_entries(const Json &node, std::string_view name) {
  static const Json empty = Json::array();
  for (const auto &section : node.at("sections"))
    if (section.at("name") == name)
      return section.at("entries");
  return empty;
}
namespace {
Json names(const Json &expression) {
  std::vector<Json> parts;
  const Json *current = &expression;
  while (current->at("node") == "Member") {
    parts.push_back(current->at("name"));
    current = &current->at("base");
  }
  if (current->at("node") != "Name")
    return Json::array();
  parts.push_back(*current);
  std::ranges::reverse(parts);
  return parts;
}
std::string spelling(const Json &parts) {
  std::string result;
  for (const auto &part : parts) {
    if (!result.empty())
      result += '.';
    result += part.at("text").get<std::string>();
  }
  return result;
}
std::set<std::string> parameters(const Definition &definition) {
  std::set<std::string> result;
  for (const auto &parameter : definition.parameters.parameters)
    result.insert(parameter.name);
  return result;
}
Json choices(const Definition &definition) {
  return definition.metadata.contains("erc_choices")
             ? definition.metadata.at("erc_choices").at("value")
             : Json::object();
}
std::set<std::string> possible(const Json &wrapped) {
  const auto &value = wrapped.at("value");
  std::set<std::string> result;
  if (value.is_object()) {
    for (const auto &[name, item] : value.at("cases").at("value").items())
      result.insert(item.at("value"));
  } else if (value.is_string())
    result.insert(value);
  else
    for (const auto &item : value)
      result.insert(item);
  return result;
}
void selection(const Json &value, const Json &declared) {
  const auto &fields = value.at("value");
  if (!fields.is_object())
    return;
  const auto &choice = fields.at("choice");
  std::string name = choice.at("value");
  if (!declared.contains(name))
    fail("erc.unknown_choice", "unknown local ERC choice '" + name + "'",
         choice.at("span"));
  std::set<std::string> cases;
  for (const auto &[key, unused] : fields.at("cases").at("value").items())
    cases.insert(key);
  if (cases != possible(declared.at(name)))
    fail("erc.choice_cases",
         "cases must cover every declared choice alternative exactly",
         fields.at("cases").at("span"));
}
std::vector<std::string>
metadata_keys(const Json &values, const MetadataOrder *orders = nullptr,
              const std::vector<std::string> &path = {}) {
  std::vector<std::string> keys;
  for (const auto &[key, unused] : values.items())
    keys.push_back(key);
  // Fresh mappings follow source order; inherited mappings retain the
  // parent's slot for an overridden field, even when that override is in
  // another file.
  std::stable_sort(
      keys.begin(), keys.end(),
      [&](const std::string &a, const std::string &b) {
        return values.at(a).at("span").at("start_pos").get<std::size_t>() <
               values.at(b).at("span").at("start_pos").get<std::size_t>();
      });
  if (orders && orders->contains(path)) {
    const auto &order = orders->at(path);
    std::stable_sort(keys.begin(), keys.end(),
                     [&](const std::string &a, const std::string &b) {
                       return std::ranges::find(order, a) <
                              std::ranges::find(order, b);
                     });
  }
  return keys;
}
class BodyResolver {
  const Project &project;
  const RegistryDescription &registry;
  Definitions &definitions;
  std::size_t max_pins, max_depth;

  const Definition &target(const std::string &module, const Json &path,
                           const std::vector<std::string> &kinds) const {
    return definitions.at(definition_symbol(project, module, path, kinds));
  }
  const Definition *type(const Json &wrapped) const {
    return type_definition(project, registry, definitions, wrapped);
  }
  void select(Json value, const Json &selectors) const {
    for (const auto &selector : selectors) {
      if (selector.at("node") == "Index")
        continue;
      auto definition = type(value);
      std::string name = selector.at("text");
      if (!definition || !definition->interface.contains(name))
        fail("resolve.private", "'" + name + "' is not a public bundle field",
             selector.at("span"));
      value = definition->interface.at(name).type;
    }
  }
  void expression(const Json &expr, const std::string &module,
                  const std::set<std::string> &locals, std::size_t depth = 0,
                  const Json &outer_span = nullptr) const {
    if (depth > max_depth)
      fail("resolve.expression-limit",
           "expression exceeds configured depth limit",
           outer_span.is_null() ? expr.at("span") : outer_span);
    const std::string kind = expr.at("node");
    if (kind == "Number" || kind == "String")
      return;
    if (kind == "Name") {
      std::string name = expr.at("text");
      if (!locals.contains(name))
        fail("resolve.expression-name", "unknown parameter '" + name + "'",
             expr.at("span"));
      return;
    }
    auto child = [&](const Json &value, const Json &span = nullptr) {
      expression(value, module, locals, depth + 1, span);
    };
    if (kind == "Unary" || kind == "Group") {
      child(expr.at(kind == "Unary" ? "operand" : "expression"));
      return;
    }
    if (kind == "Binary") {
      child(expr.at("left"));
      child(expr.at("right"));
      return;
    }
    if (kind == "Call" && expr.at("callee").at("node") == "Member") {
      const auto &callee = expr.at("callee");
      std::string method = callee.at("name").at("text");
      if (method == "format")
        child(callee.at("base"));
      else {
        auto path = names(callee.at("base"));
        if (path.empty())
          fail("resolve.helper", "arbitrary method calls are not supported",
               callee.at("span"));
        const auto &symbols = project.modules.at(module).symbols;
        auto symbol = symbols.find(spelling(path));
        if (locals.contains(path.front().at("text")) ||
            symbol == symbols.end() || symbol->second.module != "noscheme" ||
            symbol->second.name != "num" ||
            !std::set<std::string>{"int", "ceil", "floor", "round"}.contains(
                method))
          fail("resolve.helper", "unknown or unimported numeric helper",
               callee.at("span"));
      }
      for (const auto &argument : expr.at("arguments"))
        child(argument);
      for (const auto &argument : expr.at("keywords"))
        child(argument.at("value"), argument.at("span"));
      return;
    }
    fail("resolve.expression",
         "unsupported expression in parameter/value context", expr.at("span"));
  }
  void metadata_expressions(const Json &values,
                            const std::set<std::string> &locals,
                            Json *errors = nullptr,
                            const MetadataOrder *orders = nullptr,
                            std::vector<std::string> path = {}) const {
    for (const auto &key : metadata_keys(values, orders, path)) {
      const auto &wrapped = values.at(key);
      const auto &value = wrapped.at("value");
      if (!value.is_object())
        continue;
      bool numeric =
          value.contains("constraint") && value.at("constraint").is_string();
      bool ast = value.contains("node") && value.at("node").is_string();
      auto check = [&] {
        expression(numeric ? value.at("expression") : value,
                   wrapped.at("module"), locals, 0, wrapped.at("span"));
      };
      if (ast || numeric) {
        if (errors)
          collect(*errors, check);
        else
          check();
      } else if (!(value.contains("type") && value.at("type").is_string())) {
        auto nested = path;
        nested.push_back(key);
        metadata_expressions(value, locals, errors, orders, std::move(nested));
      }
    }
  }
  void index(const Json &selection, const std::string &module,
             const std::set<std::string> &locals) const {
    expression(selection.at("start"), module, locals, 0, selection.at("span"));
    if (!selection.at("stop").is_null())
      expression(selection.at("stop"), module, locals, 0, selection.at("span"));
  }
  void binding(const Json &binding, const Definition &part,
               const Definition &owner,
               const std::map<std::string, Json> &nets) const {
    const auto &endpoint = binding.at("endpoint");
    auto locals = parameters(owner);
    if (!endpoint.at("head").at("items").empty() && part.kind != "component")
      fail("resolve.pin-list",
           "pin lists select physical component pins, not block ports",
           endpoint.at("head").at("span"));
    for (const auto &key : {"instance_indices", "selectors"})
      for (const auto &selector : endpoint.at(key))
        if (selector.at("node") == "Index")
          index(selector, owner.body_module, locals);
    for (const auto &name : physical_keys(endpoint.at("head"), max_pins)) {
      auto alias = part.aliases.find(name);
      if (alias == part.aliases.end())
        fail("resolve.private",
             "'" + name + "' is not a public pin or port of " + part.key.second,
             endpoint.at("head").at("span"));
      const auto &port = part.interface.at(alias->second);
      if (!endpoint.at("selectors").empty()) {
        if (port.type.is_null())
          fail("resolve.private", "physical pins have no fields or elements",
               endpoint.at("span"));
        select(port.type, endpoint.at("selectors"));
      }
    }
    const Json *expr = &binding.at("target");
    auto selectors = Json::array();
    while (expr->at("node") == "Member" || expr->at("node") == "Subscript") {
      selectors.push_back(
          expr->at(expr->at("node") == "Member" ? "name" : "selection"));
      expr = &expr->at("base");
    }
    std::reverse(selectors.begin(), selectors.end());
    for (const auto &selector : selectors)
      if (selector.at("node") == "Index")
        index(selector, owner.body_module, locals);
    std::string name = expr->at("text");
    if (name == "global") {
      if (selectors.empty() || selectors.front().at("node") != "Name")
        fail("resolve.global", "global references require an explicit net name",
             expr->at("span"));
      return;
    }
    if (name == "no_connect" && selectors.empty())
      return;
    if (!nets.contains(name))
      fail("resolve.net", "'" + name + "' is not a declared caller net or port",
           expr->at("span"));
    select(nets.at(name), selectors);
  }
  void instance_metadata(const Json &node, const Definition &part,
                         const Definition &owner) const {
    auto locals = parameters(owner);
    MetadataReader reader(project, registry, owner.body_module, locals);
    auto ordinary = Json::array();
    const auto &fields = section_entries(node, "metadata");
    for (const auto &field : fields)
      if (field.at("key").at("first").at("text") != "pins")
        ordinary.push_back(field);
    auto values = reader.read(ordinary, "instance");
    if (part.kind != "component")
      for (const auto &[name, unused] : values.items())
        if (!std::set<std::string>{"custom", "identity", "erc_choices"}
                 .contains(name))
          fail("metadata.scope",
               "physical-part metadata cannot be applied to a reusable block",
               node.at("span"));
    auto declared = choices(owner);
    if (values.contains("erc_choices")) {
      auto permitted = choices(part);
      for (const auto &name :
           metadata_keys(values.at("erc_choices").at("value"))) {
        const auto &value = values.at("erc_choices").at("value").at(name);
        if (!permitted.contains(name))
          fail("erc.unknown_choice", "unknown public ERC choice '" + name + "'",
               value.at("span"));
        selection(value, declared);
        if (!std::ranges::includes(possible(permitted.at(name)),
                                   possible(value)))
          fail("erc.choice_value",
               "selection is outside alternatives for '" + name + "'",
               value.at("span"));
      }
    }
    metadata_expressions(values, locals);
    for (const auto &group : fields) {
      if (group.at("key").at("first").at("text") != "pins")
        continue;
      if (!group.at("value").is_null() || !group.at("key").at("last").is_null())
        fail("metadata.value",
             "instance pins metadata requires an indented mapping",
             group.at("span"));
      std::map<std::string, Json> seen;
      for (const auto &field : group.at("fields")) {
        if (!field.at("key").at("items").empty() && part.kind != "component")
          fail("resolve.pin-list",
               "pin lists select physical component pins, not block ports",
               field.at("key").at("span"));
        auto annotations = reader.pin(field, "instance_pin");
        metadata_expressions(annotations, locals);
        for (const auto &key : {"role", "external_role"})
          if (annotations.contains(key))
            selection(annotations.at(key), declared);
        if (annotations.contains("external_role") && part.kind == "component")
          fail("metadata.role",
               "write role on a component pin: on a passive or undeclared "
               "contact it describes the attached equipment",
               annotations.at("external_role").at("span"));
        for (const auto &name : physical_keys(field.at("key"), max_pins)) {
          auto alias = part.aliases.find(name);
          if (alias == part.aliases.end())
            fail("resolve.pin", "unknown pin or port '" + name + "'",
                 field.at("span"));
          const auto &pin = alias->second;
          if (seen.contains(pin))
            fail("resolve.duplicate-pin",
                 "duplicate annotation for '" + pin + "'", field.at("span"),
                 Json::array({object("message", "previous annotation", "span",
                                     seen.at(pin))}));
          seen[pin] = field.at("span");
          const auto &metadata = part.interface.at(pin).metadata;
          if (annotations.contains("role") && metadata.contains("role")) {
            auto permitted = possible(metadata.at("role"));
            bool attached = attached_role(part, pin);
            if (!attached && !std::ranges::includes(
                                 permitted, possible(annotations.at("role"))))
              fail("metadata.constraint",
                   "instance role is outside the definition permitted roles",
                   annotations.at("role").at("span"));
          }
        }
      }
    }
  }
  void instance(const Json &node, const Definition &owner,
                const std::map<std::string, Json> &nets) const {
    const auto &part =
        target(owner.body_module, node.at("component"), {"component", "block"});
    auto locals = parameters(owner), permitted = parameters(part);
    auto errors = Json::array();
    for (const auto &dimension : node.at("dimensions"))
      collect(errors, [&] {
        if (!dimension.at("stop").is_null())
          fail("resolve.dimension", "instance dimensions cannot be slices",
               dimension.at("span"));
        expression(dimension.at("start"), owner.body_module, locals, 0,
                   dimension.at("span"));
      });
    for (const auto &argument : section_entries(node, "params"))
      collect(errors, [&] {
        std::string name = argument.at("name").at("text");
        if (!permitted.contains(name))
          fail("resolve.argument", "unknown parameter '" + name + "'",
               argument.at("name").at("span"));
        expression(argument.at("value"), owner.body_module, locals, 0,
                   argument.at("span"));
      });
    for (const auto &connection : section_entries(node, "connections"))
      collect(errors, [&] { binding(connection, part, owner, nets); });
    collect(errors, [&] { instance_metadata(node, part, owner); });
    for (const auto &element : section_entries(node, "elements"))
      collect(errors, [&] {
        if (node.at("dimensions").empty() ||
            element.at("indices").size() != node.at("dimensions").size())
          fail("array.element-rank",
               "element overrides require one index per array dimension",
               element.at("span"));
        for (const auto &index : element.at("indices"))
          if (!index.at("stop").is_null() ||
              index.at("start").at("node") != "Number" ||
              index.at("start").at("tag") != "integer")
            fail("array.element-index",
                 "element indices must be nonnegative integer literals, "
                 "without slices",
                 index.at("span"));
        for (const auto &field : section_entries(element, "metadata"))
          if (field.at("key").at("first").at("text") == "identity" ||
              field.at("key").at("first").at("text") == "ref")
            fail("array.element-identity",
                 "per-element identity and ref overrides are not supported",
                 field.at("span"));
        auto override = node;
        override["sections"] = element.at("sections");
        instance(override, owner, nets);
      });
    if (!errors.empty())
      throw DiagnosticsFailure(std::move(errors));
  }

public:
  BodyResolver(const Project &project, const RegistryDescription &registry,
               Definitions &definitions, std::size_t max_pins,
               std::size_t max_depth)
      : project(project), registry(registry), definitions(definitions),
        max_pins(max_pins), max_depth(max_depth) {}
  void expand(Definition &definition) const {
    // Build transactionally: a failed expansion leaves this definition
    // unchanged.
    auto body = definition.body;
    std::vector<std::shared_ptr<const Json>> owned;
    for (auto &node : body) {
      if (node->at("node") != "Instance" || node->at("positional").empty())
        continue;
      const auto &part = target(definition.body_module, node->at("component"),
                                {"component", "block"});
      auto pins = part.metadata.contains("interchangeable")
                      ? part.metadata.at("interchangeable").at("value")
                      : Json::array();
      if (pins.empty())
        fail("resolve.positional",
             part.key.second +
                 " declares no interchangeable pins; connect pins by name",
             node->at("positional").front().at("span"));
      if (pins.size() != node->at("positional").size())
        fail("resolve.positional",
             part.key.second + " has " + std::to_string(pins.size()) +
                 " interchangeable pins; give a net or no_connect for each",
             node->at("span"));
      auto copy = std::make_shared<Json>(*node);
      auto bindings = section_entries(*node, "connections"),
           sections = Json::array();
      for (std::size_t i = 0; i < pins.size(); ++i) {
        const auto &expr = node->at("positional")[i];
        const auto &span = expr.at("span");
        auto key = object("node", "PinKey", "span", span, "text", pins[i],
                          "spelling", pins[i]);
        auto field = object("node", "FieldKey", "span", span, "first", key,
                            "last", nullptr, "items", Json::array());
        auto endpoint =
            object("node", "Endpoint", "span", span, "instance_indices",
                   Json::array(), "head", field, "selectors", Json::array());
        bindings.push_back(object("node", "Binding", "span", span, "endpoint",
                                  endpoint, "target", expr));
      }
      for (const auto &section : node->at("sections"))
        if (section.at("name") != "connections")
          sections.push_back(section);
      sections.push_back(object("node", "Section", "span", node->at("span"),
                                "name", "connections", "entries", bindings));
      (*copy)["sections"] = std::move(sections);
      (*copy)["positional"] = Json::array();
      node = copy.get();
      owned.push_back(std::move(copy));
    }
    definition.body = std::move(body);
    definition.expanded_body = std::move(owned);
  }
  void body(const Definition &definition) const {
    auto locals = parameters(definition);
    std::map<std::string, Json> nets;
    std::vector<Json> types;
    for (const auto &name : definition.interface_order) {
      const auto &port = definition.interface.at(name);
      if (!locals.insert(name).second)
        fail("resolve.duplicate-name",
             "interface '" + name + "' collides with a parameter", port.span);
      if (!port.type.is_null()) {
        type(port.type);
        nets[name] = port.type;
        types.push_back(port.type);
      }
    }
    auto params = parameters(definition);
    MetadataReader reader(project, registry, definition.body_module, params);
    for (const auto *node : definition.body) {
      std::string name = node->at("name").at("text");
      if (!locals.insert(name).second)
        fail("resolve.duplicate-name", "duplicate local name '" + name + "'",
             node->at("name").at("span"));
      if (node->at("node") == "Net") {
        auto value =
            object("value", node->at("type"), "module", definition.body_module);
        type(value);
        metadata_expressions(reader.read(node->at("fields"), "net"), params);
        nets[name] = value;
        types.push_back(std::move(value));
      }
    }
    auto errors = Json::array();
    for (const auto &parameter : definition.parameters.parameters)
      if (parameter.default_value)
        collect(errors, [&] {
          expression(parameter.default_value->expression,
                     parameter.default_value->module, params);
        });
    metadata_expressions(definition.metadata, params, &errors,
                         &definition.metadata_order);
    for (const auto &name : definition.interface_order)
      metadata_expressions(definition.interface.at(name).metadata, params,
                           &errors,
                           &definition.interface.at(name).metadata_order);
    for (const auto &value : types)
      collect(errors, [&] {
        const Json *expr = &value.at("value");
        while (expr->at("node") == "Subscript") {
          const auto &dimension = expr->at("selection");
          if (!dimension.at("stop").is_null())
            fail("resolve.dimension", "type dimensions cannot be slices",
                 dimension.at("span"));
          index(dimension, value.at("module"), params);
          expr = &expr->at("base");
        }
      });
    for (const auto *node : definition.body)
      if (node->at("node") == "Instance")
        collect(errors, [&] { instance(*node, definition, nets); });
    if (!errors.empty())
      throw DiagnosticsFailure(std::move(errors));
  }
  void bundle_cycles(const std::vector<Definition *> &order) const {
    std::set<DefinitionKey> complete;
    for (const auto *definition : order) {
      if (definition->kind != "bundle" || complete.contains(definition->key))
        continue;
      std::set<DefinitionKey> active;
      std::vector<std::pair<DefinitionKey, bool>> stack{
          {definition->key, false}};
      while (!stack.empty()) {
        auto [key, finish] = stack.back();
        stack.pop_back();
        if (finish) {
          active.erase(key);
          complete.insert(key);
          continue;
        }
        const auto &current = definitions.at(key);
        if (active.contains(key))
          fail("resolve.bundle-cycle",
               "recursive bundle fields cannot form a finite net type",
               current.source->at("span"));
        if (complete.contains(key))
          continue;
        active.insert(key);
        stack.emplace_back(key, true);
        for (const auto &name : current.interface_order)
          if (const auto *field_type = type(current.interface.at(name).type))
            stack.emplace_back(field_type->key, false);
      }
    }
  }
};
} // namespace
const Definition *type_definition(const Project &project,
                                  const RegistryDescription &registry,
                                  const Definitions &definitions,
                                  const Json &wrapped) {
  const Json *expr = &wrapped.at("value");
  while (expr->at("node") == "Subscript")
    expr = &expr->at("base");
  const std::string module = wrapped.at("module");
  const auto &symbols = project.modules.at(module).symbols;
  auto path = names(*expr);
  auto symbol = symbols.find(spelling(path));
  if (symbol != symbols.end() && symbol->second.declaration)
    return &definitions.at(
        definition_symbol(project, module, path, {"bundle", "nettype"}));
  if (expr->at("node") == "Member") {
    auto space = symbols.find(spelling(names(expr->at("base"))));
    const auto &values = registry.data.at("enums").at("nettype");
    if (space == symbols.end() || space->second.module != "noscheme" ||
        space->second.name != "nettype" ||
        std::ranges::find(values, expr->at("name").at("text")) == values.end())
      fail("metadata.enum", "expected an explicitly imported nettype value",
           expr->at("span"));
    return nullptr;
  }
  if (expr->at("node") != "Name")
    fail("resolve.net-type", "expected a nettype value, net type, or bundle",
         expr->at("span"));
  return &definitions.at(
      definition_symbol(project, module, path, {"bundle", "nettype"}));
}

std::set<std::string> possible_selections(const Json &wrapped) {
  return possible(wrapped);
}
std::vector<std::string>
metadata_field_order(const Json &values, const MetadataOrder *orders,
                     const std::vector<std::string> &path) {
  return metadata_keys(values, orders, path);
}
bool attached_role(const Definition &definition, const std::string &pin) {
  const auto &metadata = definition.interface.at(pin).metadata;
  return definition.kind == "component" &&
         (!metadata.contains("role") ||
          std::ranges::includes(std::set<std::string>{"passive"},
                                possible(metadata.at("role"))));
}
Definitions resolve_all(const Project &project,
                        const RegistryDescription &registry,
                        std::size_t max_pins,
                        std::size_t max_expression_depth) {
  auto definitions = resolve_interfaces(project, registry, max_pins);
  resolve_bodies(project, registry, definitions, max_pins,
                 max_expression_depth);
  return definitions;
}
void resolve_bodies(const Project &project, const RegistryDescription &registry,
                    Definitions &definitions, std::size_t max_pins,
                    std::size_t max_expression_depth) {
  std::vector<Definition *> order;
  for (auto &[key, definition] : definitions)
    order.push_back(&definition);
  std::ranges::sort(order, {}, &Definition::resolution_order);
  BodyResolver resolver(project, registry, definitions, max_pins,
                        max_expression_depth);
  auto errors = Json::array();
  for (auto *definition : order)
    collect(errors, [&] { resolver.expand(*definition); });
  for (const auto *definition : order)
    collect(errors, [&] { resolver.body(*definition); });
  if (!errors.empty()) {
    auto unique = Json::array();
    for (const auto &error : errors)
      if (std::ranges::find(unique, error) == unique.end())
        unique.push_back(error);
    throw DiagnosticsFailure(std::move(unique));
  }
  resolver.bundle_cycles(order);
}
} // namespace noscheme
