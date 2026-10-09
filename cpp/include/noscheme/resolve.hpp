#pragma once
#include "noscheme/metadata.hpp"
#include "noscheme/parameters.hpp"

namespace noscheme {
using MetadataOrder =
    std::map<std::vector<std::string>, std::vector<std::string>>;
struct Interface {
  std::string name;
  // Ports and bundle fields have a net type; physical pins do not.
  ScopedExpression type;
  MetaFields metadata;
  Span span;
  MetadataOrder metadata_order;
};
struct Definition {
  DefinitionKey key;
  std::string kind, body_module;
  // The project owns source/body ASTs and must outlive resolved definitions.
  const ast::Definition *source = nullptr;
  // Nets and Instances.
  std::vector<const ast::Node *> body;
  // Expanded nodes are owned here; unmodified nodes still belong to the
  // project.
  std::vector<std::shared_ptr<const ast::Node>> expanded_body;
  std::size_t resolution_order = 0;
  ParameterDefinition parameters;
  std::map<std::string, Interface> interface;
  std::vector<std::string> interface_order;
  std::map<std::string, std::string> aliases;
  // Preserve declaration order across metadata overrides for diagnostics.
  MetadataOrder metadata_order;
  MetaFields metadata;
  Json electrical = nullptr;
};
using Definitions = std::map<DefinitionKey, Definition>;
// Resolve inheritance and public contracts. Instance bodies and net-type
// references are validated by a subsequent stage, not this entry point.
Definitions resolve_interfaces(const Project &project,
                               const RegistryDescription &registry,
                               std::size_t max_pins = 100000);
std::vector<std::string> physical_keys(const ast::FieldKey &key,
                                       std::size_t max_pins = 100000);
void resolve_bodies(const Project &project, const RegistryDescription &registry,
                    Definitions &definitions, std::size_t max_pins = 100000,
                    std::size_t max_expression_depth = 128);
Definitions resolve_all(const Project &project,
                        const RegistryDescription &registry,
                        std::size_t max_pins = 100000,
                        std::size_t max_expression_depth = 128);
const Definition *type_definition(const Project &project,
                                  const RegistryDescription &registry,
                                  const Definitions &definitions,
                                  const ScopedExpression &type);
std::set<std::string> possible_selections(const Meta &wrapped);
bool attached_role(const Definition &definition, const std::string &pin);
std::vector<std::string>
metadata_field_order(const MetaFields &values,
                     const MetadataOrder *orders = nullptr,
                     const std::vector<std::string> &path = {});
Json interface_summary(const Definitions &definitions);
} // namespace noscheme
