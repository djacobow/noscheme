#include "noscheme/circuit.hpp"
#include "noscheme/validation.hpp"
#include <algorithm>
#include <cmath>
#include <numeric>

namespace noscheme {
namespace {
using Path = Json;
std::string path_text(const Path &path) {
  std::string result;
  for (const auto &part : path)
    if (part.is_number_integer())
      result += "[" + part.dump() + "]";
    else {
      if (!result.empty())
        result += '.';
      result += part.get<std::string>();
    }
  return result;
}
Path append(Path path, const Json &segment) {
  path.push_back(segment);
  return path;
}
Path concat(Path path, const Path &suffix) {
  for (const auto &part : suffix)
    path.push_back(part);
  return path;
}
bool prefix(const Path &path, const Path &parent) {
  return path.size() >= parent.size() &&
         std::equal(parent.begin(), parent.end(), path.begin());
}
Json wrapped(Json value, const Json &span, const std::string &module) {
  return object("value", std::move(value), "span", span, "module", module);
}
bool mapping(const Json &value) {
  return value.is_object() &&
         !(value.contains("node") && value.at("node").is_string()) &&
         !(value.contains("constraint") &&
           value.at("constraint").is_string()) &&
         !(value.contains("type") && value.at("type").is_string());
}
std::set<std::string> local_names(const Environment &environment) {
  std::set<std::string> result;
  for (const auto &[name, value] : environment)
    result.insert(name);
  return result;
}
struct Node;
using Tree = std::shared_ptr<const Node>;
struct Node {
  std::string kind, type;
  std::size_t leaf = 0;
  std::vector<Tree> items;
  std::map<std::string, Tree> fields;
};
Tree array_node(std::vector<Tree> items) {
  return std::make_shared<Node>(Node{"array", "", 0, std::move(items), {}});
}
struct Record {
  Json data;
  std::map<std::string, Tree> ports;
};
class Elaborator {
  const Project &project;
  const RegistryDescription &registry;
  const Definitions &definitions;
  ExpansionLimits limits;
  Limits expression_limits;
  Json warnings = Json::array(), instance_path = nullptr;
  std::vector<std::size_t> parents, sizes;
  std::vector<Json> leaves;
  std::vector<std::set<Path>> supports;
  std::map<std::size_t, Json> bound;
  std::map<std::string, Tree> globals;
  std::vector<Record> parts, blocks;
  Json choice_scopes = Json::array();
  std::map<std::string, Json> identities, references;
  std::size_t instance_count = 0;

