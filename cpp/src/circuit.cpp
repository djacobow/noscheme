#include "noscheme/circuit.hpp"
#include "noscheme/validation.hpp"
#include <algorithm>
#include <cmath>
#include <functional>
#include <numeric>

namespace noscheme {
std::string path_text(const InstancePath &path) {
  std::string result;
  for (const auto &part : path)
    if (auto index = std::get_if<std::size_t>(&part))
      result += "[" + std::to_string(*index) + "]";
    else {
      if (!result.empty())
        result += '.';
      result += std::get<std::string>(part);
    }
  return result;
}
Json to_json(const InstancePath &path) {
  auto result = Json::array();
  for (const auto &part : path)
    std::visit([&](const auto &value) { result.push_back(value); }, part);
  return result;
}
std::string_view binding_name(BindingState state) {
  switch (state) {
  case BindingState::connected:
    return "connected";
  case BindingState::no_connect:
    return "no_connect";
  default:
    return "omitted";
  }
}
namespace {
using Path = InstancePath;
Path append(Path path, PathPart segment) {
  path.push_back(std::move(segment));
  return path;
}
Path concat(Path path, const Path &suffix) {
  path.insert(path.end(), suffix.begin(), suffix.end());
  return path;
}
bool prefix(const Path &path, const Path &parent) {
  return path.size() >= parent.size() &&
         std::equal(parent.begin(), parent.end(), path.begin());
}
std::set<std::string> local_names(const Environment &environment) {
  std::set<std::string> result;
  for (const auto &[name, value] : environment)
    result.insert(name);
  return result;
}
const MetaFields &fields_of(const MetaFields &metadata,
                            const std::string &key) {
  static const MetaFields none;
  auto *value = meta_value(metadata, key);
  return value ? value->fields() : none;
}
// Expansion trees: instances and bundles have fields, arrays have items, and
// scalars name a leaf.
enum class NodeKind { scalar, array, bundle, instance };
struct Node;
using Tree = std::shared_ptr<const Node>;
struct Node {
  NodeKind kind;
  std::string type;
  std::size_t leaf = 0;
  std::vector<Tree> items;
  std::map<std::string, Tree> fields;
};
// Combinations of board choices checked when no configurations are named.
constexpr std::size_t max_configurations = 64;
Tree array_node(std::vector<Tree> items) {
  return std::make_shared<Node>(
      Node{NodeKind::array, "", 0, std::move(items), {}});
}
const ast::Node &deref(const ast::Node &node) { return node; }
const ast::Node &deref(const ast::Node *node) { return *node; }
const ast::Node &deref(const ast::Ptr &node) { return *node; }
class Elaborator {
  const Project &project;
  const RegistryDescription &registry;
  const Definitions &definitions;
  ExpansionLimits limits;
  Limits expression_limits;
  Circuit circuit;
  Json instance_path = nullptr;
  std::vector<std::size_t> parents, sizes;
  std::vector<std::set<Path>> supports;
  std::map<std::string, Tree> globals;
  // Ports of each part and block, in the order of circuit.parts/blocks.
  std::vector<std::map<std::string, Tree>> part_ports, block_ports;
  std::map<std::string, Span> identities;
  std::set<std::string> references;
  std::size_t instance_count = 0;

