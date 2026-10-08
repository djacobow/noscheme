#include "noscheme/parameters.hpp"
#include "noscheme/units.hpp"
#include "noscheme/validation.hpp"
#include <algorithm>
#include <set>

namespace noscheme {
namespace {
[[noreturn]] void fail(const std::string &code, const std::string &message,
                       const Json &span) {
  const Symbols symbols;
  Evaluator(symbols).error(code, message, span);
}
std::string spelling(const Json &names) {
  std::string result;
  for (const auto &name : names) {
    if (!result.empty())
      result += ".";
    result += name.at("text").get<std::string>();
  }
  return result;
}
const Parameter *parameter(const ParameterDefinition &definition,
                           const std::string &name) {
  auto found = std::ranges::find(definition.parameters, name, &Parameter::name);
  return found == definition.parameters.end() ? nullptr : &*found;
}
std::optional<DefinitionKey> parent_key(const Project &project,
                                        const std::string &module,
                                        const Json &source) {
  const auto &base = source.at("base");
  if (base.is_null() || base.empty())
    return {};
  if (source.at("kind") == "nettype") {
    auto prefix = base;
    prefix.erase(prefix.end() - 1);
    const auto &symbols = project.modules.at(module).symbols;
    auto found = symbols.find(spelling(prefix));
    if (found != symbols.end() && found->second.module == "noscheme" &&
        found->second.name == "nettype") {
      static const std::set<std::string> types{
          "tristate",      "passive",  "digital",    "analog",
          "power",         "ground",   "open_drain", "open_source",
          "bidirectional", "unchecked"};
      if (!types.contains(base.back().at("text")))
        fail("metadata.enum", "unknown builtin net type",
             base.back().at("span"));
      return {};
    }
  }
  return definition_symbol(project, module, base, {source.at("kind")});
}
void apply(ParameterDefinition &definition, const Json &source,
           const std::string &module, bool capture_part_value) {
  // Process params before defaults regardless of section order.
  for (const auto &section : source.at("body")) {
    if (section.at("node") != "Section" || section.at("name") != "params")
      continue;
    for (const auto &field : section.at("entries")) {
      const auto &value = field.at("value");
      if (value.is_null() || value.at("node") != "Name" ||
          (value.at("text") != "number" && value.at("text") != "string" &&
           !quantity_units.contains(value.at("text").get<std::string>())))
        fail("resolve.parameter-type", "unknown parameter type",
             field.at("span"));
      const auto &key = field.at("key");
      auto name = key.at("first").at("text").get<std::string>();
      if (!is_identifier(name))
        fail("resolve.parameter-name", "parameter name must be an identifier",
             key.at("span"));
      Parameter item{name, value.at("text"), {}, field.at("span")};
      if (!field.at("default").is_null())
        item.default_value = ScopedExpression{field.at("default"), module};
      auto previous =
          std::ranges::find(definition.parameters, name, &Parameter::name);
      if (previous == definition.parameters.end())
        definition.parameters.push_back(std::move(item));
      else
        *previous = std::move(item);
    }
  }
  for (const auto &section : source.at("body")) {
    if (section.at("node") != "Section")
      continue;
    if (section.at("name") == "defaults") {
      for (const auto &field : section.at("entries")) {
        auto name = field.at("key").at("first").at("text").get<std::string>();
        auto found =
            std::ranges::find(definition.parameters, name, &Parameter::name);
        if (found == definition.parameters.end() ||
            !field.at("key").at("last").is_null() ||
            field.at("value").is_null() || !field.at("fields").empty())
          fail("resolve.default",
               "defaults must bind existing parameters to expressions",
               field.at("span"));
        found->default_value = ScopedExpression{field.at("value"), module};
      }
    }
    if (capture_part_value && section.at("name") == "metadata")
      for (const auto &field : section.at("entries"))
        if (field.at("key").at("first").at("text") == "value") {
          if (field.at("value").is_null())
            fail("metadata.value", "value requires an expression",
                 field.at("span"));
          definition.part_value = ScopedExpression{field.at("value"), module};
        }
  }
}
// Iterative walk: dependency discovery must not exhaust the C++ call stack.
std::set<std::string> references(const Json &expression) {
  std::vector<const Json *> pending{&expression};
  std::set<std::string> result;
  while (!pending.empty()) {
    const auto &expr = *pending.back();
    pending.pop_back();
    const auto &kind = expr.at("node");
    if (kind == "Name")
      result.insert(expr.at("text"));
    else if (kind == "Group")
      pending.push_back(&expr.at("expression"));
    else if (kind == "Unary")
      pending.push_back(&expr.at("operand"));
    else if (kind == "Binary") {
      pending.push_back(&expr.at("left"));
      pending.push_back(&expr.at("right"));
    } else if (kind == "Call") {
      for (const auto &arg : expr.at("arguments"))
        pending.push_back(&arg);
      for (const auto &arg : expr.at("keywords"))
        pending.push_back(&arg.at("value"));
      const auto &callee = expr.at("callee");
      if (callee.at("node") == "Member" &&
          callee.at("name").at("text") == "format")
        pending.push_back(&callee.at("base"));
    }
  }
  return result;
}
} // namespace
DefinitionKey definition_symbol(const Project &project,
                                const std::string &module, const Json &names,
                                const std::vector<std::string> &kinds) {
  auto name = spelling(names);
  const auto &symbols = project.modules.at(module).symbols;
  auto symbol = symbols.find(name);
  if (symbol == symbols.end() && names.size() > 1 &&
      symbols.contains(names.front().at("text")))
    fail("resolve.private",
         "qualified access to definition internals is not allowed",
         names.back().at("span"));
  if (symbol == symbols.end() || !symbol->second.declaration)
    fail("resolve.missing-definition", "unknown definition '" + name + "'",
         names.front().at("span"));
  if (std::ranges::find(
          kinds, symbol->second.declaration->at("kind").get<std::string>()) ==
      kinds.end())
    fail("resolve.kind", "incorrect definition kind for '" + name + "'",
         names.front().at("span"));
  return {symbol->second.module, symbol->second.name};
}
void walk_definitions(const Project &project,
                      const DefinitionVisitor &visitor) {
  std::map<DefinitionKey, const Json *> sources;
  for (const auto &[name, module] : project.modules)
    for (const auto &item : module.ast->at("items"))
      if (item.at("node") == "Definition")
        sources[{name, item.at("name").at("text")}] = &item;
  std::set<DefinitionKey> complete;
  for (const auto &[start, unused] : sources) {
    std::vector<DefinitionKey> chain;
    std::set<DefinitionKey> active;
    auto current = start;
    while (!complete.contains(current)) {
      if (!active.insert(current).second)
        fail("resolve.inheritance-cycle", "inheritance cycle",
             sources.at(current)->at("span"));
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
                                          const Json &source,
                                          const ParameterDefinition *parent,
                                          bool capture_part_value) {
  auto result = parent ? *parent : ParameterDefinition{};
  result.key = key;
  apply(result, source, key.first, capture_part_value);
  return result;
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
        value = object("value", param.default_value->expression, "span",
                       param.default_value->expression.at("span"), "module",
                       param.default_value->module);
      params[param.name] = object("type", param.type, "default",
                                  std::move(value), "span", param.span);
    }
    result.push_back(object("module", key.first, "name", key.second,
                            "parameters", std::move(params)));
  }
  return result;
}
[[noreturn]] void ParameterEvaluator::error(const std::string &code,
                                            const std::string &message,
                                            const Json &span,
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
    auto result = evaluator.evaluate(expression.expression, environment);
    warnings.insert(warnings.end(), evaluator.warnings.begin(),
                    evaluator.warnings.end());
    return result;
  } catch (const DiagnosticsFailure &) {
    warnings.insert(warnings.end(), evaluator.warnings.begin(),
                    evaluator.warnings.end());
    throw;
  }
}
Environment ParameterEvaluator::bind(const ParameterDefinition &definition,
                                     const Json &arguments,
                                     const std::string &caller_module,
                                     const Environment &caller_environment,
                                     const Json &span) {
  Environment bound;
  std::map<std::string, const Json *> supplied;
  std::vector<std::string> order;
  for (const auto &arg : arguments) {
    auto name = arg.at("name").at("text").get<std::string>();
    if (!parameter(definition, name) || supplied.contains(name))
      error("parameter.argument",
            "unknown or duplicate argument '" + name + "'", arg.at("span"));
    supplied[name] = &arg;
    bound[name] =
        evaluate({arg.at("value"), caller_module}, caller_environment);
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
                                param.span)}));
    pending.insert(param.name);
    dependencies[param.name] = references(param.default_value->expression);
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
                ->default_value->expression.at("span"));
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
                              ? supplied.at(name)->at("span")
                              : param.default_value->expression.at("span");
      error("parameter.type", "parameter '" + name + "' requires " + param.type,
            where,
            Json::array({object("message", "parameter declared here", "span",
                                param.span)}));
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
    const auto &expr = definition.part_value->expression;
    name = expr.at("node") == "Name" ? expr.at("text").get<std::string>() : "";
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
