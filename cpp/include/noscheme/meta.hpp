#pragma once
#include "noscheme/ast.hpp"
#include "noscheme/integer.hpp"
#include <map>
#include <optional>
#include <string>
#include <variant>
#include <vector>

namespace noscheme {
struct Meta;
struct MetaValue;
// Field names to values, in name order like the comparison form. Its special
// members are defined once Meta is complete, so MetaValue can hold it.
struct MetaFields : std::map<std::string, Meta> {
  using map::map;
  MetaFields();
  MetaFields(const MetaFields &);
  MetaFields(MetaFields &&) noexcept;
  MetaFields &operator=(const MetaFields &);
  MetaFields &operator=(MetaFields &&) noexcept;
  ~MetaFields();
};
using MetaList = std::vector<MetaValue>;
// A numeric expression that elaboration evaluates and checks.
struct Constrained {
  const ast::Node *expression = nullptr;
  std::string constraint;
};
// Metadata values. Mappings hold wrapped fields; expressions refer to the
// project's AST.
struct MetaValue {
  std::variant<std::monostate, bool, std::string, Integer, double, MetaList,
               MetaFields, const ast::Node *, Constrained>
      data;
  MetaValue() = default;
  template <class T>
    requires(!std::is_same_v<std::decay_t<T>, MetaValue>)
  MetaValue(T &&value) : data(std::forward<T>(value)) {}
  MetaValue(const char *text) : data(std::string(text)) {}
  template <class T> bool is() const { return std::holds_alternative<T>(data); }
  template <class T> const T *get_if() const { return std::get_if<T>(&data); }
  template <class T> T *get_if() { return std::get_if<T>(&data); }
  bool null() const { return is<std::monostate>(); }
  const std::string &string() const { return std::get<std::string>(data); }
  const MetaList &list() const { return std::get<MetaList>(data); }
  const MetaFields &fields() const { return std::get<MetaFields>(data); }
  MetaFields &fields() { return std::get<MetaFields>(data); }
  const ast::Node *expression() const {
    auto found = get_if<const ast::Node *>();
    return found ? *found : nullptr;
  }
  bool number() const { return is<Integer>() || is<double>(); }
};
// A value with the field that supplied it and that field's module.
struct Meta {
  MetaValue value;
  Span span;
  std::string module;
};

inline MetaFields::MetaFields() = default;
inline MetaFields::MetaFields(const MetaFields &) = default;
inline MetaFields::MetaFields(MetaFields &&) noexcept = default;
inline MetaFields &MetaFields::operator=(const MetaFields &) = default;
inline MetaFields &MetaFields::operator=(MetaFields &&) noexcept = default;
inline MetaFields::~MetaFields() = default;

// The value of a field, or nullptr.
const MetaValue *meta_value(const MetaFields &fields, const std::string &key);
// Strings of a string or a list of strings, such as roles.
std::vector<std::string> meta_strings(const MetaValue &value);
// Children override parents; nested mappings merge.
MetaFields merge_metadata(const MetaFields &parent, const MetaFields &child);
// Equality ignoring spans and modules; booleans and numbers compare by value,
// as in Python.
bool equivalent(const MetaValue &left, const MetaValue &right);
// The comparison form: wrapped fields, tagged numbers, and expression ASTs.
Json to_json(const MetaValue &value);
Json to_json(const MetaFields &fields);
} // namespace noscheme
