#include "noscheme/parameters.hpp"
#include "noscheme/units.hpp"
#include "noscheme/validation.hpp"
#include <algorithm>
#include <set>

namespace noscheme {
std::vector<const ast::Argument *>
instance_arguments(const ast::Instance *node,
                   const ast::ElementOverride *element) {
  std::vector<const ast::Argument *> result;
  std::map<std::string, std::size_t> positions;
  for (const auto *sections : {node ? &node->sections : nullptr,
                               element ? &element->sections : nullptr})
    if (sections)
      for (const auto &entry : ast::section_entries(*sections, "params")) {
        const auto &arg = ast::as<ast::Argument>(*entry);
        const auto &name = arg.name.text;
        if (positions.contains(name))
          result[positions.at(name)] = &arg;
        else {
          positions[name] = result.size();
          result.push_back(&arg);
        }
      }
  return result;
}

namespace {
using ast::as;
using ast::Kind;
[[noreturn]] void fail(const std::string &code, const std::string &message,
                       const Span &span) {
  const Symbols symbols;
  Evaluator(symbols).error(code, message, span);
}
const Parameter *parameter(const ParameterDefinition &definition,
                           const std::string &name) {
  auto found = std::ranges::find(definition.parameters, name, &Parameter::name);
  return found == definition.parameters.end() ? nullptr : &*found;
}
std::optional<DefinitionKey> parent_key(const Project &project,
                                        const std::string &module,
                                        const ast::Definition &source) {
  const auto &base = source.base;
  if (base.empty())
    return {};
  if (source.keyword == "nettype") {
    const auto &symbols = project.modules.at(module).symbols;
    auto found = symbols.find(
        ast::dotted(std::vector<ast::Name>(base.begin(), base.end() - 1)));
    if (found != symbols.end() && found->second.module == "noscheme" &&
        found->second.name == "net") {
      static const std::set<std::string> types{
          "tristate",      "passive",  "digital",    "analog",
          "power",         "ground",   "open_drain", "open_source",
          "bidirectional", "unchecked"};
      if (!types.contains(base.back().text))
        fail("metadata.enum", "unknown builtin net type", base.back().span);
      return {};
    }
  }
  return definition_symbol(project, module, base, {source.keyword});
}
// The one parameter without a default, or the only parameter.
std::optional<std::string>
unnamed_parameter(const ParameterDefinition &target) {
  std::vector<const Parameter *> candidates;
  for (const auto &parameter : target.parameters)
    if (!parameter.default_value)
      candidates.push_back(&parameter);
  if (candidates.empty())
    for (const auto &parameter : target.parameters)
      candidates.push_back(&parameter);
  if (candidates.size() != 1)
    return {};
  return candidates.front()->name;
}
void apply(ParameterDefinition &definition, const ast::Definition &source,
           const std::string &module, bool capture_part_value,
           const ParameterDefinition *parent) {
  // Process params before defaults regardless of section order.
  for (const auto &item : source.body) {
    auto *section = as<ast::Section>(item);
    if (!section || section->name != "params")
      continue;
    for (const auto &entry : section->entries) {
      const auto &field = as<ast::Field>(*entry);
      const auto *type = as<ast::Name>(field.value);
      if (!type || (type->text != "number" && type->text != "string" &&
                    !quantity_units.contains(type->text)))
        fail("resolve.parameter-type", "unknown parameter type", field.span);
      const auto &name = field.key.first.text;
      if (!is_identifier(name))
        fail("resolve.parameter-name", "parameter name must be an identifier",
             field.key.span);
      Parameter item{name, type->text, {}, field.span};
      if (field.default_value)
        item.default_value =
            ScopedExpression{field.default_value.get(), module};
      auto previous =
          std::ranges::find(definition.parameters, name, &Parameter::name);
      if (previous == definition.parameters.end())
        definition.parameters.push_back(std::move(item));
      else
        *previous = std::move(item);
    }
  }
  if (source.parameter) {
    if (!parent)
      fail("resolve.parameter", ast::dotted(source.base) + " has no parameters",
           source.parameter->span);
    std::set<std::string> named;
    for (const auto &item : source.body)
      if (auto *section = as<ast::Section>(item);
          section && section->name == "defaults")
        for (const auto *field : ast::field_list(section->entries))
          named.insert(field->key.first.text);
    auto name = unnamed_target(*parent, *source.parameter, named);
    std::ranges::find(definition.parameters, name, &Parameter::name)
        ->default_value = ScopedExpression{source.parameter.get(), module};
  }
  for (const auto &item : source.body) {
    auto *section = as<ast::Section>(item);
    if (!section)
      continue;
    if (section->name == "defaults") {
      for (const auto &entry : section->entries) {
        const auto &field = as<ast::Field>(*entry);
        const auto &name = field.key.first.text;
        auto found =
            std::ranges::find(definition.parameters, name, &Parameter::name);
        if (found == definition.parameters.end() || field.key.last ||
            !field.value || !field.fields.empty())
          fail("resolve.default",
               "defaults must bind existing parameters to expressions",
               field.span);
        found->default_value = ScopedExpression{field.value.get(), module};
      }
    }
    if (capture_part_value && section->name == "metadata")
      for (const auto &entry : section->entries) {
        const auto &field = as<ast::Field>(*entry);
        if (field.key.first.text == "value") {
          if (!field.value)
            fail("metadata.value", "value requires an expression", field.span);
          definition.part_value = ScopedExpression{field.value.get(), module};
        }
      }
  }
}
// Iterative walk: dependency discovery must not exhaust the C++ call stack.
std::set<std::string> references(const ast::Node &expression) {
  std::vector<const ast::Node *> pending{&expression};
  std::set<std::string> result;
  while (!pending.empty()) {
    const auto *expr = pending.back();
    pending.pop_back();
    switch (expr->kind) {
    case Kind::Name:
      result.insert(as<ast::Name>(*expr).text);
      break;
    case Kind::Group:
      pending.push_back(as<ast::Group>(*expr).expression.get());
      break;
    case Kind::Unary:
      pending.push_back(as<ast::Unary>(*expr).operand.get());
      break;
    case Kind::Binary:
      pending.push_back(as<ast::Binary>(*expr).left.get());
      pending.push_back(as<ast::Binary>(*expr).right.get());
      break;
    case Kind::Call: {
      const auto &call = as<ast::Call>(*expr);
      for (const auto &arg : call.arguments)
        pending.push_back(arg.get());
      for (const auto &arg : call.keywords)
        pending.push_back(arg.value.get());
      if (auto *callee = as<ast::Member>(call.callee);
          callee && callee->name.text == "format")
        pending.push_back(callee->base.get());
      break;
    }
    default:
      break;
    }
  }
  return result;
}
} // namespace
DefinitionKey definition_symbol(const Project &project,
                                const std::string &module,
                                const std::vector<const ast::Name *> &names,
                                const std::vector<std::string> &kinds) {
  auto name = ast::dotted(names);
  const auto &symbols = project.modules.at(module).symbols;
  auto symbol = symbols.find(name);
  if (symbol == symbols.end() && names.size() > 1 &&
      symbols.contains(names.front()->text))
    fail("resolve.private",
         "qualified access to definition internals is not allowed",
         names.back()->span);
  if (symbol == symbols.end() || !symbol->second.declaration)
    fail("resolve.missing-definition", "unknown definition '" + name + "'",
         names.front()->span);
  if (std::ranges::find(kinds, symbol->second.declaration->keyword) ==
      kinds.end())
    fail("resolve.kind", "incorrect definition kind for '" + name + "'",
         names.front()->span);
  return {symbol->second.module, symbol->second.name};
}
DefinitionKey definition_symbol(const Project &project,
                                const std::string &module,
                                const std::vector<ast::Name> &names,
                                const std::vector<std::string> &kinds) {
  std::vector<const ast::Name *> pointers;
  for (const auto &name : names)
    pointers.push_back(&name);
  return definition_symbol(project, module, pointers, kinds);
}
void walk_definitions(const Project &project,
                      const DefinitionVisitor &visitor) {
  std::map<DefinitionKey, const ast::Definition *> sources;
  for (const auto &[name, module] : project.modules)
    for (const auto &item : module.ast->items)
      if (auto *definition = as<ast::Definition>(item))
        sources[{name, definition->name.text}] = definition;
  std::set<DefinitionKey> complete;
  for (const auto &[start, unused] : sources) {
    std::vector<DefinitionKey> chain;
    std::set<DefinitionKey> active;
    auto current = start;
    while (!complete.contains(current)) {
      if (!active.insert(current).second)
        fail("resolve.inheritance-cycle", "inheritance cycle",
             sources.at(current)->span);
      chain.push_back(current);
      auto parent = parent_key(project, current.first, *sources.at(current));
      if (!parent)
        break;
      current = *parent;
    }
    for (auto it = chain.rbegin(); it != chain.rend(); ++it) {
      const auto &source = *sources.at(*it);
      auto parent = parent_key(project, it->first, source);
      visitor(*it, source, parent);
      complete.insert(*it);
    }
  }
}
ParameterDefinition definition_parameters(const DefinitionKey &key,
                                          const ast::Definition &source,
                                          const ParameterDefinition *parent,
                                          bool capture_part_value) {
  auto result = parent ? *parent : ParameterDefinition{};
  result.key = key;
  apply(result, source, key.first, capture_part_value, parent);
  if (!parent || !parent->unnamed)
    result.unnamed = unnamed_parameter(result);
  return result;
}
std::string unnamed_target(const ParameterDefinition &target,
                           const ast::Node &value,
                           const std::set<std::string> &named) {
  const auto &name = target.key.second;
  if (!target.unnamed) {
    std::vector<std::string> candidates;
    for (const auto &parameter : target.parameters)
      if (!parameter.default_value)
        candidates.push_back(parameter.name);
    if (candidates.empty())
      for (const auto &parameter : target.parameters)
        candidates.push_back(parameter.name);
    std::string listed;
    for (const auto &candidate : candidates)
      listed += (listed.empty() ? "" : ", ") + candidate;
    fail("resolve.parameter",
         candidates.empty()
             ? name + " has no parameters"
             : name +
                   " has several parameters an unnamed value could set; "
                   "name one of: " +
                   listed,
         value.span);
  }
  if (named.contains(*target.unnamed))
    fail("resolve.parameter",
         "parameter '" + *target.unnamed + "' is set twice", value.span);
  return *target.unnamed;
}
ParameterDefinitions resolve_parameters(const Project &project) {
  ParameterDefinitions result;
  walk_definitions(
      project, [&](const auto &key, const auto &source, const auto &parent) {
        result.emplace(key, definition_parameters(
                                key, source,
                                parent ? &result.at(*parent) : nullptr, true));
      });
  return result;
}
Json parameter_summary(const ParameterDefinitions &definitions) {
  auto result = Json::array();
  for (const auto &[key, definition] : definitions) {
    auto params = Json::object();
    for (const auto &param : definition.parameters) {
      Json value = nullptr;
      if (param.default_value)
        value = object("value", ast::to_json(*param.default_value->expression),
                       "span", param.default_value->expression->span.json(),
                       "module", param.default_value->module);
      params[param.name] = object("type", param.type, "default",
                                  std::move(value), "span", param.span.json());
    }
    result.push_back(object("module", key.first, "name", key.second,
                            "parameters", std::move(params)));
  }
  return result;
}
[[noreturn]] void ParameterEvaluator::error(const std::string &code,
                                            const std::string &message,
                                            const Span &span,
                                            const Json &related) {
  const Symbols symbols;
  Evaluator evaluator(symbols, limits);
  evaluator.instance_path = instance_path;
  evaluator.error(code, message, span, related);
}
Value ParameterEvaluator::evaluate(const ScopedExpression &expression,
                                   const Environment &environment) {
  Evaluator evaluator(project.modules.at(expression.module).symbols, limits);
  evaluator.instance_path = instance_path;
  try {
    auto result = evaluator.evaluate(*expression.expression, environment);
    warnings.insert(warnings.end(), evaluator.warnings.begin(),
                    evaluator.warnings.end());
    return result;
  } catch (const DiagnosticsFailure &) {
    warnings.insert(warnings.end(), evaluator.warnings.begin(),
                    evaluator.warnings.end());
    throw;
  }
}
Environment
ParameterEvaluator::bind(const ParameterDefinition &definition,
                         const std::vector<const ast::Argument *> &arguments,
                         const std::string &caller_module,
                         const Environment &caller_environment,
                         const Span &span) {
  Environment bound;
  std::map<std::string, const ast::Argument *> supplied;
  std::vector<std::string> order;
  for (const auto *arg : arguments) {
    const auto &name = arg->name.text;
    if (!parameter(definition, name) || supplied.contains(name))
      error("parameter.argument",
            "unknown or duplicate argument '" + name + "'", arg->span);
    supplied[name] = arg;
    bound[name] =
        evaluate({arg->value.get(), caller_module}, caller_environment);
    order.push_back(name);
  }
  std::set<std::string> pending;
  std::map<std::string, std::set<std::string>> dependencies;
  for (const auto &param : definition.parameters) {
    if (bound.contains(param.name))
      continue;
    if (!param.default_value)
      error("parameter.required",
            "missing required parameter '" + param.name + "'", span,
            Json::array({object("message", "parameter declared here", "span",
                                param.span.json())}));
    pending.insert(param.name);
    dependencies[param.name] = references(*param.default_value->expression);
  }
  while (!pending.empty()) {
    std::vector<std::string> ready;
    for (const auto &name : pending)
      if (std::ranges::none_of(dependencies.at(name), [&](const auto &dep) {
            return pending.contains(dep);
          }))
        ready.push_back(name);
    if (ready.empty()) {
      std::string names;
      for (const auto &name : pending) {
        if (!names.empty())
          names += ", ";
        names += name;
      }
      error("parameter.cycle", "cyclic parameter defaults: " + names,
            parameter(definition, *pending.begin())
                ->default_value->expression->span);
    }
    for (const auto &name : ready) {
      bound[name] =
          evaluate(*parameter(definition, name)->default_value, bound);
      order.push_back(name);
      pending.erase(name);
    }
  }
  for (const auto &name : order) {
    const auto &param = *parameter(definition, name);
    bool string = std::holds_alternative<std::string>(bound.at(name));
    if ((param.type == "string" && !string) ||
        (param.type == "number" && string)) {
      const auto &where = supplied.contains(name)
                              ? supplied.at(name)->span
                              : param.default_value->expression->span;
      error("parameter.type", "parameter '" + name + "' requires " + param.type,
            where,
            Json::array({object("message", "parameter declared here", "span",
                                param.span.json())}));
    }
  }
  return bound;
}
std::optional<Value>
ParameterEvaluator::part_value(const ParameterDefinition &definition,
                               const Environment &parameters) {
  std::string name = "value";
  std::optional<Value> result;
  if (definition.part_value) {
    result = evaluate(*definition.part_value, parameters);
    const auto *expr = as<ast::Name>(definition.part_value->expression);
    name = expr ? expr->text : "";
  } else if (parameters.contains(name))
    result = parameters.at(name);
  if (result) {
    auto param = parameter(definition, name);
    if (param && quantity_units.contains(param->type) &&
        !std::holds_alternative<std::string>(*result))
      return format_quantity(*result, param->type);
  }
  return result;
}
} // namespace noscheme
