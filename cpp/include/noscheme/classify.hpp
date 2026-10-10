#pragma once
#include "noscheme/ast.hpp"
#include <vector>
namespace noscheme {
// A net reference, or flat(net).
bool is_target(const ast::Node *value);
// The node a "not a connection target" error locates: a call's callee, or the
// value itself.
const ast::Node &target_error_node(const ast::Node &value);
// A declared type with the declarator's array dimensions: net.passive[4][2].
ast::Ptr typed(ast::Ptr type, const ast::Declarator &declarator);
// A dotted path, such as net.power, as a Name or Member expression.
ast::Ptr path_expression(const std::vector<ast::Name> &path);
// The key of a field that declares a name: `number gain` declares gain.
ast::FieldKey declared_key(const ast::Name &name);
// A bundle's `net.passive scl, sda` declares one member Field per name.
std::vector<ast::Field> member_fields(const ast::Declaration &declaration);
// The Nets or the Instance a declaration makes, given whether its type is a
// net type. Errors are thrown as DiagnosticsFailure.
std::vector<ast::Ptr> classify(const ast::Declaration &declaration, bool net);
} // namespace noscheme
