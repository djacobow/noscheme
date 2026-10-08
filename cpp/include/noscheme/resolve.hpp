#pragma once
#include "noscheme/metadata.hpp"
#include "noscheme/parameters.hpp"

namespace noscheme {
using MetadataOrder =
    std::map<std::vector<std::string>, std::vector<std::string>>;
struct Interface {
  std::string name;
  Json type = nullptr, metadata = Json::object(), span;
  MetadataOrder metadata_order;
};
struct Definition {
  DefinitionKey key;
  std::string kind, body_module;
  // The project owns source/body ASTs and must outlive resolved definitions.
  const Json *source = nullptr;
  std::vector<const Json *> body;
  // Expanded nodes are owned here; unmodified nodes still belong to the
  // project.
  std::vector<std::shared_ptr<const Json>> expanded_body;
  std::size_t resolution_order = 0;
  ParameterDefinition parameters;
  std::map<std::string, Interface> interface;
  std::vector<std::string> interface_order;
  std::map<std::string, std::string> aliases;
  // Preserve declaration order across metadata overrides for diagnostics.
  MetadataOrder metadata_order;
  Json metadata = Json::object(), electrical = nullptr;
};
using Definitions = std::map<DefinitionKey, Definition>;
// Resolve inheritance and public contracts. Instance bodies and net-type
// references are validated by a subsequent stage, not this entry point.
Definitions resolve_interfaces(const Project &project,
                               const RegistryDescription &registry,
                               std::size_t max_pins = 100000);
std::vector<std::string> physical_keys(const Json &key,
                                       std::size_t max_pins = 100000);
void resolve_bodies(const Project &project, const RegistryDescription &registry,
                    Definitions &definitions, std::size_t max_pins = 100000,
                    std::size_t max_expression_depth = 128);
Definitions resolve_all(const Project &project,
                        const RegistryDescription &registry,
                        std::size_t max_pins = 100000,
                        std::size_t max_expression_depth = 128);
const Json &section_entries(const Json &node, std::string_view name);
const Definition *type_definition(const Project &project,
                                  const RegistryDescription &registry,
                                  const Definitions &definitions,
                                  const Json &value);
std::set<std::string> possible_selections(const Json &wrapped);
bool attached_role(const Definition &definition, const std::string &pin);
std::vector<std::string>
metadata_field_order(const Json &values, const MetadataOrder *orders = nullptr,
                     const std::vector<std::string> &path = {});
Json interface_summary(const Definitions &definitions);
} // namespace noscheme
