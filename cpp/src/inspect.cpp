#include "noscheme/inspect.hpp"
namespace noscheme {
namespace {
using namespace ast;
void names(const Node *node, std::set<std::string> &used) {
  if (!node)
    return;
  if (auto *name = as<Name>(node)) {
    used.insert(name->text);
    return;
  }
  if (auto *member = as<Member>(node)) {
    names(member->base.get(), used);
    return;
  }
  if (auto *keyword = as<KeywordArgument>(node)) {
    names(keyword->value.get(), used);
    return;
  }
  for_each_child(
      *node, [&](std::string_view, const Node &child) { names(&child, used); });
}
void uses(const Node &node, std::set<std::string> &used) {
  if (auto *field = as<Field>(&node)) {
    if (field->key.first.text == "custom" && !field->fields.empty())
      return;
    names(field->value.get(), used);
    names(field->default_value.get(), used);
    for (const auto &child : field->fields)
      uses(child, used);
  } else if (auto *argument = as<Argument>(&node))
    names(argument->value.get(), used);
  else if (auto *binding = as<Binding>(&node)) {
    names(binding->target.get(), used);
    for (const auto &index : binding->endpoint.instance_indices)
      names(&index, used);
    for (const auto &selector : binding->endpoint.selectors)
      if (as<Index>(selector))
        names(selector.get(), used);
  } else if (auto *net = as<Net>(&node)) {
    names(net->type.get(), used);
    for (const auto &field : net->fields)
      uses(field, used);
  } else if (auto *instance = as<Instance>(&node)) {
    for (const auto &dimension : instance->dimensions)
      names(&dimension, used);
    for (const auto &section : instance->sections)
      uses(section, used);
  } else if (auto *element = as<ElementOverride>(&node)) {
    for (const auto &section : element->sections)
      uses(section, used);
  } else if (auto *section = as<Section>(&node)) {
    for (const auto &entry : section->entries)
      uses(*entry, used);
  }
}
} // namespace
Json unused_parameters(const Definitions &definitions) {
  using Key = std::pair<std::string, std::size_t>;
  std::map<Key, const Parameter *> declared;
  std::set<Key> used;
  for (const auto &[key, definition] : definitions) {
    std::set<std::string> referenced;
    for (const auto &node : definition.source->body)
      uses(*node, referenced);
    if (definition.kind == "component" &&
        !definition.metadata.contains("value"))
      referenced.insert("value");
    for (const auto &parameter : definition.parameters.parameters) {
      Key span{parameter.span.filename(), parameter.span.start};
      declared[span] = &parameter;
      if (referenced.contains(parameter.name))
        used.insert(span);
    }
  }
  auto result = Json::array();
  for (const auto &[span, parameter] : declared)
    if (!used.contains(span))
      result.push_back(object(
          "code", "lint.unused-parameter", "severity", "warning", "message",
          "parameter '" + parameter->name +
              "' is not referenced; it may be an intentional public API "
              "placeholder",
          "span", parameter->span.json(), "related", Json::array(),
          "instance_path", nullptr, "configuration", nullptr, "application",
          nullptr));
  return result;
}
} // namespace noscheme

