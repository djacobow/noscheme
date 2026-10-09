#pragma once
#include "noscheme/json.hpp"
#include <cstdint>
#include <functional>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <tuple>
#include <vector>

namespace noscheme {
class Source;
// UTF-8 byte offsets into a Source. An AST's Module keeps its Source alive;
// other spans refer to sources that their owner keeps alive in the same way.
struct Span {
  const Source *source = nullptr;
  std::size_t start = 0, end = 0;
  explicit operator bool() const { return source != nullptr; }
  const std::string &filename() const;
  // The diagnostic and probe form, with lines and Unicode columns.
  Json json() const;
  std::tuple<const std::string &, std::size_t, std::size_t> key() const {
    return {filename(), start, end};
  }
  // Sources for the same file compare equal: spans from separate parses of a
  // file still identify the same text.
  bool operator==(const Span &other) const;
};
// A null span for absent values.
Json span_json(const Span &span);

namespace ast {
// Every node kind, in comparison-form spelling.
#define NOSCHEME_AST_KINDS(X)                                                  \
  X(Name)                                                                      \
  X(Number)                                                                    \
  X(String)                                                                    \
  X(Member)                                                                    \
  X(Subscript)                                                                 \
  X(Call)                                                                      \
  X(KeywordArgument)                                                           \
  X(Binary)                                                                    \
  X(Unary)                                                                     \
  X(Group)                                                                     \
  X(ListExpr)                                                                  \
  X(Index)                                                                     \
  X(PinKey)                                                                    \
  X(FieldKey)                                                                  \
  X(Field)                                                                     \
  X(Section)                                                                   \
  X(Net)                                                                       \
  X(Endpoint)                                                                  \
  X(Binding)                                                                   \
  X(Argument)                                                                  \
  X(ElementOverride)                                                           \
  X(Instance)                                                                  \
  X(ImportedName)                                                              \
  X(Import)                                                                    \
  X(Definition)                                                                \
  X(Module)
enum class Kind : std::uint8_t {
#define NOSCHEME_AST_KIND(name) name,
  NOSCHEME_AST_KINDS(NOSCHEME_AST_KIND)
#undef NOSCHEME_AST_KIND
};
std::string_view kind_name(Kind kind);

struct Node {
  Kind kind;
  Span span;
};
using Ptr = std::shared_ptr<const Node>;
template <Kind K> struct Typed : Node {
  static constexpr Kind node_kind = K;
  Typed() : Node{K, {}} {}
};
template <class T> const T *as(const Node *node) {
  return node && node->kind == T::node_kind ? static_cast<const T *>(node)
                                            : nullptr;
}
template <class T> const T *as(const Ptr &node) { return as<T>(node.get()); }
template <class T> const T &as(const Node &node) {
  if (node.kind != T::node_kind)
    throw std::logic_error("unexpected AST node kind");
  return static_cast<const T &>(node);
}
inline bool is(const Node *node, Kind kind) {
  return node && node->kind == kind;
}
inline bool is(const Ptr &node, Kind kind) { return is(node.get(), kind); }

// Expressions.
struct Name : Typed<Kind::Name> {
  std::string text;
};
struct Number : Typed<Kind::Number> {
  std::string spelling;
  bool integer = false;
};
struct String : Typed<Kind::String> {
  std::string value, spelling;
};
struct Index : Typed<Kind::Index> {
  Ptr start, stop;
};
struct Member : Typed<Kind::Member> {
  Ptr base;
  Name name;
};
struct Subscript : Typed<Kind::Subscript> {
  Ptr base;
  Index selection;
};
struct KeywordArgument : Typed<Kind::KeywordArgument> {
  Name name;
  Ptr value;
};
struct Call : Typed<Kind::Call> {
  Ptr callee;
  std::vector<Ptr> arguments;
  std::vector<KeywordArgument> keywords;
};
struct Binary : Typed<Kind::Binary> {
  std::string op;
  Ptr left, right;
};
struct Unary : Typed<Kind::Unary> {
  std::string op;
  Ptr operand;
};
struct Group : Typed<Kind::Group> {
  Ptr expression;
};
struct ListExpr : Typed<Kind::ListExpr> {
  std::vector<Ptr> items;
};

// Declarations.
struct PinKey : Typed<Kind::PinKey> {
  std::string text, spelling;
};
struct FieldKey : Typed<Kind::FieldKey> {
  PinKey first;
  std::optional<PinKey> last;
  std::vector<FieldKey> items;
};
struct Field : Typed<Kind::Field> {
  FieldKey key;
  Ptr value, default_value;
  std::vector<Field> fields;
};
// Entries are Fields in definitions; connections hold Bindings, instance
// params hold Arguments, and elements hold ElementOverrides.
struct Section : Typed<Kind::Section> {
  std::string name;
  std::vector<Ptr> entries;
};
struct Net : Typed<Kind::Net> {
  Name name;
  Ptr type;
  bool global = false;
  std::vector<Field> fields;
};
struct Endpoint : Typed<Kind::Endpoint> {
  std::vector<Index> instance_indices;
  FieldKey head;
  // Names and Indexes.
  std::vector<Ptr> selectors;
};
struct Binding : Typed<Kind::Binding> {
  Endpoint endpoint;
  Ptr target;
};
struct Argument : Typed<Kind::Argument> {
  Name name;
  Ptr value;
};
struct ElementOverride : Typed<Kind::ElementOverride> {
  std::vector<Index> indices;
  std::vector<Section> sections;
};
struct Instance : Typed<Kind::Instance> {
  Name name;
  std::vector<Index> dimensions;
  std::vector<Name> component;
  std::vector<Section> sections;
  std::vector<Ptr> positional;
};
struct ImportedName : Typed<Kind::ImportedName> {
  Name name;
  std::optional<Name> alias;
  const Name &local() const { return alias ? *alias : name; }
};
struct Import : Typed<Kind::Import> {
  std::vector<Name> module;
  std::vector<ImportedName> names;
};
struct Definition : Typed<Kind::Definition> {
  // component, block, board, bundle, or nettype.
  std::string keyword;
  Name name;
  // Empty unless the definition extends another.
  std::vector<Name> base;
  // Sections, Fields, Nets, and Instances.
  std::vector<Ptr> body;
};
struct Module : Typed<Kind::Module> {
  std::shared_ptr<const Source> source;
  // Imports and Definitions.
  std::vector<Ptr> items;
};

std::string dotted(const std::vector<Name> &names);
std::string dotted(const std::vector<const Name *> &names);
// The Names of a Name or a Member chain, such as nettype.power; empty for
// other expressions.
std::vector<const Name *> name_chain(const Node &expression);
// The dotted spelling of name_chain.
std::string expression_path(const Node &expression);
// A connection target such as bus.sd[0]: its first Name, then its member
// Names and Index selections in source order.
struct ReferenceParts {
  const Name *head = nullptr;
  std::vector<const Node *> selectors;
};
ReferenceParts reference_parts(const Node &reference);
// Field entries as a list, skipping other entry kinds.
std::vector<const Field *> field_list(const std::vector<Field> &fields);
std::vector<const Field *> field_list(const std::vector<Ptr> &entries);
// The entries of the first section with this name, or an empty list.
const std::vector<Ptr> &section_entries(const std::vector<Section> &sections,
                                        std::string_view name);
// Children in the order of the comparison form's keys, named by those keys.
using ChildVisitor = std::function<void(std::string_view key, const Node &)>;
void for_each_child(const Node &node, const ChildVisitor &visit);
// The comparison form shared with the Python AST.
Json to_json(const Node &node);

} // namespace ast
} // namespace noscheme
