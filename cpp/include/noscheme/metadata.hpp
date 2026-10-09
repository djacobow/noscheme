#pragma once
#include "noscheme/evaluate.hpp"
#include "noscheme/registry.hpp"
#include <set>

namespace noscheme {
// Readers return typed metadata (meta.hpp): each field keeps its source span
// and module. Expressions refer to the AST; numeric expressions also carry a
// constraint for elaboration to check.
class MetadataReader {
  using Fields = std::vector<const ast::Field *>;
  const Project &project;
  const RegistryDescription &registry;
  std::string module;
  std::set<std::string> locals;
  MetaFields read_fields(const Fields &fields, const Json &schema,
                         const std::string &scope, unsigned depth);
  MetaValue literal(const ast::Node &expr, unsigned depth = 0);
  MetaValue read_value(const Json &schema, const ast::Node &expr,
                       const std::string &scope);
  MetaValue roles(const std::vector<const ast::Node *> &items,
                  const Span &span);
  std::string enumeration(const ast::Node &expr, const std::string &space,
                          const Json &values);
  Meta wrapped(MetaValue value, const Span &span) const;
  const Symbol *namespace_symbol(const ast::Node &expr,
                                 bool respect_locals = true) const;
  Fields mapping(const ast::Field &field) const;
  std::string choice_string(const ast::Node *expr) const;
  // choice(name, alternative=value, ...): a value selected by a local choice.
  MetaFields selection(const ast::Node &expr, bool roles = false);
  MetaFields choices(const ast::Field &field, const std::string &kind);
  MetaFields waivers(const ast::Field &field);
  MetaFields checks(const ast::Field &field, const std::string &scope);
  std::string registered_name(const ast::Node *expr) const;
  const Json *endpoint_schema(const std::string &name, bool net = false) const;
  MetaList selector(const ast::Node &expr);
  MetaValue argument(const Json &schema, const ast::Node &expr);
  [[noreturn]] void error(const std::string &code, const std::string &message,
                          const Span &span) const;

public:
  MetadataReader(const Project &project, const RegistryDescription &registry,
                 std::string module, std::set<std::string> locals = {});
  MetaFields read(const Fields &fields, const std::string &scope);
  MetaFields read(const std::vector<ast::Field> &fields,
                  const std::string &scope) {
    return read(ast::field_list(fields), scope);
  }
  MetaFields read(const std::vector<ast::Ptr> &entries,
                  const std::string &scope) {
    return read(ast::field_list(entries), scope);
  }
  MetaFields pin(const ast::Field &field, const std::string &scope);
};
} // namespace noscheme