namespace noscheme {
namespace {
class TemplateEvaluator {
  const Project &project;
  const Definitions &definitions;
  Json warnings = Json::array();
  std::string path;
  template <class F> auto evaluating(const std::string &module, F operation) {
    const Symbols empty;
    Evaluator evaluator(module.empty() ? empty
                                       : project.modules.at(module).symbols);
    evaluator.instance_path = path;
    auto result = operation(evaluator);
    for (const auto &warning : evaluator.warnings)
      warnings.push_back(warning);
    return result;
  }
  template <class F> auto parameters(F operation) {
    ParameterEvaluator evaluator(project);
    evaluator.instance_path = path;
    auto result = operation(evaluator);
    for (const auto &warning : evaluator.warnings)
      warnings.push_back(warning);
    return result;
  }
  [[noreturn]] void error(const std::string &code, const std::string &message,
                          const Span &span) {
    const Symbols empty;
    Evaluator evaluator(empty);
    evaluator.instance_path = path;
    evaluator.error(code, message, span);
  }
  std::vector<std::size_t> shape(const ScopedExpression &type,
                                 const Environment &env) {
    std::vector<const ast::Index *> indices;
    auto *expr = type.expression;
    while (auto *sub = ast::as<ast::Subscript>(expr)) {
      indices.push_back(&sub->selection);
      expr = sub->base.get();
    }
    std::reverse(indices.begin(), indices.end());
    std::vector<std::size_t> result;
    for (auto *index : indices)
      result.push_back(dimension(*index, type.module, env));
    return result;
  }
  std::size_t dimension(const ast::Index &index, const std::string &module,
                        const Environment &env) {
    return evaluating(module, [&](Evaluator &e) {
      return e.dimension(e.evaluate(*index.start, env), index.span);
    });
  }
  std::optional<Value> override_value(const std::vector<ast::Section> &sections,
                                      const noscheme::Definition &caller,
                                      const Environment &env,
                                      std::optional<Value> fallback) {
    for (const auto *field :
         ast::field_list(ast::section_entries(sections, "metadata")))
      if (field->key.first.text == "value") {
        auto display = caller.parameters;
        display.part_value =
            ScopedExpression{field->value.get(), caller.body_module};
        return parameters(
            [&](ParameterEvaluator &e) { return e.part_value(display, env); });
      }
    return fallback;
  }
  struct Frame {
    DefinitionKey key;
    std::string path;
    std::vector<DefinitionKey> ancestry;
    std::vector<const ast::Argument *> arguments;
    Environment caller;
    std::string module;
    Span span;
    std::vector<std::size_t> dimensions;
    std::optional<Value> value;
  };

public:
  TemplateEvaluator(const Project &p, const Definitions &d)
      : project(p), definitions(d) {}
  Json run() {
    DefinitionKey root{project.board.module, project.board.name};
    std::vector<Frame> stack{{root,
                              root.second,
                              {},
                              {},
                              {},
                              root.first,
                              definitions.at(root).source->span,
                              {},
                              {}}};
    auto records = Json::array();
    while (!stack.empty()) {
      auto frame = std::move(stack.back());
      stack.pop_back();
      path = frame.path;
      if (std::ranges::find(frame.ancestry, frame.key) != frame.ancestry.end())
        error("instance.cycle",
              "recursive block instantiation cannot produce a finite circuit",
              frame.span);
      if (records.size() >= 10000)
        error("instance.limit",
              "instance declaration count exceeds configured limit",
              frame.span);
      const auto &definition = definitions.at(frame.key);
      auto env = parameters([&](ParameterEvaluator &e) {
        return e.bind(definition.parameters, frame.arguments, frame.module,
                      frame.caller, frame.span);
      });
      auto shapes = Json::object(), nets = Json::object(),
           values = Json::object(), params = Json::object();
      for (const auto &[name, port] : definition.interface)
        if (port.type.expression)
          shapes[name] = shape(port.type, env);
      auto shown =
          frame.value ? frame.value : parameters([&](ParameterEvaluator &e) {
            return e.part_value(definition.parameters, env);
          });
      if (shown)
        values["value"] = encoded(*shown);
      for (const auto &[name, value] : env)
        params[name] = encoded(value);
      for (const auto *node : definition.body)
        if (auto *net = ast::as<ast::Net>(node))
          nets[net->name.text] =
              shape({net->type.get(), definition.body_module}, env);
      auto record = object(
          "path", path, "definition", frame.key.first + "." + frame.key.second,
          "kind", definition.kind, "dimensions", frame.dimensions, "parameters",
          params, "interface_dimensions", shapes, "values", values,
          "connection_selections", Json::object(), "net_dimensions", nets);
      std::vector<Frame> children;
      for (const auto *node : definition.body) {
        auto *instance = ast::as<ast::Instance>(node);
        if (!instance)
          continue;
        auto target =
            definition_symbol(project, definition.body_module,
                              instance->component, {"component", "block"});
        path = frame.path + "." + instance->name.text;
        std::vector<std::size_t> dimensions;
        for (const auto &index : instance->dimensions)
          dimensions.push_back(dimension(index, definition.body_module, env));
        auto selected = Json::array();
        for (const auto &entry :
             ast::section_entries(instance->sections, "connections")) {
          const auto &binding = ast::as<ast::Binding>(*entry);
          std::vector<const ast::Index *> selections, rhs;
          for (const auto &index : binding.endpoint.instance_indices)
            selections.push_back(&index);
          for (const auto &selector : binding.endpoint.selectors)
            if (auto *index = ast::as<ast::Index>(selector))
              selections.push_back(index);
          auto *expr = binding.target.get();
          while (expr) {
            if (auto *member = ast::as<ast::Member>(expr))
              expr = member->base.get();
            else if (auto *sub = ast::as<ast::Subscript>(expr)) {
              rhs.push_back(&sub->selection);
              expr = sub->base.get();
            } else
              break;
          }
          selections.insert(selections.end(), rhs.rbegin(), rhs.rend());
          for (const auto *selection : selections) {
            auto bounds = evaluating(definition.body_module, [&](Evaluator &e) {
              auto start = e.integer(e.evaluate(*selection->start, env),
                                     selection->span);
              std::optional<Integer> stop;
              if (selection->stop)
                stop = e.integer(e.evaluate(*selection->stop, env),
                                 selection->span);
              if (start < 0 || (stop && *stop <= start))
                e.error("array.selection",
                        "indices must be nonnegative and slices nonempty and "
                        "ascending",
                        selection->span);
              // Keep arbitrary-precision integers tagged until the Python CLI
              // serializes its public JSON result.
              return object("start", encoded(Value(start)), "stop",
                            stop ? encoded(Value(*stop)) : Json(), "span",
                            selection->span.json());
            });
            selected.push_back(bounds);
          }
        }
        record["connection_selections"][instance->name.text] = selected;
        auto value = override_value(instance->sections, definition, env, {});
        std::vector<
            std::pair<std::vector<std::size_t>, const ast::ElementOverride *>>
            elements;
        std::set<std::vector<std::size_t>> seen;
        for (const auto &entry :
             ast::section_entries(instance->sections, "elements")) {
          const auto &element = ast::as<ast::ElementOverride>(*entry);
          std::vector<std::size_t> indices;
          for (std::size_t i = 0; i < dimensions.size(); ++i)
            indices.push_back(evaluating("", [&](Evaluator &e) {
              const auto &index = element.indices.at(i);
              return e.index(e.evaluate(*index.start), index.span,
                             dimensions[i]);
            }));
          if (!seen.insert(indices).second)
            error("array.element-overlap",
                  "array element is overridden more than once", element.span);
          elements.emplace_back(indices, &element);
        }
        // Only compare with the override count, so the product cannot overflow.
        std::size_t count = 1;
        for (auto size : dimensions) {
          if (count > elements.size())
            break;
          count = size > elements.size() / count ? elements.size() + 1
                                                 : count * size;
        }
        auto ancestry = frame.ancestry;
        ancestry.push_back(frame.key);
        if (elements.size() < count)
          children.push_back(
              {target, path, ancestry, instance_arguments(instance), env,
               definition.body_module, instance->span, dimensions, value});
        for (const auto &[indices, element] : elements) {
          auto name = path;
          for (auto index : indices)
            name += "[" + std::to_string(index) + "]";
          children.push_back(
              {target,
               name,
               ancestry,
               instance_arguments(instance, element),
               env,
               definition.body_module,
               instance->span,
               {},
               override_value(element->sections, definition, env, value)});
        }
      }
      records.push_back(record);
      stack.insert(stack.end(), children.rbegin(), children.rend());
    }
    return object("schema_version", 1, "stage", "expression-evaluation",
                  "array_representation", "unexpanded declaration templates",
                  "instances", records, "warnings", warnings,
                  "remaining_stages",
                  Json::array({"connectivity-elaboration", "ERC", "export"}));
  }
};
} // namespace
Json evaluate_templates(const Project &project,
                        const Definitions &definitions) {
  return TemplateEvaluator(project, definitions).run();
}
} // namespace noscheme
