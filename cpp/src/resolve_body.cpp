#include "noscheme/resolve.hpp"
#include "noscheme/validation.hpp"
#include <algorithm>
#include <iterator>

namespace noscheme {
namespace {
using ast::as;
using ast::Kind;
[[noreturn]] void fail(const std::string &code, const std::string &message,
                       const Json &span, const Json &related = Json::array()) {
  const Symbols symbols;
  Evaluator(symbols).error(code, message, span, related);
}
[[noreturn]] void fail(const std::string &code, const std::string &message,
                       const Span &span, const Json &related = Json::array()) {
  fail(code, message, span.json(), related);
}
template <typename F> void collect(Json &errors, F &&operation) {
  try {
    operation();
  } catch (const DiagnosticsFailure &failure) {
    for (const auto &diagnostic : failure.diagnostics)
      errors.push_back(diagnostic);
  }
}
std::set<std::string> parameters(const Definition &definition) {
  std::set<std::string> result;
  for (const auto &parameter : definition.parameters.parameters)
    result.insert(parameter.name);
  return result;
}
const MetaFields &choices(const Definition &definition) {
  static const MetaFields none;
  auto *declared = meta_value(definition.metadata, "erc_choices");
  return declared ? declared->fields() : none;
}
// The alternatives a role or choice binding can take.
std::set<std::string> possible(const Meta &wrapped) {
  if (auto *selection = wrapped.value.get_if<MetaFields>()) {
    std::set<std::string> result;
    for (const auto &[name, item] : selection->at("cases").value.fields())
      result.insert(item.value.string());
    return result;
  }
  auto values = meta_strings(wrapped.value);
  return {values.begin(), values.end()};
}
void selection(const Meta &value, const MetaFields &declared) {
  auto *fields = value.value.get_if<MetaFields>();
  if (!fields)
    return;
  const auto &choice = fields->at("choice");
  const auto &name = choice.value.string();
  if (!declared.contains(name))
    fail("erc.unknown_choice", "unknown local ERC choice '" + name + "'",
         choice.span);
  std::set<std::string> cases;
  for (const auto &[key, unused] : fields->at("cases").value.fields())
    cases.insert(key);
  if (cases != possible(declared.at(name)))
    fail("erc.choice_cases",
         "cases must cover every declared choice alternative exactly",
         fields->at("cases").span);
}
std::vector<std::string>
metadata_keys(const MetaFields &values, const MetadataOrder *orders = nullptr,
              const std::vector<std::string> &path = {}) {
  std::vector<std::string> keys;
  for (const auto &[key, unused] : values)
    keys.push_back(key);
  // Fresh mappings follow source order; inherited mappings retain the
  // parent's slot for an overridden field, even when that override is in
  // another file.
  std::stable_sort(keys.begin(), keys.end(),
                   [&](const std::string &a, const std::string &b) {
                     return values.at(a).span.start < values.at(b).span.start;
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
  using Nets = std::map<std::string, ScopedExpression>;
  const Project &project;
  const RegistryDescription &registry;
  Definitions &definitions;
  std::size_t max_pins, max_depth;

  const Definition &target(const std::string &module,
                           const std::vector<ast::Name> &path,
                           const std::vector<std::string> &kinds) const {
    return definitions.at(definition_symbol(project, module, path, kinds));
  }
  const Definition *type(const ScopedExpression &value) const {
    return type_definition(project, registry, definitions, value);
  }
  template <class Selectors>
  void select(ScopedExpression value, const Selectors &selectors) const {
    for (const auto &item : selectors) {
      const ast::Node &selector = *item;
      if (selector.kind == Kind::Index)
        continue;
      auto definition = type(value);
      const auto &name = as<ast::Name>(selector).text;
      if (!definition || !definition->interface.contains(name))
        fail("resolve.private", "'" + name + "' is not a public bundle field",
             selector.span);
      value = definition->interface.at(name).type;
    }
  }
  void expression(const ast::Node &expr, const std::string &module,
                  const std::set<std::string> &locals, std::size_t depth = 0,
                  const Span *origin = nullptr) const {
    if (depth > max_depth)
      fail("resolve.expression-limit",
           "expression exceeds configured depth limit",
           origin ? *origin : expr.span);
    auto child = [&](const ast::Node &value, const Span *where = nullptr) {
      expression(value, module, locals, depth + 1, where);
    };
    switch (expr.kind) {
    case Kind::Number:
    case Kind::String:
      return;
    case Kind::Name: {
      const auto &name = as<ast::Name>(expr).text;
      if (!locals.contains(name))
        fail("resolve.expression-name", "unknown parameter '" + name + "'",
             expr.span);
      return;
    }
    case Kind::Unary:
      child(*as<ast::Unary>(expr).operand);
      return;
    case Kind::Group:
      child(*as<ast::Group>(expr).expression);
      return;
    case Kind::Binary:
      child(*as<ast::Binary>(expr).left);
      child(*as<ast::Binary>(expr).right);
      return;
    case Kind::Call: {
      const auto &call = as<ast::Call>(expr);
      const auto *callee = as<ast::Member>(call.callee);
      if (!callee)
        break;
      const auto &method = callee->name.text;
      if (method == "format")
        child(*callee->base);
      else {
        auto path = ast::name_chain(*callee->base);
        if (path.empty())
          fail("resolve.helper", "arbitrary method calls are not supported",
               callee->span);
        const auto &symbols = project.modules.at(module).symbols;
        auto symbol = symbols.find(ast::dotted(path));
        if (locals.contains(path.front()->text) || symbol == symbols.end() ||
            symbol->second.module != "noscheme" ||
            symbol->second.name != "num" ||
            !std::set<std::string>{"int", "ceil", "floor", "round"}.contains(
                method))
          fail("resolve.helper", "unknown or unimported numeric helper",
               callee->span);
      }
      for (const auto &argument : call.arguments)
        child(*argument);
      for (const auto &argument : call.keywords)
        child(*argument.value, &argument.span);
      return;
    }
    default:
      break;
    }
    fail("resolve.expression",
         "unsupported expression in parameter/value context", expr.span);
  }
  void metadata_expressions(const MetaFields &values,
                            const std::set<std::string> &locals,
                            Json *errors = nullptr,
                            const MetadataOrder *orders = nullptr,
                            std::vector<std::string> path = {}) const {
    for (const auto &key : metadata_keys(values, orders, path)) {
      const auto &wrapped = values.at(key);
      const auto &value = wrapped.value;
      const auto *constrained = value.get_if<Constrained>();
      const auto *expr =
          constrained ? constrained->expression : value.expression();
      auto check = [&] {
        expression(*expr, wrapped.module, locals, 0, &wrapped.span);
      };
      if (expr) {
        if (errors)
          collect(*errors, check);
        else
          check();
      } else if (auto *fields = value.get_if<MetaFields>()) {
        auto nested = path;
        nested.push_back(key);
        metadata_expressions(*fields, locals, errors, orders,
                             std::move(nested));
      }
    }
  }
  void index(const ast::Index &selection, const std::string &module,
             const std::set<std::string> &locals) const {
    expression(*selection.start, module, locals, 0, &selection.span);
    if (selection.stop)
      expression(*selection.stop, module, locals, 0, &selection.span);
  }
  void binding(const ast::Binding &binding, const Definition &part,
               const Definition &owner, const Nets &nets) const {
    const auto &endpoint = binding.endpoint;
    auto locals = parameters(owner);
    if (!endpoint.head.items.empty() && part.kind != "component")
      fail("resolve.pin-list",
           "pin lists select physical component pins, not block ports",
           endpoint.head.span);
    for (const auto &selector : endpoint.instance_indices)
      index(selector, owner.body_module, locals);
    for (const auto &selector : endpoint.selectors)
      if (auto *selection = as<ast::Index>(selector))
        index(*selection, owner.body_module, locals);
    for (const auto &name : physical_keys(endpoint.head, max_pins)) {
      auto alias = part.aliases.find(name);
      if (alias == part.aliases.end())
        fail("resolve.private",
             "'" + name + "' is not a public pin or port of " + part.key.second,
             endpoint.head.span);
      const auto &port = part.interface.at(alias->second);
      if (!endpoint.selectors.empty()) {
        if (!port.type.expression)
          fail("resolve.private", "physical pins have no fields or elements",
               endpoint.span);
        select(port.type, endpoint.selectors);
      }
    }
    auto [head, selectors] = ast::reference_parts(*binding.target);
    for (const auto *selector : selectors)
      if (auto *selection = as<ast::Index>(selector))
        index(*selection, owner.body_module, locals);
    const auto &name = head->text;
    if (name == "global") {
      if (selectors.empty() || selectors.front()->kind != Kind::Name)
        fail("resolve.global", "global references require an explicit net name",
             head->span);
      return;
    }
    if (name == "no_connect" && selectors.empty()) {
      if (binding.target->kind == Kind::Call)
        fail("resolve.flat", "flat() takes an array net, not no_connect",
             head->span);
      return;
    }
    if (!nets.contains(name))
      fail("resolve.net", "'" + name + "' is not a declared caller net or port",
           head->span);
    if (nets.at(name).expression)
      select(nets.at(name), selectors);
  }
  void instance_metadata(const ast::Instance &node,
                         const std::vector<ast::Section> &sections,
                         const Definition &part,
                         const Definition &owner) const {
    auto locals = parameters(owner);
    MetadataReader reader(project, registry, owner.body_module, locals);
    auto fields = ast::field_list(ast::section_entries(sections, "metadata"));
    std::vector<const ast::Field *> ordinary;
    for (const auto *field : fields)
      if (field->key.first.text != "pins")
        ordinary.push_back(field);
    auto values = reader.read(ordinary, "instance");
    if (part.kind != "component")
      for (const auto &[name, unused] : values)
        if (!std::set<std::string>{"custom", "identity", "erc_choices"}
                 .contains(name))
          fail("metadata.scope",
               "physical-part metadata cannot be applied to a reusable block",
               node.span);
    const auto &declared = choices(owner);
    if (auto *bindings = meta_value(values, "erc_choices")) {
      const auto &permitted = choices(part);
      for (const auto &name : metadata_keys(bindings->fields())) {
        const auto &value = bindings->fields().at(name);
        if (!permitted.contains(name))
          fail("erc.unknown_choice", "unknown public ERC choice '" + name + "'",
               value.span);
        selection(value, declared);
        if (!std::ranges::includes(possible(permitted.at(name)),
                                   possible(value)))
          fail("erc.choice_value",
               "selection is outside alternatives for '" + name + "'",
               value.span);
      }
    }
    metadata_expressions(values, locals);
    for (const auto *group : fields) {
      if (group->key.first.text != "pins")
        continue;
      if (group->value || group->key.last)
        fail("metadata.value",
             "instance pins metadata requires an indented mapping",
             group->span);
      std::map<std::string, const Span *> seen;
      for (const auto &field : group->fields) {
        if (!field.key.items.empty() && part.kind != "component")
          fail("resolve.pin-list",
               "pin lists select physical component pins, not block ports",
               field.key.span);
        auto annotations = reader.pin(field, "instance_pin");
        metadata_expressions(annotations, locals);
        for (const auto &key : {"role", "external_role"})
          if (annotations.contains(key))
            selection(annotations.at(key), declared);
        if ((annotations.contains("external_role") ||
             annotations.contains("external_drive")) &&
            part.kind == "component")
          fail("metadata.role",
               "write role on a component pin: on a passive or undeclared "
               "contact it describes the attached equipment",
               annotations
                   .at(annotations.contains("external_role") ? "external_role"
                                                             : "external_drive")
                   .span);
        for (const auto &name : physical_keys(field.key, max_pins)) {
          auto alias = part.aliases.find(name);
          if (alias == part.aliases.end())
            fail("resolve.pin", "unknown pin or port '" + name + "'",
                 field.span);
          const auto &pin = alias->second;
          if (seen.contains(pin))
            fail("resolve.duplicate-pin",
                 "duplicate annotation for '" + pin + "'", field.span,
                 Json::array({object("message", "previous annotation", "span",
                                     seen.at(pin)->json())}));
          seen[pin] = &field.span;
          const auto &metadata = part.interface.at(pin).metadata;
          validate_drive(merge_metadata(metadata, annotations));
          if (annotations.contains("drive") && metadata.contains("drive") &&
              annotations.at("drive").value.string() !=
                  metadata.at("drive").value.string())
            fail("metadata.constraint",
                 "instance drive differs from the definition drive",
                 annotations.at("drive").span);
          if (annotations.contains("role") && metadata.contains("role")) {
            auto permitted = possible(metadata.at("role"));
            bool attached = attached_role(part, pin);
            if (!attached && !std::ranges::includes(
                                 permitted, possible(annotations.at("role"))))
              fail("metadata.constraint",
                   "instance role is outside the definition permitted roles",
                   annotations.at("role").span);
          }
        }
      }
    }
  }
  // Element overrides check the instance again with the element's sections.
  void instance(const ast::Instance &node,
                const std::vector<ast::Section> &sections,
                const Definition &owner, const Nets &nets) const {
    const auto &part =
        target(owner.body_module, node.component, {"component", "block"});
    if (part.metadata.contains("configurations"))
      fail("project.root-only", "only the root block may define configurations",
           node.span);
    for (const auto *entry : part.body)
      if (auto *net = as<ast::Net>(entry); net && net->global)
        fail("project.root-only", "only the root block may declare globals",
             node.span);
    auto locals = parameters(owner), permitted = parameters(part);
    auto errors = Json::array();
    for (const auto &dimension : node.dimensions)
      collect(errors, [&] {
        if (dimension.stop)
          fail("resolve.dimension", "instance dimensions cannot be slices",
               dimension.span);
        expression(*dimension.start, owner.body_module, locals, 0,
                   &dimension.span);
      });
    for (const auto &entry : ast::section_entries(sections, "params"))
      collect(errors, [&] {
        const auto &argument = as<ast::Argument>(*entry);
        if (!permitted.contains(argument.name.text))
          fail("resolve.argument",
               "unknown parameter '" + argument.name.text + "'",
               argument.name.span);
        expression(*argument.value, owner.body_module, locals, 0,
                   &argument.span);
      });
    for (const auto &connection : ast::section_entries(sections, "connections"))
      collect(errors, [&] {
        binding(as<ast::Binding>(*connection), part, owner, nets);
      });
    collect(errors, [&] { instance_metadata(node, sections, part, owner); });
    for (const auto &entry : ast::section_entries(sections, "elements"))
      collect(errors, [&] {
        const auto &element = as<ast::ElementOverride>(*entry);
        if (node.dimensions.empty() ||
            element.indices.size() != node.dimensions.size())
          fail("array.element-rank",
               "element overrides require one index per array dimension",
               element.span);
        for (const auto &index : element.indices) {
          auto *number = as<ast::Number>(index.start);
          if (index.stop || !number || !number->integer)
            fail("array.element-index",
                 "element indices must be nonnegative integer literals, "
                 "without slices",
                 index.span);
        }
        for (const auto *field : ast::field_list(
                 ast::section_entries(element.sections, "metadata")))
          if (field->key.first.text == "identity" ||
              field->key.first.text == "ref")
            fail("array.element-identity",
                 "per-element identity and ref overrides are not supported",
                 field->span);
        instance(node, element.sections, owner, nets);
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
  // A connection from a pin or port name to a target, located at span.
  static ast::Ptr connection(const Span &span, const std::string &pin,
                             ast::Ptr target) {
    auto binding = std::make_shared<ast::Binding>();
    binding->span = span;
    auto &head = binding->endpoint.head;
    binding->endpoint.span = head.span = head.first.span = span;
    head.first.text = head.first.spelling = pin;
    binding->target = std::move(target);
    return binding;
  }
  std::vector<ast::Ptr> positional_bindings(const ast::Instance &instance,
                                            const Definition &part) const {
    if (instance.positional.empty())
      return {};
    auto *interchangeable = meta_value(part.metadata, "interchangeable");
    auto pins = interchangeable ? meta_strings(*interchangeable)
                                : std::vector<std::string>{};
    if (pins.empty())
      fail("resolve.positional",
           part.key.second +
               " declares no interchangeable pins; connect pins by name",
           instance.positional.front()->span);
    if (pins.size() != instance.positional.size())
      fail("resolve.positional",
           part.key.second + " has " + std::to_string(pins.size()) +
               " interchangeable pins; give a net or no_connect for each",
           instance.span);
    std::vector<ast::Ptr> result;
    for (std::size_t i = 0; i < pins.size(); ++i)
      result.push_back(connection(instance.positional[i]->span, pins[i],
                                  instance.positional[i]));
    return result;
  }
  // `*` binds each remaining pin or port, by its name or an alias, to the
  // caller's net or port of the same name.
  std::vector<ast::Ptr>
  wildcard_bindings(const ast::Instance &instance, const Definition &part,
                    const std::vector<ast::Ptr> &entries,
                    const std::set<std::string> &scope) const {
    std::set<std::string> bound;
    for (const auto &entry : entries)
      for (const auto &name :
           physical_keys(as<ast::Binding>(*entry).endpoint.head, max_pins)) {
        auto alias = part.aliases.find(name);
        bound.insert(alias == part.aliases.end() ? name : alias->second);
      }
    std::map<std::string, std::vector<std::string>> names;
    for (const auto &[alias, pin] : part.aliases)
      names[pin].push_back(alias);
    const auto &span = *instance.wildcard;
    std::vector<ast::Ptr> result;
    for (const auto &pin : part.interface_order) {
      std::vector<std::string> matches;
      for (const auto &name : names[pin])
        if (scope.contains(name))
          matches.push_back(name);
      if (bound.contains(pin) || matches.empty())
        continue;
      if (matches.size() > 1) {
        std::string listed;
        for (const auto &name : matches)
          listed += (listed.empty() ? "" : ", ") + name;
        fail("resolve.wildcard",
             "* matches pin '" + pin + "' through several names: " + listed,
             span);
      }
      auto target = std::make_shared<ast::Name>();
      target->span = span;
      target->text = matches.front();
      result.push_back(connection(span, matches.front(), std::move(target)));
    }
    if (result.empty())
      fail("resolve.wildcard", "* matches no caller net or port", span);
    return result;
  }
  // Turn an unnamed parameter, positional nets, and `*` into ordinary entries.
  // An unnamed parameter sets the target's one parameter without a default.
  void expand(Definition &definition) const {
    // Build transactionally: a failed expansion leaves this definition
    // unchanged.
    std::set<std::string> scope;
    for (const auto *node : definition.body)
      if (auto *net = as<ast::Net>(node); net && !net->global)
        scope.insert(net->name.text);
    if (definition.kind == "block")
      for (const auto &[name, unused] : definition.interface)
        scope.insert(name);
    auto body = definition.body;
    std::vector<std::shared_ptr<const ast::Node>> owned;
    for (auto &node : body) {
      auto *instance = as<ast::Instance>(node);
      if (!instance || (instance->positional.empty() && !instance->wildcard &&
                        !instance->parameter))
        continue;
      const auto &part = target(definition.body_module, instance->component,
                                {"component", "block"});
      auto copy = std::make_shared<ast::Instance>(*instance);
      ast::Section connections;
      connections.span = instance->span;
      connections.name = "connections";
      connections.entries =
          ast::section_entries(instance->sections, "connections");
      std::ranges::move(positional_bindings(*instance, part),
                        std::back_inserter(connections.entries));
      if (instance->wildcard)
        std::ranges::move(
            wildcard_bindings(*instance, part, connections.entries, scope),
            std::back_inserter(connections.entries));
      copy->sections.clear();
      for (const auto &section : instance->sections)
        if (section.name != "connections")
          copy->sections.push_back(section);
      if (const auto &value = instance->parameter) {
        auto found = std::ranges::find(copy->sections, std::string("params"),
                                       &ast::Section::name);
        ast::Section params;
        params.span = value->span;
        params.name = "params";
        if (found != copy->sections.end()) {
          params = *found;
          copy->sections.erase(found);
        }
        std::set<std::string> named;
        for (const auto &entry : params.entries)
          named.insert(as<ast::Argument>(*entry).name.text);
        auto argument = std::make_shared<ast::Argument>();
        argument->span = argument->name.span = value->span;
        argument->name.text = unnamed_target(part.parameters, *value, named);
        argument->value = value;
        params.entries.insert(params.entries.begin(), std::move(argument));
        copy->sections.push_back(std::move(params));
      }
      copy->sections.push_back(std::move(connections));
      copy->positional.clear();
      copy->wildcard.reset();
      copy->parameter = nullptr;
      node = copy.get();
      owned.push_back(std::move(copy));
    }
    definition.body = std::move(body);
    definition.expanded_body = std::move(owned);
  }
  void body(const Definition &definition) const {
    auto locals = parameters(definition);
    Nets nets;
    std::vector<ScopedExpression> types;
    for (const auto &name : definition.interface_order) {
      const auto &port = definition.interface.at(name);
      if (!locals.insert(name).second)
        fail("resolve.duplicate-name",
             "interface '" + name + "' collides with a parameter", port.span);
      if (port.type.expression) {
        type(port.type);
        nets[name] = port.type;
        types.push_back(port.type);
      }
    }
    auto params = parameters(definition);
    MetadataReader reader(project, registry, definition.body_module, params);
    for (const auto *node : definition.body) {
      const auto *net = as<ast::Net>(node);
      const auto &name = net ? net->name : as<ast::Instance>(*node).name;
      if (!locals.insert(name.text).second)
        fail("resolve.duplicate-name",
             "duplicate local name '" + name.text + "'", name.span);
      if (net) {
        ScopedExpression value{net->type.get(), definition.body_module};
        type(value);
        metadata_expressions(reader.read(net->fields, "net"), params);
        nets[name.text] = value;
        types.push_back(value);
      } else if (!project.modules.at(definition.body_module)
                      .symbols.contains(
                          ast::dotted(as<ast::Instance>(*node).component))) {
        // An unknown type may have been meant as a net; its instance check
        // reports it, and connections to it are not checked again.
        nets[name.text] = ScopedExpression{};
      }
    }
    auto errors = Json::array();
    for (const auto &parameter : definition.parameters.parameters)
      if (parameter.default_value)
        collect(errors, [&] {
          expression(*parameter.default_value->expression,
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
        const auto *expr = value.expression;
        while (auto *subscript = as<ast::Subscript>(expr)) {
          const auto &dimension = subscript->selection;
          if (dimension.stop)
            fail("resolve.dimension", "type dimensions cannot be slices",
                 dimension.span);
          index(dimension, value.module, params);
          expr = subscript->base.get();
        }
      });
    for (const auto *node : definition.body)
      if (auto *instance = as<ast::Instance>(node))
        collect(errors, [&] {
          this->instance(*instance, instance->sections, definition, nets);
        });
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
               current.source->span);
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
                                  const ScopedExpression &type) {
  const ast::Node *expr = type.expression;
  if (!expr)
    return nullptr;
  while (auto *subscript = as<ast::Subscript>(expr))
    expr = subscript->base.get();
  const auto &module = type.module;
  const auto &symbols = project.modules.at(module).symbols;
  auto path = ast::name_chain(*expr);
  auto symbol = symbols.find(ast::dotted(path));
  if (symbol != symbols.end() && symbol->second.declaration)
    return &definitions.at(
        definition_symbol(project, module, path, {"bundle", "nettype"}));
  if (auto *member = as<ast::Member>(expr)) {
    auto space = symbols.find(ast::expression_path(*member->base));
    const auto &values = registry.data.at("enums").at("net");
    if (space == symbols.end() || space->second.module != "noscheme" ||
        space->second.name != "net" ||
        std::ranges::find(values, Json(member->name.text)) == values.end())
      fail("metadata.enum", "expected an explicitly imported net value",
           expr->span);
    return nullptr;
  }
  if (expr->kind != Kind::Name)
    fail("resolve.net-type",
         "expected a builtin net value, net type, or bundle", expr->span);
  return &definitions.at(
      definition_symbol(project, module, path, {"bundle", "nettype"}));
}

std::set<std::string> possible_selections(const Meta &wrapped) {
  return possible(wrapped);
}
std::vector<std::string>
metadata_field_order(const MetaFields &values, const MetadataOrder *orders,
                     const std::vector<std::string> &path) {
  return metadata_keys(values, orders, path);
}
void validate_drive(const MetaFields &metadata) {
  const std::set<std::string> allowed{"passive", "digital_input",
                                      "digital_output", "digital_bidirectional",
                                      "unchecked"};
  for (const std::string prefix : {"", "external_"}) {
    auto drive = metadata.find(prefix + "drive"),
         role = metadata.find(prefix + "role");
    if (drive != metadata.end() && role != metadata.end() &&
        !std::ranges::includes(allowed, possible(role->second)))
      fail("metadata.drive",
           "directional drive requires a digital or passive role",
           drive->second.span);
  }
}
bool attached_role(const Definition &definition, const std::string &pin) {
  const auto &metadata = definition.interface.at(pin).metadata;
  return definition.kind == "component" && !metadata.contains("drive") &&
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
    UniqueDiagnostics unique;
    unique.add_all(errors);
    throw DiagnosticsFailure(unique.take());
  }
  const auto &root = definitions.at({project.board.module, project.board.name});
  if (!root.interface.empty())
    fail("project.root-ports", "the root block must not have public ports",
         root.source->span);
  resolver.bundle_cycles(order);
}
} // namespace noscheme