  [[noreturn]] void error(const std::string &code, const std::string &message,
                          const Json &span,
                          const Json &related = Json::array()) const {
    const Symbols symbols;
    Evaluator evaluator(symbols, expression_limits);
    evaluator.instance_path = instance_path;
    evaluator.error(code, message, span, related);
  }
  [[noreturn]] void error(const std::string &code, const std::string &message,
                          const Span &span,
                          const Json &related = Json::array()) const {
    error(code, message, span.json(), related);
  }
  void collect_warnings(const Json &warnings) {
    for (const auto &warning : warnings)
      circuit.warnings.push_back(warning);
  }
  template <typename F>
  auto evaluating(const std::string &module, F &&operation) {
    const Symbols empty;
    Evaluator evaluator(module.empty() ? empty
                                       : project.modules.at(module).symbols,
                        expression_limits);
    evaluator.instance_path = instance_path;
    try {
      auto result = operation(evaluator);
      collect_warnings(evaluator.warnings);
      return result;
    } catch (const DiagnosticsFailure &) {
      collect_warnings(evaluator.warnings);
      throw;
    }
  }
  template <typename F> auto binding_parameters(F &&operation) {
    ParameterEvaluator evaluator(project, expression_limits);
    evaluator.instance_path = instance_path;
    try {
      auto result = operation(evaluator);
      collect_warnings(evaluator.warnings);
      return result;
    } catch (const DiagnosticsFailure &) {
      collect_warnings(evaluator.warnings);
      throw;
    }
  }
  Value evaluate(const ast::Node &expr, const std::string &module,
                 const Environment &env) {
    return evaluating(module,
                      [&](Evaluator &e) { return e.evaluate(expr, env); });
  }
  std::size_t root(std::size_t leaf) {
    while (parents[leaf] != leaf) {
      parents[leaf] = parents[parents[leaf]];
      leaf = parents[leaf];
    }
    return leaf;
  }
  void join(std::size_t child, std::size_t caller) {
    auto first = root(child), second = root(caller);
    if (first == second)
      return;
    if (sizes[first] > sizes[second])
      std::swap(first, second);
    parents[first] = second;
    sizes[second] += sizes[first];
    supports[second].insert(supports[first].begin(), supports[first].end());
    supports[first].clear();
  }
  Tree new_leaf(const Path &path, const Path &scope, const std::string &kind,
                std::optional<std::string> type, MetaFields metadata,
                const Span &span,
                const std::optional<ErcSelections> &selections) {
    auto &leaves = circuit.leaves;
    if (leaves.size() >= limits.leaves)
      error("circuit.limit", "expanded net/pin count exceeds configured limit",
            span);
    auto number = leaves.size();
    parents.push_back(number);
    sizes.push_back(1);
    auto *placeholder = meta_value(metadata, "placeholder");
    bool support = kind == "pin" || metadata.contains("external_role") ||
                   (placeholder && placeholder->is<bool>() &&
                    *placeholder->get_if<bool>());
    supports.push_back(support ? std::set<Path>{scope} : std::set<Path>{});
    Leaf leaf;
    leaf.path = path;
    leaf.scope = scope;
    leaf.kind = kind;
    leaf.type = std::move(type);
    leaf.metadata = std::move(metadata);
    leaf.selections = selections;
    leaf.span = span;
    leaves.push_back(std::move(leaf));
    return std::make_shared<Node>(Node{NodeKind::scalar, "", number, {}, {}});
  }
  std::vector<std::size_t> node_leaves(const Tree &node) const {
    std::vector<std::size_t> result;
    std::function<void(const Tree &)> visit = [&](const Tree &child) {
      if (child->kind == NodeKind::scalar)
        result.push_back(child->leaf);
      else if (child->kind == NodeKind::array)
        for (const auto &item : child->items)
          visit(item);
      else
        for (const auto &[name, item] : child->fields)
          visit(item);
    };
    visit(node);
    return result;
  }
  template <class Selections>
  std::vector<std::size_t> dimensions(const Selections &selections,
                                      const std::string &module,
                                      const Environment &env) {
    std::vector<std::size_t> result;
    for (const auto &item : selections) {
      const auto &s = ast::as<ast::Index>(deref(item));
      result.push_back(evaluating(module, [&](Evaluator &e) {
        return e.dimension(e.evaluate(*s.start, env), s.span);
      }));
    }
    return result;
  }
  Tree array(const std::vector<std::size_t> &shape,
             const std::function<Tree(const Path &)> &build, const Span &span) {
    if (shape.size() > limits.hierarchy_depth)
      error("circuit.limit", "array rank exceeds configured nesting limit",
            span);
    std::size_t count = 1;
    for (auto size : shape) {
      if (size > limits.leaves / count)
        error("circuit.limit", "array exceeds configured expansion limit",
              span);
      count *= size;
    }
    std::function<Tree(std::size_t, Path)> expand = [&](std::size_t depth,
                                                        Path indices) -> Tree {
      if (depth == shape.size())
        return build(indices);
      std::vector<Tree> items;
      items.reserve(shape[depth]);
      for (std::size_t i = 0; i < shape[depth]; ++i)
        items.push_back(expand(depth + 1, append(indices, i)));
      return array_node(std::move(items));
    };
    return expand(0, {});
  }
  // Evaluate numeric expressions; overrides use the caller's parameters.
  MetaFields numeric_metadata(const MetaFields &values, const Environment &env,
                              const MetaFields &overrides = {},
                              const Environment &caller = {},
                              const MetadataOrder *orders = nullptr,
                              std::vector<std::string> path = {}) {
    auto result = merge_metadata(values, overrides);
    for (const auto &key : metadata_field_order(result, orders, path)) {
      auto &record = result.at(key);
      auto &value = record.value;
      auto nested = path;
      nested.push_back(key);
      auto override_field = overrides.find(key);
      auto value_field = values.find(key);
      bool overridden = override_field != overrides.end();
      if (overridden && value_field != values.end() && value.is<MetaFields>() &&
          value_field->second.value.is<MetaFields>() &&
          override_field->second.value.is<MetaFields>()) {
        value = numeric_metadata(value_field->second.value.fields(), env,
                                 override_field->second.value.fields(), caller,
                                 orders, nested);
        continue;
      }
      const auto &scope = overridden ? caller : env;
      if (auto *constrained = value.get_if<Constrained>()) {
        auto number = evaluate(*constrained->expression, record.module, scope);
        if (std::holds_alternative<std::string>(number))
          error("expression.type", "expected a number", record.span);
        const auto &constraint = constrained->constraint;
        bool valid = std::visit(
            [&](const auto &n) {
              using T = std::decay_t<decltype(n)>;
              if constexpr (std::is_same_v<T, std::string>)
                return false;
              else
                return (constraint == "number" ||
                        (constraint == "positive" ? n > 0 : n >= 0));
            },
            number);
        if (!valid)
          error("metadata.value",
                "'" + key + "' requires " + constraint + " metadata",
                record.span);
        value = to_meta(number);
      } else if (auto *fields = value.get_if<MetaFields>())
        value = numeric_metadata(*fields, scope, {}, {}, orders, nested);
    }
    return result;
  }
  Tree typed(const ScopedExpression &value, const Environment &env,
             const Path &path, const Path &scope, const std::string &kind,
             MetaFields metadata, const Span &span,
             const std::optional<ErcSelections> &selections,
             std::size_t depth = 0) {
    if (depth > limits.type_depth || path.size() > limits.hierarchy_depth)
      error("circuit.limit", "bundle nesting exceeds configured limit", span);
    metadata = numeric_metadata(metadata, env);
    const ast::Node *expr = value.expression;
    std::vector<const ast::Node *> type_dimensions;
    while (auto *subscript = ast::as<ast::Subscript>(expr)) {
      type_dimensions.push_back(&subscript->selection);
      expr = subscript->base.get();
    }
    std::reverse(type_dimensions.begin(), type_dimensions.end());
    auto shape = dimensions(type_dimensions, value.module, env);
    if (path.size() + shape.size() > limits.hierarchy_depth)
      error("circuit.limit", "net path exceeds configured nesting limit", span);
    const auto *base = type_definition(project, registry, definitions, value);
    if (base && base->kind == "nettype")
      metadata = merge_metadata(
          numeric_metadata(base->metadata, {}, {}, {}, &base->metadata_order),
          metadata);
    return array(
        shape,
        [&](const Path &indices) -> Tree {
          auto here = concat(path, indices);
          if (!base || base->kind == "nettype") {
            std::optional<std::string> electrical;
            if (!base)
              electrical = ast::as<ast::Member>(*expr).name.text;
            else if (base->electrical.is_string())
              electrical = base->electrical.get<std::string>();
            auto leaf = new_leaf(here, scope, kind, std::move(electrical),
                                 metadata, span, selections);
            if (base)
              circuit.leaves[leaf->leaf].nettype =
                  base->key.first + "." + base->key.second;
            return leaf;
          }
          std::map<std::string, Tree> fields;
          for (const auto &[name, port] : base->interface)
            fields[name] = typed(port.type, {}, append(here, name), scope, kind,
                                 merge_metadata(metadata, port.metadata),
                                 port.span, selections, depth + 1);
          return std::make_shared<Node>(
              Node{NodeKind::bundle,
                   base->key.first + "." + base->key.second,
                   0,
                   {},
                   std::move(fields)});
        },
        span);
  }
  Tree field(const Tree &node, const std::string &name, const Span &span) {
    if (node->kind == NodeKind::array) {
      std::vector<Tree> items;
      for (const auto &child : node->items)
        items.push_back(field(child, name, span));
      return array_node(std::move(items));
    }
    if (!node->fields.contains(name))
      error("connection.field", "unknown public field '" + name + "'", span);
    return node->fields.at(name);
  }
  template <class Selectors>
  Tree select(Tree node, const Selectors &selectors, const std::string &module,
              const Environment &env) {
    for (const auto &item : selectors) {
      const auto &selector = deref(item);
      if (auto *name = ast::as<ast::Name>(&selector)) {
        node = field(node, name->text, name->span);
        continue;
      }
      const auto &selection = ast::as<ast::Index>(selector);
      if (node->kind != NodeKind::array)
        error("array.scalar-index", "cannot index a scalar endpoint or net",
              selection.span);
      auto start = evaluating(module, [&](Evaluator &e) {
        return e.index(e.evaluate(*selection.start, env), selection.span,
                       node->items.size());
      });
      if (!selection.stop)
        node = node->items[start];
      else {
        auto stop = evaluating(module, [&](Evaluator &e) {
          return e.index(e.evaluate(*selection.stop, env), selection.span,
                         node->items.size(), true);
        });
        if (stop <= start)
          error("array.slice", "slices must be nonempty and ascending",
                selection.span);
        node = array_node(std::vector<Tree>(node->items.begin() + start,
                                            node->items.begin() + stop));
      }
    }
    return node;
  }
  Tree endpoint(const Tree &instances, const ast::Endpoint &endpoint,
                const Definition &target, const std::string &module,
                const Environment &env) {
    auto selected = select(instances, endpoint.instance_indices, module, env);
    auto names = physical_keys(endpoint.head);
    std::function<Tree(const Tree &)> ports = [&](const Tree &node) -> Tree {
      if (node->kind == NodeKind::array) {
        std::vector<Tree> items;
        for (const auto &child : node->items)
          items.push_back(ports(child));
        return array_node(std::move(items));
      }
      std::vector<Tree> values;
      for (const auto &name : names)
        values.push_back(node->fields.at(target.aliases.at(name)));
      bool grouped = endpoint.head.last || !endpoint.head.items.empty();
      return select(grouped ? array_node(std::move(values)) : values.front(),
                    endpoint.selectors, module, env);
    };
    return ports(selected);
  }
  Tree reference(const ast::Node &expression,
                 const std::map<std::string, Tree> &nets,
                 const std::string &module, const Environment &env) {
    if (expression.kind == ast::Kind::Call) {
      // flat(net): the array's elements in row-major order.
      auto node = reference(*ast::as<ast::Call>(expression).arguments.front(),
                            nets, module, env);
      if (!node || node->kind != NodeKind::array)
        error("connection.flat", "flat() takes an array net", expression.span);
      std::vector<Tree> elements;
      std::function<void(const Tree &)> flatten = [&](const Tree &item) {
        if (item->kind != NodeKind::array)
          elements.push_back(item);
        else
          for (const auto &child : item->items)
            flatten(child);
      };
      flatten(node);
      return array_node(std::move(elements));
    }
    auto [head, selectors] = ast::reference_parts(expression);
    auto name = head->text;
    if (name == "no_connect" && selectors.empty())
      return nullptr;
    Tree node;
    if (name == "global") {
      const auto &selection = ast::as<ast::Name>(*selectors.front());
      selectors.erase(selectors.begin());
      name = selection.text;
      if (!globals.contains(name))
        error("connection.global",
              "global net '" + name + "' is not declared by the board",
              selection.span);
      node = globals.at(name);
    } else {
      if (!nets.contains(name))
        error("connection.net", "undeclared net '" + name + "'", head->span);
      node = nets.at(name);
    }
    return select(node, selectors, module, env);
  }
  std::vector<std::size_t> shape(Tree node) const {
    std::vector<std::size_t> result;
    while (node && node->kind == NodeKind::array) {
      result.push_back(node->items.size());
      node = node->items.front();
    }
    return result;
  }
  void connect(const Tree &left, const Tree &right, const Span &span) {
    auto lhs_shape = shape(left), rhs_shape = shape(right);
    if (right && !rhs_shape.empty() && lhs_shape != rhs_shape)
      error("connection.shape", "endpoint shape does not match net shape",
            span);
    std::function<void(const Tree &, const Tree &)> bind =
        [&](const Tree &lhs, const Tree &rhs) {
          if (lhs->kind == NodeKind::array) {
            for (std::size_t i = 0; i < lhs->items.size(); ++i)
              bind(lhs->items[i],
                   rhs && rhs->kind == NodeKind::array ? rhs->items[i] : rhs);
          } else if (lhs->kind == NodeKind::bundle) {
            if (rhs &&
                (rhs->kind != NodeKind::bundle || lhs->type != rhs->type))
              error("connection.bundle-type",
                    "whole bundles must have the same named type", span);
            for (const auto &[name, child] : lhs->fields)
              connect(child, rhs ? rhs->fields.at(name) : nullptr, span);
          } else {
            if (rhs && rhs->kind != NodeKind::scalar)
              error("connection.shape",
                    "a physical pin or scalar port requires a scalar net",
                    span);
            auto &leaf = circuit.leaves[lhs->leaf];
            if (leaf.binding != BindingState::omitted)
              error("connection.overlap", "endpoint is bound more than once",
                    span,
                    Json::array({object("message", "previous binding", "span",
                                        leaf.binding_span.json())}));
            leaf.binding =
                rhs ? BindingState::connected : BindingState::no_connect;
            leaf.binding_span = span;
            if (rhs)
              join(lhs->leaf, rhs->leaf);
          }
        };
    bind(left, right);
  }
  struct Metadata {
    MetaFields values, overrides;
    std::map<std::string, MetaFields> annotations;
  };
  Metadata effective_metadata(const Definition &definition,
                              const ast::Instance *node,
                              const ast::ElementOverride *element,
                              const std::string &module,
                              const Environment &caller,
                              const Environment &env) {
    MetaFields overrides;
    std::map<std::string, MetaFields> annotations;
    MetadataReader reader(project, registry, module, local_names(caller));
    for (const auto *sections : {node ? &node->sections : nullptr,
                                 element ? &element->sections : nullptr}) {
      if (!sections)
        continue;
      auto fields =
          ast::field_list(ast::section_entries(*sections, "metadata"));
      std::vector<const ast::Field *> ordinary;
      for (const auto *field : fields)
        if (field->key.first.text != "pins")
          ordinary.push_back(field);
      overrides = merge_metadata(overrides, reader.read(ordinary, "instance"));
      for (const auto *group : fields)
        if (group->key.first.text == "pins")
          for (const auto &f : group->fields) {
            auto values = reader.pin(f, "instance_pin");
            for (const auto &key : physical_keys(f.key)) {
              auto pin = definition.aliases.at(key);
              auto attrs = values;
              if (auto role = attrs.find("role");
                  role != attrs.end() && attached_role(definition, pin)) {
                auto moved = role->second;
                attrs.erase(role);
                attrs.insert_or_assign("external_role", std::move(moved));
              }
              annotations[pin] = merge_metadata(annotations[pin], attrs);
            }
          }
    }
    return {numeric_metadata(definition.metadata, env, overrides, caller,
                             &definition.metadata_order),
            std::move(overrides), std::move(annotations)};
  }
  // Role selections become their possible alternatives; the selections are
  // kept for configuration views.
  static std::optional<ErcSelections>
  role_selections(MetaFields &attrs, const Path &caller_scope, const Span &span,
                  const std::string &module) {
    ErcSelections selections{{}, span, module};
    for (const auto *role : {"role", "external_role"}) {
      auto found = attrs.find(role);
      if (found == attrs.end() || !found->second.value.is<MetaFields>())
        continue;
      auto alternatives = possible_selections(found->second);
      selections.roles[role] = {found->second, caller_scope};
      found->second.value = MetaList(alternatives.begin(), alternatives.end());
    }
    if (selections.roles.empty())
      return {};
    return selections;
  }
  Tree
  expand(const DefinitionKey &key, const Path &path, const ast::Instance *node,
         const std::string &caller_module, const Environment &caller,
         std::vector<DefinitionKey> ancestry = {},
         std::optional<std::pair<Path, std::string>> ancestor_identity = {},
         const Path &caller_scope = {},
         const ast::ElementOverride *element = nullptr) {
    const auto &definition = definitions.at(key);
    auto name = path_text(path);
    instance_path = name.empty() ? definition.key.second : name;
    const auto &span = node ? node->span : definition.source->span;
    if (std::ranges::find(ancestry, key) != ancestry.end())
      error("instance.cycle", "recursive block instantiation", span);
    if (ancestry.size() > limits.hierarchy_depth ||
        instance_count >= limits.instances)
      error("circuit.limit", "instance expansion exceeds configured limit",
            span);
    ++instance_count;
    auto env = binding_parameters([&](ParameterEvaluator &e) {
      return e.bind(definition.parameters, instance_arguments(node, element),
                    caller_module, caller, span);
    });
    auto [metadata, overrides, annotations] = effective_metadata(
        definition, node, element, caller_module, caller, env);
    if (definition.kind != "component")
      circuit.choice_scopes.push_back(
          {path, caller_scope, fields_of(definition.metadata, "erc_choices"),
           fields_of(overrides, "erc_choices")});
    std::map<std::string, Tree> ports;
    for (const auto &[pin, port] : definition.interface) {
      auto attrs = numeric_metadata(port.metadata, env, annotations[pin],
                                    caller, &port.metadata_order);
      auto selections =
          role_selections(attrs, caller_scope, span, caller_module);
      ports[pin] = definition.kind == "component"
                       ? new_leaf(append(path, pin), path, "pin", {},
                                  std::move(attrs), port.span, selections)
                       : typed(port.type, env, append(path, pin), path, "port",
                               std::move(attrs), port.span, selections);
    }
    std::string identity;
    if (auto *explicit_identity = meta_value(metadata, "identity")) {
      identity = explicit_identity->string();
      if (identity.empty())
        error("identity.empty", "explicit identity must not be empty", span);
      if (node && !node->dimensions.empty())
        identity +=
            path_text(Path(path.end() - node->dimensions.size(), path.end()));
    } else if (ancestor_identity)
      identity = ancestor_identity->second + "." +
                 path_text(Path(path.begin() + ancestor_identity->first.size(),
                                path.end()));
    else
      identity = name;
    if (!path.empty()) {
      if (identities.contains(identity))
        error("identity.collision", "duplicate identity '" + identity + "'",
              span,
              Json::array({object("message", "previous instance", "span",
                                  identities.at(identity).json())}));
      identities[identity] = span;
    }
    if (definition.kind == "component") {
      if (auto *ref = meta_value(metadata, "ref")) {
        const auto &reference = ref->string();
        if (reference.empty() || references.contains(reference))
          error("reference.collision",
                "empty or duplicate explicit reference '" + reference + "'",
                span);
        references.insert(reference);
      }
      std::optional<Value> value;
      if (auto shown = overrides.find("value"); shown != overrides.end()) {
        // Reuse quantity display rules using the caller's parameter
        // declarations.
        auto display = definitions.at(ancestry.back()).parameters;
        display.part_value = ScopedExpression{shown->second.value.expression(),
                                              shown->second.module};
        value = binding_parameters([&](ParameterEvaluator &e) {
          return e.part_value(display, caller);
        });
      } else
        value = binding_parameters([&](ParameterEvaluator &e) {
          return e.part_value(definition.parameters, env);
        });
      auto *footprint = meta_value(metadata, "footprint");
      if (!footprint ||
          (footprint->is<std::string>() && footprint->string().empty()))
        circuit.warnings.push_back(object(
            "code", "footprint.unspecified", "severity", "warning", "message",
            "physical instance has no assigned footprint", "span", span.json(),
            "related", Json::array(), "instance_path", name, "configuration",
            nullptr, "application", nullptr));
      Part part;
      part.path = path;
      part.identity = identity;
      part.definition = key.first + "." + key.second;
      part.parameters = env;
      part.value = std::move(value);
      part.metadata = std::move(metadata);
      part.declaration = span;
      part.source = definition.source->span;
      circuit.parts.push_back(std::move(part));
      part_ports.push_back(ports);
      return std::make_shared<Node>(
          Node{NodeKind::instance, "", 0, {}, std::move(ports)});
    }
    auto nets = ports;
    std::vector<const ast::Net *> net_declarations;
    std::vector<const ast::Instance *> instances;
    for (const auto *declaration : definition.body)
      if (auto *net = ast::as<ast::Net>(declaration))
        net_declarations.push_back(net);
      else
        instances.push_back(&ast::as<ast::Instance>(*declaration));
    auto by_name = [](const auto *a, const auto *b) {
      return a->name.text < b->name.text;
    };
    std::ranges::sort(net_declarations, by_name);
    std::ranges::sort(instances, by_name);
    MetadataReader reader(project, registry, definition.body_module,
                          local_names(env));
    for (const auto *declaration : net_declarations) {
      const auto &net_name = declaration->name.text;
      auto net =
          typed({declaration->type.get(), definition.body_module}, env,
                append(path, net_name), path, "net",
                reader.read(declaration->fields, "net"), declaration->span, {});
      nets[net_name] = net;
      if (declaration->global)
        globals[net_name] = net;
    }
    struct Child {
      const ast::Instance *node;
      const Definition *target;
      Tree instances;
    };
    std::vector<Child> children;
    auto inherited = metadata.contains("identity")
                         ? std::make_optional(std::make_pair(path, identity))
                         : ancestor_identity;
    ancestry.push_back(key);
    for (const auto *declaration : instances) {
      auto target_key =
          definition_symbol(project, definition.body_module,
                            declaration->component, {"component", "block"});
      auto child_path = append(path, declaration->name.text);
      instance_path = path_text(child_path);
      auto shape =
          dimensions(declaration->dimensions, definition.body_module, env);
      if (path.size() + shape.size() + 1 > limits.hierarchy_depth)
        error("circuit.limit", "instance path exceeds configured nesting limit",
              declaration->span);
      std::map<Path, const ast::ElementOverride *> elements;
      for (const auto &entry :
           ast::section_entries(declaration->sections, "elements")) {
        const auto &override = ast::as<ast::ElementOverride>(*entry);
        Path indices;
        for (std::size_t i = 0; i < shape.size(); ++i) {
          const auto &selection = override.indices[i];
          indices.push_back(evaluating("", [&](Evaluator &e) {
            return e.index(e.evaluate(*selection.start), selection.span,
                           shape[i]);
          }));
        }
        if (elements.contains(indices))
          error("array.element-overlap",
                "array element is overridden more than once", override.span);
        elements[indices] = &override;
      }
      auto group = array(
          shape,
          [&](const Path &indices) {
            auto found = elements.find(indices);
            return expand(target_key, concat(child_path, indices), declaration,
                          definition.body_module, env, ancestry, inherited,
                          path,
                          found == elements.end() ? nullptr : found->second);
          },
          declaration->span);
      children.push_back(
          {declaration, &definitions.at(target_key), std::move(group)});
    }
    for (const auto &child : children) {
      instance_path = path_text(append(path, child.node->name.text));
      for (const auto &entry :
           ast::section_entries(child.node->sections, "connections")) {
        const auto &binding = ast::as<ast::Binding>(*entry);
        auto left = endpoint(child.instances, binding.endpoint, *child.target,
                             definition.body_module, env);
        auto right =
            reference(*binding.target, nets, definition.body_module, env);
        connect(left, right, binding.span);
      }
    }
    instance_path = name.empty() ? key.second : name;
    for (const auto &[pin, port] : ports)
      for (auto leaf : node_leaves(port)) {
        const auto &support = supports[root(leaf)];
        if (!std::ranges::any_of(support, [&](const Path &scope) {
              return prefix(scope, path);
            }))
          error("port.unimplemented",
                "port '" + pin +
                    "' has no internal physical endpoint, external endpoint, "
                    "or placeholder",
                definition.interface.at(pin).span);
      }
    Block block;
    block.path = path;
    block.definition = key.first + "." + key.second;
    block.identity = identity;
    block.declaration = span;
    block.source = definition.source->span;
    circuit.blocks.push_back(std::move(block));
    block_ports.push_back(ports);
    return std::make_shared<Node>(
        Node{NodeKind::instance, "", 0, {}, std::move(ports)});
  }
  // Sort records by path text, keeping their ports aligned.
  template <class Record>
  static void sort_records(std::vector<Record> &records,
                           std::vector<std::map<std::string, Tree>> &ports) {
    std::vector<std::size_t> order(records.size());
    std::iota(order.begin(), order.end(), 0);
    std::vector<std::string> keys;
    for (const auto &record : records)
      keys.push_back(path_text(record.path));
    std::ranges::stable_sort(order, {}, [&](auto i) { return keys[i]; });
    std::vector<Record> sorted_records;
    std::vector<std::map<std::string, Tree>> sorted_ports;
    for (auto i : order) {
      sorted_records.push_back(std::move(records[i]));
      sorted_ports.push_back(std::move(ports[i]));
    }
    records = std::move(sorted_records);
    ports = std::move(sorted_ports);
  }
  void finish() {
    auto &leaves = circuit.leaves;
    std::map<std::size_t, std::vector<std::size_t>> groups;
    for (std::size_t i = 0; i < leaves.size(); ++i)
      groups[root(i)].push_back(i);
    // Name each connected group after its outermost declaration.
    std::map<std::string,
             std::pair<CircuitNet, const std::vector<std::size_t> *>>
        named;
    for (const auto &[unused, ids] : groups) {
      std::vector<std::size_t> declared;
      for (auto i : ids)
        if (leaves[i].kind != "pin")
          declared.push_back(i);
      if (declared.empty())
        continue;
      auto chosen = *std::ranges::min_element(declared, {}, [&](auto i) {
        return std::make_tuple(leaves[i].scope.size(), leaves[i].kind != "net",
                               path_text(leaves[i].path));
      });
      auto name = path_text(leaves[chosen].path);
      std::ranges::sort(declared, {},
                        [&](auto i) { return path_text(leaves[i].path); });
      named[name] = {
          CircuitNet{name, leaves[chosen].path, std::move(declared), {}}, &ids};
    }
    for (auto &[name, entry] : named) {
      for (auto i : *entry.second)
        leaves[i].net = circuit.nets.size();
      circuit.nets.push_back(std::move(entry.first));
    }
    sort_records(circuit.parts, part_ports);
    sort_records(circuit.blocks, block_ports);
    for (std::size_t p = 0; p < circuit.parts.size(); ++p)
      for (const auto &[pin, port] : part_ports[p]) {
        circuit.parts[p].pins[pin] = port->leaf;
        if (auto net = leaves[port->leaf].net)
          circuit.nets[*net].endpoints.emplace_back(p, pin);
      }
    for (std::size_t b = 0; b < circuit.blocks.size(); ++b)
      for (const auto &[name, port] : block_ports[b])
        circuit.blocks[b].ports[name] = node_leaves(port);
  }

public:
  Elaborator(const Project &project, const RegistryDescription &registry,
             const Definitions &definitions, ExpansionLimits limits,
             Limits expression_limits)
      : project(project), registry(registry), definitions(definitions),
        limits(limits), expression_limits(expression_limits) {}
  Circuit elaborate() {
    const auto &board =
        definitions.at({project.board.module, project.board.name});
    for (const auto &file : project.config.erc_modules)
      circuit.erc_modules.push_back(file.string());
    circuit.erc_rule_sets = project.config.erc_rule_sets;
    circuit.board = project.board.module + "." + project.board.name;
    circuit.source = board.source->span;
    expand({project.board.module, project.board.name}, {}, nullptr,
           project.board.module, {});
    finish();
    circuit.configurations = configurations(board);
    return std::move(circuit);
  }
  // Named configurations, or by default every combination of the board's
  // choices.
  MetaFields configurations(const Definition &board) const {
    if (auto *named = meta_value(board.metadata, "configurations"))
      return named->fields();
    auto *choices = meta_value(board.metadata, "erc_choices");
    if (!choices)
      return {};
    const auto &declared = choices->fields();
    std::size_t count = 1;
    for (const auto &[name, alternatives] : declared)
      count *= alternatives.value.list().size();
    if (count > max_configurations)
      error("erc.configurations",
            "the board's choices form " + std::to_string(count) +
                " combinations, more than " +
                std::to_string(max_configurations) +
                "; name the configurations to check",
            board.metadata.at("erc_choices").span);
    MetaFields result;
    for (std::size_t i = 0; i < count; ++i) {
      // Mixed-radix digits, the last choice varying fastest.
      MetaFields values;
      std::string name;
      auto rest = i;
      for (auto it = declared.rbegin(); it != declared.rend(); ++it) {
        const auto &alternatives = it->second.value.list();
        values.insert_or_assign(it->first,
                                Meta{alternatives[rest % alternatives.size()],
                                     it->second.span, board.key.first});
        rest /= alternatives.size();
      }
      for (const auto &[choice, value] : values)
        name += (name.empty() ? "" : ",") + choice + "=" + value.value.string();
      result.insert_or_assign(name, Meta{std::move(values),
                                         board.metadata.at("erc_choices").span,
                                         board.key.first});
    }
    return result;
  }
};
Json leaf_metadata(const Leaf &leaf) {
  auto result = to_json(leaf.metadata);
  if (leaf.selections) {
    auto roles = Json::object();
    for (const auto &[role, selection] : leaf.selections->roles) {
      const auto &source = selection.selection;
      auto choices = to_json(source.value.fields());
      choices["scope"] = object("value", to_json(selection.scope), "span",
                                source.span.json(), "module", source.module);
      roles[role] = object("value", std::move(choices), "span",
                           source.span.json(), "module", source.module);
    }
    result["_erc_selections"] =
        object("value", std::move(roles), "span", leaf.selections->span.json(),
               "module", leaf.selections->module);
  }
  return result;
}
Json source_json(const Span &declaration, const Span &definition) {
  return object("declaration", declaration.json(), "definition",
                definition.json());
}
} // namespace
Circuit elaborate_project(const Project &project,
                          const RegistryDescription &registry,
                          const Definitions &definitions,
                          ExpansionLimits limits, Limits expression_limits) {
  return Elaborator(project, registry, definitions, limits, expression_limits)
      .elaborate();
}
Json to_json(const Circuit &circuit) {
  const auto &leaves = circuit.leaves;
  auto net_name = [&](const Leaf &leaf) {
    return leaf.net ? Json(circuit.nets[*leaf.net].name) : Json(nullptr);
  };
  auto parts = Json::array(), nets = Json::array(), blocks = Json::array(),
       scopes = Json::array();
  for (const auto &part : circuit.parts) {
    auto params = Json::object();
    for (const auto &[name, value] : part.parameters)
      params[name] = encoded(value);
    auto pins = Json::object();
    for (const auto &[pin, index] : part.pins) {
      const auto &leaf = leaves[index];
      pins[pin] =
          object("net", net_name(leaf), "binding", binding_name(leaf.binding),
                 "binding_span", leaf.binding_span.json(), "span",
                 leaf.span.json(), "metadata", leaf_metadata(leaf));
    }
    parts.push_back(object(
        "path", to_json(part.path), "identity", part.identity, "definition",
        part.definition, "parameters", std::move(params), "value",
        part.value ? encoded(*part.value) : Json(nullptr), "metadata",
        to_json(part.metadata), "source",
        source_json(part.declaration, part.source), "pins", std::move(pins)));
  }
  for (const auto &net : circuit.nets) {
    auto declarations = Json::array();
    for (auto index : net.declarations) {
      const auto &leaf = leaves[index];
      auto declaration = object(
          "path", to_json(leaf.path), "scope", to_json(leaf.scope), "kind",
          leaf.kind, "type", leaf.type ? Json(*leaf.type) : Json(nullptr),
          "metadata", leaf_metadata(leaf), "span", leaf.span.json(), "binding",
          binding_name(leaf.binding), "binding_span", leaf.binding_span.json());
      if (leaf.nettype)
        declaration["nettype"] = *leaf.nettype;
      declarations.push_back(std::move(declaration));
    }
    auto endpoints = Json::array();
    for (const auto &[part, pin] : net.endpoints)
      endpoints.push_back(
          object("instance", to_json(circuit.parts[part].path), "pin", pin));
    nets.push_back(object("name", net.name, "path", to_json(net.path),
                          "declarations", std::move(declarations), "endpoints",
                          std::move(endpoints)));
  }
  for (const auto &block : circuit.blocks) {
    auto ports = Json::object();
    for (const auto &[name, members] : block.ports) {
      auto list = Json::array();
      for (auto index : members)
        list.push_back(object("path", to_json(leaves[index].path), "net",
                              net_name(leaves[index]), "binding",
                              binding_name(leaves[index].binding)));
      ports[name] = std::move(list);
    }
    blocks.push_back(object(
        "path", to_json(block.path), "definition", block.definition, "identity",
        block.identity, "source", source_json(block.declaration, block.source),
        "ports", std::move(ports)));
  }
  for (const auto &scope : circuit.choice_scopes)
    scopes.push_back(object("scope", to_json(scope.scope), "parent_scope",
                            to_json(scope.parent_scope), "choices",
                            to_json(scope.choices), "bindings",
                            to_json(scope.bindings)));
  return object("schema_version", 1, "stage", "circuit", "erc_performed", false,
                "erc_modules", circuit.erc_modules, "erc_rule_sets",
                circuit.erc_rule_sets, "board", circuit.board, "source",
                circuit.source.json(), "erc_choice_scopes", std::move(scopes),
                "erc_configurations", to_json(circuit.configurations), "parts",
                std::move(parts), "nets", std::move(nets), "blocks",
                std::move(blocks), "warnings", circuit.warnings);
}
} // namespace noscheme
