#pragma once
#include "noscheme/evaluate.hpp"
#include <optional>
#include <set>

namespace noscheme {
using DefinitionKey = std::pair<std::string, std::string>;
// The expression belongs to the project's AST.
struct ScopedExpression {
  const ast::Node *expression = nullptr;
  std::string module;
};
struct Parameter {
  std::string name, type;
  std::optional<ScopedExpression> default_value;
  Span span;
};
// A projection of definition headers, not a fully validated definition.
// Declaration order is retained for deterministic missing-parameter
// diagnostics.
struct ParameterDefinition {
  DefinitionKey key;
  std::vector<Parameter> parameters;
  std::optional<ScopedExpression> part_value;
  // The parameter that an unnamed `<value>` sets: once a definition has one,
  // its extensions keep it.
  std::optional<std::string> unnamed;
};
// The parameter of target that an unnamed value sets, or a located error.
std::string unnamed_target(const ParameterDefinition &target,
                           const ast::Node &value,
                           const std::set<std::string> &named);
using ParameterDefinitions = std::map<DefinitionKey, ParameterDefinition>;
DefinitionKey definition_symbol(const Project &project,
                                const std::string &module,
                                const std::vector<const ast::Name *> &names,
                                const std::vector<std::string> &kinds);
DefinitionKey definition_symbol(const Project &project,
                                const std::string &module,
                                const std::vector<ast::Name> &names,
                                const std::vector<std::string> &kinds);
// Shared iterative inheritance traversal: visit each parent before its
// children.
using DefinitionVisitor =
    std::function<void(const DefinitionKey &, const ast::Definition &,
                       const std::optional<DefinitionKey> &)>;
void walk_definitions(const Project &project, const DefinitionVisitor &visitor);
ParameterDefinition
definition_parameters(const DefinitionKey &key, const ast::Definition &source,
                      const ParameterDefinition *parent = nullptr,
                      bool capture_part_value = false);
ParameterDefinitions resolve_parameters(const Project &project);
Json parameter_summary(const ParameterDefinitions &definitions);

std::vector<const ast::Argument *>
instance_arguments(const ast::Instance *node,
                   const ast::ElementOverride *element = nullptr);

class ParameterEvaluator {
  const Project &project;
  Value evaluate(const ScopedExpression &expression,
                 const Environment &environment);
  [[noreturn]] void error(const std::string &code, const std::string &message,
                          const Span &span,
                          const Json &related = Json::array());

public:
  Limits limits;
  Json warnings = Json::array(), instance_path = nullptr;
  explicit ParameterEvaluator(const Project &project, Limits limits = {})
      : project(project), limits(limits) {}
  Environment bind(const ParameterDefinition &definition,
                   const std::vector<const ast::Argument *> &arguments,
                   const std::string &caller_module,
                   const Environment &caller_environment, const Span &span);
  std::optional<Value> part_value(const ParameterDefinition &definition,
                                  const Environment &parameters);
};
} // namespace noscheme
