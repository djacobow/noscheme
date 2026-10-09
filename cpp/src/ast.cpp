#include "noscheme/ast.hpp"
#include "noscheme/source.hpp"
#include <algorithm>

namespace noscheme {
const std::string &Span::filename() const {
  static const std::string none;
  return source ? source->filename : none;
}
Json Span::json() const {
  return source ? source->span(start, end) : Json(nullptr);
}
bool Span::operator==(const Span &other) const {
  return start == other.start && end == other.end &&
         (source == other.source ||
          (source && other.source && filename() == other.filename()));
}
Json span_json(const Span &span) { return span.json(); }

namespace ast {
std::string_view kind_name(Kind kind) {
  static constexpr std::string_view names[] = {
#define NOSCHEME_AST_KIND(name) #name,
      NOSCHEME_AST_KINDS(NOSCHEME_AST_KIND)
#undef NOSCHEME_AST_KIND
  };
  return names[static_cast<std::size_t>(kind)];
}
std::string dotted(const std::vector<Name> &names) {
  std::string result;
  for (const auto &name : names) {
    if (!result.empty())
      result += '.';
    result += name.text;
  }
  return result;
}
std::string dotted(const std::vector<const Name *> &names) {
  std::string result;
  for (const auto *name : names) {
    if (!result.empty())
      result += '.';
    result += name->text;
  }
  return result;
}
std::vector<const Name *> name_chain(const Node &expression) {
  std::vector<const Name *> result;
  const Node *current = &expression;
  while (auto member = as<Member>(current)) {
    result.push_back(&member->name);
    current = member->base.get();
  }
  auto name = as<Name>(current);
  if (!name)
    return {};
  result.push_back(name);
  std::reverse(result.begin(), result.end());
  return result;
}
std::string expression_path(const Node &expression) {
  return dotted(name_chain(expression));
}
ReferenceParts reference_parts(const Node &reference) {
  ReferenceParts result;
  const Node *current = &reference;
  if (auto *call = as<Call>(current))
    current = call->arguments.front().get();
  while (true) {
    if (auto *member = as<Member>(current)) {
      result.selectors.push_back(&member->name);
      current = member->base.get();
    } else if (auto *subscript = as<Subscript>(current)) {
      result.selectors.push_back(&subscript->selection);
      current = subscript->base.get();
    } else
      break;
  }
  std::reverse(result.selectors.begin(), result.selectors.end());
  result.head = &as<Name>(*current);
  return result;
}
std::vector<const Field *> field_list(const std::vector<Field> &fields) {
  std::vector<const Field *> result;
  for (const auto &field : fields)
    result.push_back(&field);
  return result;
}
std::vector<const Field *> field_list(const std::vector<Ptr> &entries) {
  std::vector<const Field *> result;
  for (const auto &entry : entries)
    if (auto *field = as<Field>(entry))
      result.push_back(field);
  return result;
}
const std::vector<Ptr> &section_entries(const std::vector<Section> &sections,
                                        std::string_view name) {
  static const std::vector<Ptr> empty;
  for (const auto &section : sections)
    if (section.name == name)
      return section.entries;
  return empty;
}

namespace {
template <class T>
void each(std::string_view key, const std::vector<T> &items,
          const ChildVisitor &visit) {
  for (const auto &item : items) {
    if constexpr (std::is_same_v<T, Ptr>)
      visit(key, *item);
    else
      visit(key, item);
  }
}
void optional(std::string_view key, const Ptr &node,
              const ChildVisitor &visit) {
  if (node)
    visit(key, *node);
}
} // namespace
void for_each_child(const Node &node, const ChildVisitor &visit) {
  switch (node.kind) {
  case Kind::Name:
  case Kind::Number:
  case Kind::String:
  case Kind::PinKey:
    return;
  case Kind::Member: {
    const auto &n = as<Member>(node);
    visit("base", *n.base);
    visit("name", n.name);
    return;
  }
  case Kind::Subscript: {
    const auto &n = as<Subscript>(node);
    visit("base", *n.base);
    visit("selection", n.selection);
    return;
  }
  case Kind::Call: {
    const auto &n = as<Call>(node);
    each("arguments", n.arguments, visit);
    visit("callee", *n.callee);
    each("keywords", n.keywords, visit);
    return;
  }
  case Kind::KeywordArgument: {
    const auto &n = as<KeywordArgument>(node);
    visit("name", n.name);
    visit("value", *n.value);
    return;
  }
  case Kind::Binary: {
    const auto &n = as<Binary>(node);
    visit("left", *n.left);
    visit("right", *n.right);
    return;
  }
  case Kind::Unary:
    visit("operand", *as<Unary>(node).operand);
    return;
  case Kind::Group:
    visit("expression", *as<Group>(node).expression);
    return;
  case Kind::ListExpr:
    each("items", as<ListExpr>(node).items, visit);
    return;
  case Kind::Index: {
    const auto &n = as<Index>(node);
    visit("start", *n.start);
    optional("stop", n.stop, visit);
    return;
  }
  case Kind::Range: {
    const auto &n = as<Range>(node);
    visit("high", *n.high);
    visit("low", *n.low);
    return;
  }
  case Kind::Tolerance: {
    const auto &n = as<Tolerance>(node);
    visit("center", *n.center);
    visit("deviation", *n.deviation);
    return;
  }
  case Kind::FieldKey: {
    const auto &n = as<FieldKey>(node);
    each("aliases", n.aliases, visit);
    visit("first", n.first);
    each("items", n.items, visit);
    if (n.last)
      visit("last", *n.last);
    return;
  }
  case Kind::Field: {
    const auto &n = as<Field>(node);
    optional("default", n.default_value, visit);
    each("fields", n.fields, visit);
    visit("key", n.key);
    optional("value", n.value, visit);
    return;
  }
  case Kind::Section:
    each("entries", as<Section>(node).entries, visit);
    return;
  case Kind::Net: {
    const auto &n = as<Net>(node);
    each("fields", n.fields, visit);
    visit("name", n.name);
    visit("type", *n.type);
    return;
  }
  case Kind::Endpoint: {
    const auto &n = as<Endpoint>(node);
    visit("head", n.head);
    each("instance_indices", n.instance_indices, visit);
    each("selectors", n.selectors, visit);
    return;
  }
  case Kind::Binding: {
    const auto &n = as<Binding>(node);
    visit("endpoint", n.endpoint);
    visit("target", *n.target);
    return;
  }
  case Kind::Argument: {
    const auto &n = as<Argument>(node);
    visit("name", n.name);
    visit("value", *n.value);
    return;
  }
  case Kind::ElementOverride: {
    const auto &n = as<ElementOverride>(node);
    each("indices", n.indices, visit);
    each("sections", n.sections, visit);
    return;
  }
  case Kind::Instance: {
    const auto &n = as<Instance>(node);
    each("component", n.component, visit);
    each("dimensions", n.dimensions, visit);
    visit("name", n.name);
    each("positional", n.positional, visit);
    each("sections", n.sections, visit);
    return;
  }
  case Kind::ImportedName: {
    const auto &n = as<ImportedName>(node);
    if (n.alias)
      visit("alias", *n.alias);
    visit("name", n.name);
    return;
  }
  case Kind::Import: {
    const auto &n = as<Import>(node);
    each("module", n.module, visit);
    each("names", n.names, visit);
    return;
  }
  case Kind::Definition: {
    const auto &n = as<Definition>(node);
    each("base", n.base, visit);
    each("body", n.body, visit);
    visit("name", n.name);
    return;
  }
  case Kind::Module:
    each("items", as<Module>(node).items, visit);
    return;
  }
}

namespace {
Json optional_json(const Ptr &node) {
  return node ? to_json(*node) : Json(nullptr);
}
template <class T> Json list_json(const std::vector<T> &items) {
  auto result = Json::array();
  for (const auto &item : items) {
    if constexpr (std::is_same_v<T, Ptr>)
      result.push_back(to_json(*item));
    else
      result.push_back(to_json(item));
  }
  return result;
}
Json fields_json(const Node &node) {
  switch (node.kind) {
  case Kind::Name:
    return object("text", as<Name>(node).text);
  case Kind::Number: {
    const auto &n = as<Number>(node);
    return object("spelling", n.spelling, "tag",
                  n.integer ? "integer" : "double");
  }
  case Kind::String: {
    const auto &n = as<String>(node);
    return object("value", n.value, "spelling", n.spelling);
  }
  case Kind::Member: {
    const auto &n = as<Member>(node);
    return object("base", to_json(*n.base), "name", to_json(n.name));
  }
  case Kind::Subscript: {
    const auto &n = as<Subscript>(node);
    return object("base", to_json(*n.base), "selection", to_json(n.selection));
  }
  case Kind::Call: {
    const auto &n = as<Call>(node);
    return object("callee", to_json(*n.callee), "arguments",
                  list_json(n.arguments), "keywords", list_json(n.keywords));
  }
  case Kind::KeywordArgument: {
    const auto &n = as<KeywordArgument>(node);
    return object("name", to_json(n.name), "value", to_json(*n.value));
  }
  case Kind::Binary: {
    const auto &n = as<Binary>(node);
    return object("operator", n.op, "left", to_json(*n.left), "right",
                  to_json(*n.right));
  }
  case Kind::Unary: {
    const auto &n = as<Unary>(node);
    return object("operator", n.op, "operand", to_json(*n.operand));
  }
  case Kind::Group:
    return object("expression", to_json(*as<Group>(node).expression));
  case Kind::ListExpr:
    return object("items", list_json(as<ListExpr>(node).items));
  case Kind::Index: {
    const auto &n = as<Index>(node);
    return object("start", to_json(*n.start), "stop", optional_json(n.stop));
  }
  case Kind::Range: {
    const auto &n = as<Range>(node);
    return object("low", to_json(*n.low), "high", to_json(*n.high));
  }
  case Kind::Tolerance: {
    const auto &n = as<Tolerance>(node);
    return object("center", to_json(*n.center), "deviation",
                  to_json(*n.deviation), "percent", n.percent);
  }
  case Kind::PinKey: {
    const auto &n = as<PinKey>(node);
    return object("text", n.text, "spelling", n.spelling);
  }
  case Kind::FieldKey: {
    const auto &n = as<FieldKey>(node);
    return object("first", to_json(n.first), "last",
                  n.last ? to_json(*n.last) : Json(nullptr), "items",
                  list_json(n.items), "aliases", list_json(n.aliases));
  }
  case Kind::Field: {
    const auto &n = as<Field>(node);
    return object("key", to_json(n.key), "value", optional_json(n.value),
                  "default", optional_json(n.default_value), "fields",
                  list_json(n.fields));
  }
  case Kind::Section: {
    const auto &n = as<Section>(node);
    return object("name", n.name, "entries", list_json(n.entries));
  }
  case Kind::Net: {
    const auto &n = as<Net>(node);
    return object("name", to_json(n.name), "type", to_json(*n.type), "global_",
                  n.global, "fields", list_json(n.fields));
  }
  case Kind::Endpoint: {
    const auto &n = as<Endpoint>(node);
    return object("instance_indices", list_json(n.instance_indices), "head",
                  to_json(n.head), "selectors", list_json(n.selectors));
  }
  case Kind::Binding: {
    const auto &n = as<Binding>(node);
    return object("endpoint", to_json(n.endpoint), "target",
                  to_json(*n.target));
  }
  case Kind::Argument: {
    const auto &n = as<Argument>(node);
    return object("name", to_json(n.name), "value", to_json(*n.value));
  }
  case Kind::ElementOverride: {
    const auto &n = as<ElementOverride>(node);
    return object("indices", list_json(n.indices), "sections",
                  list_json(n.sections));
  }
  case Kind::Instance: {
    const auto &n = as<Instance>(node);
    return object("name", to_json(n.name), "dimensions",
                  list_json(n.dimensions), "component", list_json(n.component),
                  "sections", list_json(n.sections), "positional",
                  list_json(n.positional), "wildcard",
                  n.wildcard ? n.wildcard->json() : Json(nullptr));
  }
  case Kind::ImportedName: {
    const auto &n = as<ImportedName>(node);
    return object("name", to_json(n.name), "alias",
                  n.alias ? to_json(*n.alias) : Json(nullptr));
  }
  case Kind::Import: {
    const auto &n = as<Import>(node);
    return object("module", list_json(n.module), "names", list_json(n.names));
  }
  case Kind::Definition: {
    const auto &n = as<Definition>(node);
    return object("kind", n.keyword, "name", to_json(n.name), "base",
                  n.base.empty() ? Json(nullptr) : list_json(n.base), "body",
                  list_json(n.body));
  }
  case Kind::Module:
    return object("items", list_json(as<Module>(node).items));
  }
  throw std::logic_error("unhandled AST node kind");
}
} // namespace
Json to_json(const Node &node) {
  auto result = fields_json(node);
  result["node"] = kind_name(node.kind);
  result["span"] = node.span.json();
  return result;
}
} // namespace ast
} // namespace noscheme
