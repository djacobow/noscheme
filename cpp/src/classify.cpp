#include "noscheme/classify.hpp"
#include "noscheme/validation.hpp"
#include <algorithm>

namespace noscheme {
namespace {
using ast::as;
[[noreturn]] void fail(const std::string &code, const std::string &message,
                       const Span &span) {
  throw DiagnosticsFailure(Json::array({error_json(code, message, span)}));
}
bool is_reference(const ast::Node *value) {
  if (auto *member = as<ast::Member>(value))
    return is_reference(member->base.get());
  if (auto *subscript = as<ast::Subscript>(value))
    return is_reference(subscript->base.get());
  return as<ast::Name>(value) != nullptr;
}
[[noreturn]] void not_a_target(const ast::Node &node) {
  fail("syntax.target", "a connection target is a net reference or flat(net)",
       target_error_node(node).span);
}
// The fields of a net or bundle member: its body lines, with no instance parts.
std::vector<ast::Field> net_fields(const ast::Declaration &declaration,
                                   const std::string &noun) {
  for (const auto *span : {&declaration.parameters, &declaration.arguments})
    if (*span)
      fail("syntax.net-declaration",
           "a " + noun + " takes no parameters or connections", **span);
  auto fields = declaration.fields;
  for (const auto &section : declaration.sections) {
    if (section.name != "connections")
      fail("syntax.net-declaration",
           "a " + noun + " has no " + section.name + " section", section.span);
    for (const auto &entry : section.entries) {
      const auto &binding = as<ast::Binding>(*entry);
      if (!binding.endpoint.instance_indices.empty() ||
          !binding.endpoint.selectors.empty() || as<ast::Call>(binding.target))
        fail("syntax.net-declaration",
             "a " + noun + " has fields, not connections", binding.span);
      ast::Field field;
      field.span = binding.span;
      field.key = binding.endpoint.head;
      field.value = binding.target;
      fields.push_back(std::move(field));
    }
  }
  std::stable_sort(fields.begin(), fields.end(),
                   [](const ast::Field &a, const ast::Field &b) {
                     return a.span.start < b.span.start;
                   });
  if (auto errors = field_errors(ast::field_list(fields)); !errors.empty())
    throw DiagnosticsFailure(std::move(errors));
  return fields;
}
} // namespace

bool is_target(const ast::Node *value) {
  if (auto *call = as<ast::Call>(value)) {
    auto *callee = as<ast::Name>(call->callee);
    return callee && callee->text == "flat" && call->arguments.size() == 1 &&
           call->keywords.empty() && is_reference(call->arguments[0].get());
  }
  return is_reference(value);
}
const ast::Node &target_error_node(const ast::Node &value) {
  if (auto *call = as<ast::Call>(&value))
    if (auto *callee = as<ast::Name>(call->callee))
      return *callee;
  return value;
}
ast::Ptr typed(ast::Ptr type, const ast::Declarator &declarator) {
  for (const auto &index : declarator.dimensions) {
    auto subscript = std::make_shared<ast::Subscript>();
    subscript->span = {type->span.source, type->span.start, index.span.end};
    subscript->base = std::move(type);
    subscript->selection = index;
    type = std::move(subscript);
  }
  return type;
}
ast::FieldKey declared_key(const ast::Name &name) {
  ast::FieldKey key;
  key.span = name.span;
  key.first.span = name.span;
  key.first.text = key.first.spelling = name.text;
  return key;
}
ast::Ptr path_expression(const std::vector<ast::Name> &path) {
  ast::Ptr result = std::make_shared<ast::Name>(path.front());
  for (std::size_t i = 1; i < path.size(); ++i) {
    auto member = std::make_shared<ast::Member>();
    member->span = {result->span.source, result->span.start, path[i].span.end};
    member->base = std::move(result);
    member->name = path[i];
    result = std::move(member);
  }
  return result;
}
std::vector<ast::Field> member_fields(const ast::Declaration &declaration) {
  if (declaration.global)
    fail("syntax.net-declaration", "a bundle member is not global",
         declaration.span);
  auto fields = net_fields(declaration, "bundle member");
  auto type = path_expression(declaration.type);
  std::vector<ast::Field> result;
  for (const auto &declarator : declaration.declarators) {
    ast::Field field;
    field.span = declaration.span;
    field.key = declared_key(declarator.name);
    field.value = typed(type, declarator);
    field.fields = fields;
    result.push_back(std::move(field));
  }
  return result;
}
std::vector<ast::Ptr> classify(const ast::Declaration &declaration, bool net) {
  std::vector<ast::Ptr> result;
  if (net) {
    auto fields = net_fields(declaration, "net");
    auto type = path_expression(declaration.type);
    for (const auto &declarator : declaration.declarators) {
      auto node = std::make_shared<ast::Net>();
      node->span = declaration.span;
      node->name = declarator.name;
      node->type = typed(type, declarator);
      node->global = declaration.global;
      node->fields = fields;
      result.push_back(std::move(node));
    }
    return result;
  }
  if (declaration.global || declaration.declarators.size() > 1)
    fail("syntax.instance-declaration",
         "an instance is declared alone, and only nets are global",
         declaration.span);
  if (!declaration.fields.empty()) {
    const auto &field = declaration.fields.front();
    if (field.value)
      not_a_target(*field.value);
    not_a_target(field.key);
  }
  const auto &declarator = declaration.declarators.front();
  auto node = std::make_shared<ast::Instance>();
  node->span = declaration.span;
  node->name = declarator.name;
  node->dimensions = declarator.dimensions;
  node->component = declaration.type;
  node->sections = declaration.sections;
  node->positional = declaration.positional;
  node->wildcard = declaration.wildcard;
  node->parameter = declaration.parameter;
  result.push_back(std::move(node));
  return result;
}
} // namespace noscheme