  [[noreturn]] void error(const std::string &code, const std::string &message,
                          const Json &span,
                          const Json &related = Json::array()) const {
    const Symbols symbols;
    Evaluator evaluator(symbols, expression_limits);
    evaluator.instance_path = instance_path;
    evaluator.error(code, message, span, related);
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
      for (const auto &warning : evaluator.warnings)
        warnings.push_back(warning);
      return result;
    } catch (const DiagnosticsFailure &) {
      for (const auto &warning : evaluator.warnings)
        warnings.push_back(warning);
      throw;
    }
  }
  template <typename F> auto binding_parameters(F &&operation) {
    ParameterEvaluator evaluator(project, expression_limits);
    evaluator.instance_path = instance_path;
    try {
      auto result = operation(evaluator);
      for (const auto &warning : evaluator.warnings)
        warnings.push_back(warning);
      return result;
    } catch (const DiagnosticsFailure &) {
      for (const auto &warning : evaluator.warnings)
        warnings.push_back(warning);
      throw;
    }
  }
  Value evaluate(const Json &expr, const std::string &module,
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
                const Json &type, const Json &metadata, const Json &span) {
    if (leaves.size() >= limits.leaves)
      error("circuit.limit", "expanded net/pin count exceeds configured limit",
            span);
    auto number = leaves.size();
    parents.push_back(number);
    sizes.push_back(1);
    bool support = kind == "pin" || metadata.contains("external_role") ||
                   (metadata.contains("placeholder") &&
                    metadata.at("placeholder").at("value") == true);
    supports.push_back(support ? std::set<Path>{scope} : std::set<Path>{});
    leaves.push_back(object("path", path, "scope", scope, "kind", kind, "type",
                            type, "metadata", metadata, "span", span));
    return std::make_shared<Node>(Node{"scalar", "", number, {}, {}});
  }
  std::vector<std::size_t> node_leaves(const Tree &node) const {
    std::vector<std::size_t> result;
    std::function<void(const Tree &)> visit = [&](const Tree &child) {
      if (child->kind == "scalar")
        result.push_back(child->leaf);
      else if (child->kind == "array")
        for (const auto &item : child->items)
          visit(item);
      else
        for (const auto &[name, item] : child->fields)
          visit(item);
    };
    visit(node);
    return result;
  }
  std::vector<std::size_t> dimensions(const Json &selections,
                                      const std::string &module,
                                      const Environment &env) {
    std::vector<std::size_t> result;
    for (const auto &s : selections)
      result.push_back(evaluating(module, [&](Evaluator &e) {
        return e.dimension(e.evaluate(s.at("start"), env), s.at("span"));
      }));
    return result;
  }
  Tree array(const std::vector<std::size_t> &shape,
             const std::function<Tree(const Path &)> &build, const Json &span) {
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
    return expand(0, Json::array());
  }
  Json numeric_metadata(const Json &values, const Environment &env,
                        const Json &overrides = Json::object(),
                        const Environment &caller = {},
                        const MetadataOrder *orders = nullptr,
                        std::vector<std::string> path = {}) {
    auto result = merge_metadata(values, overrides);
    for (const auto &key : metadata_field_order(result, orders, path)) {
      auto &record = result.at(key);
      auto &value = record.at("value");
      auto nested = path;
      nested.push_back(key);
      if (overrides.contains(key) && values.contains(key) && mapping(value) &&
          mapping(values.at(key).at("value")) &&
          mapping(overrides.at(key).at("value"))) {
        value = numeric_metadata(values.at(key).at("value"), env,
                                 overrides.at(key).at("value"), caller, orders,
                                 nested);
        continue;
      }
      const auto &scope = overrides.contains(key) ? caller : env;
      if (value.is_object() && value.contains("constraint") &&
          value.at("constraint").is_string()) {
        auto number =
            evaluate(value.at("expression"), record.at("module"), scope);
        if (std::holds_alternative<std::string>(number))
          error("expression.type", "expected a number", record.at("span"));
        std::string constraint = value.at("constraint");
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
                record.at("span"));
        value = encoded(number);
      } else if (mapping(value))
        value =
            numeric_metadata(value, scope, Json::object(), {}, orders, nested);
    }
    return result;
  }
  Tree typed(const Json &value, const Environment &env, const Path &path,
             const Path &scope, const std::string &kind, Json metadata,
             const Json &span, std::size_t depth = 0) {
    if (depth > limits.type_depth || path.size() > limits.hierarchy_depth)
      error("circuit.limit", "bundle nesting exceeds configured limit", span);
    metadata = numeric_metadata(metadata, env);
    const Json *expr = &value.at("value");
    auto selections = Json::array();
    while (expr->at("node") == "Subscript") {
      selections.push_back(expr->at("selection"));
      expr = &expr->at("base");
    }
    std::reverse(selections.begin(), selections.end());
    auto shape = dimensions(selections, value.at("module"), env);
    if (path.size() + shape.size() > limits.hierarchy_depth)
      error("circuit.limit", "net path exceeds configured nesting limit", span);
    const auto *base = type_definition(project, registry, definitions, value);
    if (base && base->kind == "nettype")
      metadata =
          merge_metadata(numeric_metadata(base->metadata, {}, Json::object(),
                                          {}, &base->metadata_order),
                         metadata);
    return array(
        shape,
        [&](const Path &indices) -> Tree {
          auto here = concat(path, indices);
          if (!base || base->kind == "nettype") {
            auto leaf =
                new_leaf(here, scope, kind,
                         base ? base->electrical : expr->at("name").at("text"),
                         metadata, span);
            if (base)
              leaves[leaf->leaf]["nettype"] =
                  base->key.first + "." + base->key.second;
            return leaf;
          }
          std::map<std::string, Tree> fields;
          for (const auto &[name, port] : base->interface)
            fields[name] = typed(port.type, {}, append(here, name), scope, kind,
                                 merge_metadata(metadata, port.metadata),
                                 port.span, depth + 1);
          return std::make_shared<Node>(
              Node{"bundle",
                   base->key.first + "." + base->key.second,
                   0,
                   {},
                   std::move(fields)});
        },
        span);
  }
  Tree field(const Tree &node, const std::string &name, const Json &span) {
    if (node->kind == "array") {
      std::vector<Tree> items;
      for (const auto &child : node->items)
        items.push_back(field(child, name, span));
      return array_node(std::move(items));
    }
    if (!node->fields.contains(name))
      error("connection.field", "unknown public field '" + name + "'", span);
    return node->fields.at(name);
  }
  Tree select(Tree node, const Json &selectors, const std::string &module,
              const Environment &env) {
    for (const auto &selection : selectors) {
      if (selection.at("node") == "Name") {
        node = field(node, selection.at("text"), selection.at("span"));
        continue;
      }
      if (node->kind != "array")
        error("array.scalar-index", "cannot index a scalar endpoint or net",
              selection.at("span"));
      auto start = evaluating(module, [&](Evaluator &e) {
        return e.index(e.evaluate(selection.at("start"), env),
                       selection.at("span"), node->items.size());
      });
      if (selection.at("stop").is_null())
        node = node->items[start];
      else {
        auto stop = evaluating(module, [&](Evaluator &e) {
          return e.index(e.evaluate(selection.at("stop"), env),
                         selection.at("span"), node->items.size(), true);
        });
        if (stop <= start)
          error("array.slice", "slices must be nonempty and ascending",
                selection.at("span"));
        node = array_node(std::vector<Tree>(node->items.begin() + start,
                                            node->items.begin() + stop));
      }
    }
    return node;
  }
  Tree endpoint(const Tree &instances, const Json &endpoint,
                const Definition &target, const std::string &module,
                const Environment &env) {
    auto selected =
        select(instances, endpoint.at("instance_indices"), module, env);
    auto names = physical_keys(endpoint.at("head"));
    std::function<Tree(const Tree &)> ports = [&](const Tree &node) -> Tree {
      if (node->kind == "array") {
        std::vector<Tree> items;
        for (const auto &child : node->items)
          items.push_back(ports(child));
        return array_node(std::move(items));
      }
      std::vector<Tree> values;
      for (const auto &name : names)
        values.push_back(node->fields.at(target.aliases.at(name)));
      bool grouped = !endpoint.at("head").at("last").is_null() ||
                     !endpoint.at("head").at("items").empty();
      return select(grouped ? array_node(std::move(values)) : values.front(),
                    endpoint.at("selectors"), module, env);
    };
    return ports(selected);
  }
  Tree reference(const Json &expression,
                 const std::map<std::string, Tree> &nets,
                 const std::string &module, const Environment &env) {
    const Json *expr = &expression;
    auto selectors = Json::array();
    while (expr->at("node") == "Member" || expr->at("node") == "Subscript") {
      selectors.push_back(
          expr->at(expr->at("node") == "Member" ? "name" : "selection"));
      expr = &expr->at("base");
    }
    std::reverse(selectors.begin(), selectors.end());
    std::string name = expr->at("text");
    if (name == "no_connect" && selectors.empty())
      return nullptr;
    Tree node;
    if (name == "global") {
      auto selection = selectors.front();
      selectors.erase(selectors.begin());
      name = selection.at("text");
      if (!globals.contains(name))
        error("connection.global",
              "global net '" + name + "' is not declared by the board",
              selection.at("span"));
      node = globals.at(name);
    } else {
      if (!nets.contains(name))
        error("connection.net", "undeclared net '" + name + "'",
              expr->at("span"));
      node = nets.at(name);
    }
    return select(node, selectors, module, env);
  }
  std::vector<std::size_t> shape(Tree node) const {
    std::vector<std::size_t> result;
    while (node && node->kind == "array") {
      result.push_back(node->items.size());
      node = node->items.front();
    }
    return result;
  }
  void connect(const Tree &left, const Tree &right, const Json &span) {
    auto lhs_shape = shape(left), rhs_shape = shape(right);
    if (right && !rhs_shape.empty() && lhs_shape != rhs_shape)
      error("connection.shape", "endpoint shape does not match net shape",
            span);
    std::function<void(const Tree &, const Tree &)> bind =
        [&](const Tree &lhs, const Tree &rhs) {
          if (lhs->kind == "array") {
            for (std::size_t i = 0; i < lhs->items.size(); ++i)
              bind(lhs->items[i],
                   rhs && rhs->kind == "array" ? rhs->items[i] : rhs);
          } else if (lhs->kind == "bundle") {
            if (rhs && (rhs->kind != "bundle" || lhs->type != rhs->type))
              error("connection.bundle-type",
                    "whole bundles must have the same named type", span);
            for (const auto &[name, child] : lhs->fields)
              connect(child, rhs ? rhs->fields.at(name) : nullptr, span);
          } else {
            if (rhs && rhs->kind != "scalar")
              error("connection.shape",
                    "a physical pin or scalar port requires a scalar net",
                    span);
            if (bound.contains(lhs->leaf))
              error("connection.overlap", "endpoint is bound more than once",
                    span,
                    Json::array({object("message", "previous binding", "span",
                                        bound.at(lhs->leaf).at("span"))}));
            bound[lhs->leaf] =
                object("state", rhs ? "connected" : "no_connect", "span", span);
            if (rhs)
              join(lhs->leaf, rhs->leaf);
          }
        };
    bind(left, right);
  }
  struct Metadata {
    Json values, overrides, annotations;
  };
  Metadata effective_metadata(const Definition &definition, const Json *node,
                              const Json *element, const std::string &module,
                              const Environment &caller,
                              const Environment &env) {
    auto overrides = Json::object(), annotations = Json::object();
    MetadataReader reader(project, registry, module, local_names(caller));
    for (const auto *source : {node, element}) {
      if (!source)
        continue;
      const auto &fields = section_entries(*source, "metadata");
      auto ordinary = Json::array();
      for (const auto &field : fields)
        if (field.at("key").at("first").at("text") != "pins")
          ordinary.push_back(field);
      overrides = merge_metadata(overrides, reader.read(ordinary, "instance"));
      for (const auto &group : fields)
        if (group.at("key").at("first").at("text") == "pins")
          for (const auto &f : group.at("fields")) {
            auto values = reader.pin(f, "instance_pin");
            for (const auto &key : physical_keys(f.at("key"))) {
              auto pin = definition.aliases.at(key);
              auto attrs = values;
              if (attrs.contains("role") && attached_role(definition, pin)) {
                attrs["external_role"] = attrs.at("role");
                attrs.erase("role");
              }
              annotations[pin] =
                  merge_metadata(annotations.value(pin, Json::object()), attrs);
            }
          }
    }
    return {numeric_metadata(definition.metadata, env, overrides, caller,
                             &definition.metadata_order),
            overrides, annotations};
  }
  Json arguments(const Json *node, const Json *element) const {
    auto result = Json::array();
    std::map<std::string, std::size_t> positions;
    for (const auto *source : {node, element})
      if (source)
        for (const auto &arg : section_entries(*source, "params")) {
          std::string name = arg.at("name").at("text");
          if (positions.contains(name))
            result[positions.at(name)] = arg;
          else {
            positions[name] = result.size();
            result.push_back(arg);
          }
        }
    return result;
  }
  Tree
  expand(const DefinitionKey &key, const Path &path, const Json *node,
         const std::string &caller_module, const Environment &caller,
         std::vector<DefinitionKey> ancestry = {},
         std::optional<std::pair<Path, std::string>> ancestor_identity = {},
         const Path &caller_scope = Json::array(),
         const Json *element = nullptr) {
    const auto &definition = definitions.at(key);
    auto name = path_text(path);
    instance_path = name.empty() ? definition.key.second : name;
    const auto &span = node ? node->at("span") : definition.source->at("span");
    if (std::ranges::find(ancestry, key) != ancestry.end())
      error("instance.cycle", "recursive block instantiation", span);
    if (ancestry.size() > limits.hierarchy_depth ||
        instance_count >= limits.instances)
      error("circuit.limit", "instance expansion exceeds configured limit",
            span);
    ++instance_count;
    auto env = binding_parameters([&](ParameterEvaluator &e) {
      return e.bind(definition.parameters, arguments(node, element),
                    caller_module, caller, span);
    });
    auto [metadata, overrides, annotations] = effective_metadata(
        definition, node, element, caller_module, caller, env);
    if (definition.kind != "component")
      choice_scopes.push_back(
          object("scope", path, "parent_scope", caller_scope, "choices",
                 definition.metadata.contains("erc_choices")
                     ? definition.metadata.at("erc_choices").at("value")
                     : Json::object(),
                 "bindings",
                 overrides.contains("erc_choices")
                     ? overrides.at("erc_choices").at("value")
                     : Json::object()));
    std::map<std::string, Tree> ports;
    for (const auto &[pin, port] : definition.interface) {
      auto attrs = numeric_metadata(port.metadata, env,
                                    annotations.value(pin, Json::object()),
                                    caller, &port.metadata_order);
      auto selections = Json::object();
      for (const auto &role : {"role", "external_role"})
        if (attrs.contains(role) && mapping(attrs.at(role).at("value"))) {
          auto value = attrs.at(role);
          auto choices = value.at("value");
          choices["scope"] =
              wrapped(caller_scope, value.at("span"), value.at("module"));
          selections[role] =
              wrapped(std::move(choices), value.at("span"), value.at("module"));
          attrs[role]["value"] = possible_selections(value);
        }
      if (!selections.empty())
        attrs["_erc_selections"] =
            wrapped(std::move(selections), span, caller_module);
      ports[pin] = definition.kind == "component"
                       ? new_leaf(append(path, pin), path, "pin", nullptr,
                                  attrs, port.span)
                       : typed(port.type, env, append(path, pin), path, "port",
                               attrs, port.span);
    }
    std::string identity;
    if (metadata.contains("identity")) {
      identity = metadata.at("identity").at("value");
      if (identity.empty())
        error("identity.empty", "explicit identity must not be empty", span);
      if (node && !node->at("dimensions").empty()) {
        auto indices = Json::array();
        for (std::size_t i = path.size() - node->at("dimensions").size();
             i < path.size(); ++i)
          indices.push_back(path[i]);
        identity += path_text(indices);
      }
    } else if (ancestor_identity) {
      auto suffix = Json::array();
      for (std::size_t i = ancestor_identity->first.size(); i < path.size();
           ++i)
        suffix.push_back(path[i]);
      identity = ancestor_identity->second + "." + path_text(suffix);
    } else
      identity = name;
    if (!path.empty()) {
      if (identities.contains(identity))
        error("identity.collision", "duplicate identity '" + identity + "'",
              span,
              Json::array({object("message", "previous instance", "span",
                                  identities.at(identity))}));
      identities[identity] = span;
    }
    auto origin = object("declaration", span, "definition",
                         definition.source->at("span"));
    if (definition.kind == "component") {
      if (metadata.contains("ref")) {
        std::string reference = metadata.at("ref").at("value");
        if (reference.empty() || references.contains(reference))
          error("reference.collision",
                "empty or duplicate explicit reference '" + reference + "'",
                span);
        references[reference] = span;
      }
      std::optional<Value> value;
      if (overrides.contains("value")) {
        // Reuse quantity display rules using the caller's parameter
        // declarations.
        auto display = definitions.at(ancestry.back()).parameters;
        const auto &shown = overrides.at("value");
        display.part_value =
            ScopedExpression{shown.at("value"), shown.at("module")};
        value = binding_parameters([&](ParameterEvaluator &e) {
          return e.part_value(display, caller);
        });
      } else
        value = binding_parameters([&](ParameterEvaluator &e) {
          return e.part_value(definition.parameters, env);
        });
      auto params = Json::object();
      for (const auto &[param, number] : env)
        params[param] = encoded(number);
      parts.push_back(
          {object("path", path, "identity", identity, "definition",
                  key.first + "." + key.second, "parameters", std::move(params),
                  "value", value ? encoded(*value) : Json(nullptr), "metadata",
                  std::move(metadata), "source", origin),
           ports});
      const auto &attrs = parts.back().data.at("metadata");
      if (!attrs.contains("footprint") ||
          attrs.at("footprint").at("value") == "")
        warnings.push_back(
            object("code", "footprint.unspecified", "severity", "warning",
                   "message", "physical instance has no assigned footprint",
                   "span", span, "related", Json::array(), "instance_path",
                   name, "configuration", nullptr, "application", nullptr));
      return std::make_shared<Node>(
          Node{"instance", "", 0, {}, std::move(ports)});
    }
    auto nets = ports;
    std::vector<const Json *> net_declarations, instances;
    for (const auto *declaration : definition.body)
      (declaration->at("node") == "Net" ? net_declarations : instances)
          .push_back(declaration);
    auto by_name = [](const Json *a, const Json *b) {
      return a->at("name").at("text") < b->at("name").at("text");
    };
    std::ranges::sort(net_declarations, by_name);
    std::ranges::sort(instances, by_name);
    MetadataReader reader(project, registry, definition.body_module,
                          local_names(env));
    for (const auto *declaration : net_declarations) {
      std::string net_name = declaration->at("name").at("text");
      auto net = typed(wrapped(declaration->at("type"),
                               declaration->at("type").at("span"),
                               definition.body_module),
                       env, append(path, net_name), path, "net",
                       reader.read(declaration->at("fields"), "net"),
                       declaration->at("span"));
      nets[net_name] = net;
      if (declaration->at("global_") == true)
        globals[net_name] = net;
    }
    struct Child {
      const Json *node;
      const Definition *target;
      Tree instances;
    };
    std::vector<Child> children;
    auto inherited = metadata.contains("identity")
                         ? std::make_optional(std::make_pair(path, identity))
                         : ancestor_identity;
    ancestry.push_back(key);
    for (const auto *declaration : instances) {
      auto target_key = definition_symbol(project, definition.body_module,
                                          declaration->at("component"),
                                          {"component", "block"});
      auto child_path = append(path, declaration->at("name").at("text"));
      instance_path = path_text(child_path);
      auto shape = dimensions(declaration->at("dimensions"),
                              definition.body_module, env);
      if (path.size() + shape.size() + 1 > limits.hierarchy_depth)
        error("circuit.limit", "instance path exceeds configured nesting limit",
              declaration->at("span"));
      std::map<Path, const Json *> elements;
      for (const auto &override : section_entries(*declaration, "elements")) {
        auto indices = Json::array();
        for (std::size_t i = 0; i < shape.size(); ++i) {
          const auto &selection = override.at("indices")[i];
          indices.push_back(evaluating("", [&](Evaluator &e) {
            return e.index(e.evaluate(selection.at("start")),
                           selection.at("span"), shape[i]);
          }));
        }
        if (elements.contains(indices))
          error("array.element-overlap",
                "array element is overridden more than once",
                override.at("span"));
        elements[indices] = &override;
      }
      auto group = array(
          shape,
          [&](const Path &indices) {
            return expand(
                target_key, concat(child_path, indices), declaration,
                definition.body_module, env, ancestry, inherited, path,
                elements.contains(indices) ? elements.at(indices) : nullptr);
          },
          declaration->at("span"));
      children.push_back(
          {declaration, &definitions.at(target_key), std::move(group)});
    }
    for (const auto &child : children) {
      instance_path =
          path_text(append(path, child.node->at("name").at("text")));
      for (const auto &binding : section_entries(*child.node, "connections")) {
        auto left = endpoint(child.instances, binding.at("endpoint"),
                             *child.target, definition.body_module, env);
        auto right =
            reference(binding.at("target"), nets, definition.body_module, env);
        connect(left, right, binding.at("span"));
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
    blocks.push_back(
        {object("path", path, "definition", key.first + "." + key.second,
                "identity", identity, "source", origin),
         ports});
    return std::make_shared<Node>(
        Node{"instance", "", 0, {}, std::move(ports)});
  }
  Json finish() {
    const auto &board =
        definitions.at({project.board.module, project.board.name});
    std::map<std::size_t, std::vector<std::size_t>> groups;
    for (std::size_t i = 0; i < leaves.size(); ++i)
      groups[root(i)].push_back(i);
    std::map<std::size_t, std::string> net_for_leaf;
    std::map<std::string, Json> nets;
    auto binding_state = [&](std::size_t i) -> Json {
      return bound.contains(i) ? bound.at(i).at("state") : Json("omitted");
    };
    auto binding_span = [&](std::size_t i) -> Json {
      return bound.contains(i) ? bound.at(i).at("span") : Json(nullptr);
    };
    for (const auto &[unused, ids] : groups) {
      std::vector<std::size_t> declared;
      for (auto i : ids)
        if (leaves[i].at("kind") != "pin")
          declared.push_back(i);
      if (declared.empty())
        continue;
      auto chosen = *std::ranges::min_element(declared, {}, [&](auto i) {
        return std::make_tuple(leaves[i].at("scope").size(),
                               leaves[i].at("kind") != "net",
                               path_text(leaves[i].at("path")));
      });
      auto name = path_text(leaves[chosen].at("path"));
      for (auto i : ids)
        net_for_leaf[i] = name;
      std::ranges::sort(declared, {}, [&](auto i) {
        return path_text(leaves[i].at("path"));
      });
      auto declarations = Json::array();
      for (auto i : declared) {
        auto declaration = leaves[i];
        declaration["binding"] = binding_state(i);
        declaration["binding_span"] = binding_span(i);
        declarations.push_back(std::move(declaration));
      }
      nets[name] = object("name", name, "path", leaves[chosen].at("path"),
                          "declarations", std::move(declarations), "endpoints",
                          Json::array());
    }
    auto by_path = [](const Record &r) { return path_text(r.data.at("path")); };
    std::ranges::sort(parts, {}, by_path);
    std::ranges::sort(blocks, {}, by_path);
    auto part_records = Json::array(), block_records = Json::array(),
         net_records = Json::array();
    for (auto &part : parts) {
      auto pins = Json::object();
      for (const auto &[pin, port] : part.ports) {
        auto leaf = port->leaf;
        Json net = net_for_leaf.contains(leaf) ? Json(net_for_leaf.at(leaf))
                                               : Json(nullptr);
        pins[pin] =
            object("net", net, "binding", binding_state(leaf), "binding_span",
                   binding_span(leaf), "span", leaves[leaf].at("span"),
                   "metadata", leaves[leaf].at("metadata"));
        if (!net.is_null())
          nets.at(net.get<std::string>())
              .at("endpoints")
              .push_back(object("instance", part.data.at("path"), "pin", pin));
      }
      part.data["pins"] = std::move(pins);
      part_records.push_back(std::move(part.data));
    }
    for (auto &block : blocks) {
      auto ports = Json::object();
      for (const auto &[name, port] : block.ports) {
        auto list = Json::array();
        for (auto leaf : node_leaves(port))
          list.push_back(object("path", leaves[leaf].at("path"), "net",
                                net_for_leaf.contains(leaf)
                                    ? Json(net_for_leaf.at(leaf))
                                    : Json(nullptr),
                                "binding", binding_state(leaf)));
        ports[name] = std::move(list);
      }
      block.data["ports"] = std::move(ports);
      block_records.push_back(std::move(block.data));
    }
    for (auto &[name, net] : nets)
      net_records.push_back(std::move(net));
    std::vector<std::string> modules;
    for (const auto &file : project.config.erc_modules)
      modules.push_back(file.string());
    return object(
        "schema_version", 1, "stage", "circuit", "erc_performed", false,
        "erc_modules", modules, "erc_rule_sets", project.config.erc_rule_sets,
        "board", project.board.module + "." + project.board.name, "source",
        board.source->at("span"), "erc_choice_scopes", std::move(choice_scopes),
        "erc_configurations",
        board.metadata.contains("configurations")
            ? board.metadata.at("configurations").at("value")
            : Json::object(),
        "parts", std::move(part_records), "nets", std::move(net_records),
        "blocks", std::move(block_records), "warnings", warnings);
  }

public:
  Elaborator(const Project &project, const RegistryDescription &registry,
             const Definitions &definitions, ExpansionLimits limits,
             Limits expression_limits)
      : project(project), registry(registry), definitions(definitions),
        limits(limits), expression_limits(expression_limits) {}
  Json elaborate() {
    expand({project.board.module, project.board.name}, Json::array(), nullptr,
           project.board.module, {});
    return finish();
  }
};
} // namespace
Json elaborate_project(const Project &project,
                       const RegistryDescription &registry,
                       const Definitions &definitions, ExpansionLimits limits,
                       Limits expression_limits) {
  return Elaborator(project, registry, definitions, limits, expression_limits)
      .elaborate();
}
} // namespace noscheme
