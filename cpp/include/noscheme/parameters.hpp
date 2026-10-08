#pragma once
#include "noscheme/evaluate.hpp"
#include <optional>

namespace noscheme {
using DefinitionKey = std::pair<std::string, std::string>;
struct ScopedExpression {
  Json expression;
  std::string module;
};
struct Parameter {
  std::string name, type;
  std::optional<ScopedExpression> default_value;
  Json span;
};
// A projection of definition headers, not a fully validated definition.
// Declaration order is retained for deterministic missing-parameter
// diagnostics.
struct ParameterDefinition {
  DefinitionKey key;
  std::vector<Parameter> parameters;
  std::optional<ScopedExpression> part_value;
};
using ParameterDefinitions = std::map<DefinitionKey, ParameterDefinition>;
DefinitionKey definition_symbol(const Project &project,
                                const std::string &module, const Json &names,
                                const std::vector<std::string> &kinds);
// Shared iterative inheritance traversal: visit each parent before its
// children.
using DefinitionVisitor = std::function<void(
    const DefinitionKey &, const Json &, const std::optional<DefinitionKey> &)>;
void walk_definitions(const Project &project, const DefinitionVisitor &visitor);
ParameterDefinition
definition_parameters(const DefinitionKey &key, const Json &source,
                      const ParameterDefinition *parent = nullptr,
                      bool capture_part_value = false);
ParameterDefinitions resolve_parameters(const Project &project);
Json parameter_summary(const ParameterDefinitions &definitions);

class ParameterEvaluator {
  const Project &project;
  Value evaluate(const ScopedExpression &expression,
                 const Environment &environment);
  [[noreturn]] void error(const std::string &code, const std::string &message,
                          const Json &span,
                          const Json &related = Json::array());

public:
  Limits limits;
  Json warnings = Json::array(), instance_path = nullptr;
  explicit ParameterEvaluator(const Project &project, Limits limits = {})
      : project(project), limits(limits) {}
  Environment bind(const ParameterDefinition &definition, const Json &arguments,
                   const std::string &caller_module,
                   const Environment &caller_environment, const Json &span);
  std::optional<Value> part_value(const ParameterDefinition &definition,
                                  const Environment &parameters);
};
} // namespace noscheme
