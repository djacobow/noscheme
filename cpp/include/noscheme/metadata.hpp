#pragma once
#include "noscheme/evaluate.hpp"
#include "noscheme/registry.hpp"
#include <set>

namespace noscheme {
// Internal representation: each field is {value, span, module}. Literal numbers
// use evaluator encodings so JSON does not lose arbitrary-precision integers.
// Expressions retain their AST; numeric expressions also retain a constraint.
class MetadataReader {
  const Project &project;
  const RegistryDescription &registry;
  std::string module;
  std::set<std::string> locals;
  Json read_fields(const Json &fields, const Json &schema,
                   const std::string &scope, unsigned depth);
  Json literal(const Json &expr, unsigned depth = 0);
  Json read_value(const Json &schema, const Json &expr,
                  const std::string &scope);
  Json enumeration(const Json &expr, const std::string &space,
                   const Json &values);
  Json wrapped(Json value, const Json &span) const;
  const Symbol *namespace_symbol(const Json &expr,
                                 bool respect_locals = true) const;
  std::vector<const Json *> mapping(const Json &field) const;
  Json choice_string(const Json &expr) const;
  Json selection(const Json &field, bool roles = false);
  Json choices(const Json &field, const std::string &kind);
  Json waivers(const Json &field);
  Json checks(const Json &field, const std::string &scope);
  std::string registered_name(const Json &expr) const;
  const Json *endpoint_schema(const std::string &name, bool net = false) const;
  Json selector(const Json &expr);
  Json argument(const Json &schema, const Json &expr);
  [[noreturn]] void error(const std::string &code, const std::string &message,
                          const Json &span) const;

public:
  MetadataReader(const Project &project, const RegistryDescription &registry,
                 std::string module, std::set<std::string> locals = {});
  Json read(const Json &fields, const std::string &scope);
  Json pin(const Json &field, const std::string &scope);
};
Json merge_metadata(const Json &parent, const Json &child);
} // namespace noscheme
