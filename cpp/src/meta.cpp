#include "noscheme/evaluate.hpp"
#include <algorithm>
#include <cmath>

namespace noscheme {
const MetaValue *meta_value(const MetaFields &fields, const std::string &key) {
  auto found = fields.find(key);
  return found == fields.end() ? nullptr : &found->second.value;
}
std::vector<std::string> meta_strings(const MetaValue &value) {
  if (auto text = value.get_if<std::string>())
    return {*text};
  std::vector<std::string> result;
  if (auto list = value.get_if<MetaList>())
    for (const auto &item : *list)
      if (auto text = item.get_if<std::string>())
        result.push_back(*text);
  return result;
}
namespace {
MetaFields merge(const MetaFields &parent, const MetaFields &child,
                 std::vector<std::string> path) {
  auto result = parent;
  for (const auto &[key, value] : child) {
    auto found = result.find(key);
    if (found != result.end()) {
      const auto &previous = found->second.value;
      const auto &next = value.value;
      if (key == "checker" && path.size() >= 2 &&
          path[path.size() - 2] == "checks" &&
          std::ranges::find(path, "custom") == path.end() &&
          !equivalent(previous, next)) {
        const Symbols symbols;
        Evaluator(symbols).error(
            "erc.application_checker",
            "an inherited application cannot change checker", value.span);
      }
      auto *old_fields = previous.get_if<MetaFields>();
      auto *new_fields = next.get_if<MetaFields>();
      if (old_fields && new_fields) {
        auto nested = path;
        nested.push_back(key);
        auto combined = merge(*old_fields, *new_fields, std::move(nested));
        found->second = value;
        found->second.value = std::move(combined);
        continue;
      }
    }
    result.insert_or_assign(key, value);
  }
  return result;
}
// Python compares booleans as integers.
std::optional<Value> numeric(const MetaValue &value) {
  if (auto flag = value.get_if<bool>())
    return Integer(*flag ? 1 : 0);
  if (value.number())
    return to_value(value);
  return {};
}
Json without_spans(Json value) {
  if (value.is_object()) {
    value.erase("span");
    for (auto &[key, item] : value.items())
      item = without_spans(std::move(item));
  } else if (value.is_array())
    for (auto &item : value)
      item = without_spans(std::move(item));
  return value;
}
bool same_expression(const ast::Node *left, const ast::Node *right) {
  return left == right || (left && right &&
                           without_spans(ast::to_json(*left)) ==
                               without_spans(ast::to_json(*right)));
}
} // namespace
MetaFields merge_metadata(const MetaFields &parent, const MetaFields &child) {
  return merge(parent, child, {});
}
bool equivalent(const MetaValue &left, const MetaValue &right) {
  auto a = numeric(left), b = numeric(right);
  if (a && b) {
    if (a->index() == b->index())
      return *a == *b;
    const auto &integer = std::holds_alternative<Integer>(*a)
                              ? std::get<Integer>(*a)
                              : std::get<Integer>(*b);
    double floating = std::holds_alternative<double>(*a) ? std::get<double>(*a)
                                                         : std::get<double>(*b);
    return floating >= -0x1p63 && floating < 0x1p63 &&
           floating == std::trunc(floating) &&
           integer == static_cast<Integer>(floating);
  }
  if (left.data.index() != right.data.index())
    return false;
  if (auto list = left.get_if<MetaList>()) {
    const auto &other = right.list();
    return list->size() == other.size() &&
           std::ranges::equal(*list, other, equivalent);
  }
  if (auto fields = left.get_if<MetaFields>()) {
    const auto &other = right.fields();
    return fields->size() == other.size() &&
           std::ranges::all_of(*fields, [&](const auto &entry) {
             auto found = other.find(entry.first);
             return found != other.end() &&
                    equivalent(entry.second.value, found->second.value);
           });
  }
  if (auto expression = left.get_if<const ast::Node *>())
    return same_expression(*expression, right.expression());
  if (auto constrained = left.get_if<Constrained>()) {
    const auto &other = std::get<Constrained>(right.data);
    return constrained->constraint == other.constraint &&
           same_expression(constrained->expression, other.expression);
  }
  if (auto text = left.get_if<std::string>())
    return *text == right.string();
  return true;
}
Json to_json(const MetaValue &value) {
  return std::visit(
      [](const auto &v) -> Json {
        using T = std::decay_t<decltype(v)>;
        if constexpr (std::is_same_v<T, std::monostate>)
          return nullptr;
        else if constexpr (std::is_same_v<T, bool> ||
                           std::is_same_v<T, std::string>)
          return v;
        else if constexpr (std::is_same_v<T, Integer> ||
                           std::is_same_v<T, double>)
          return encoded(v);
        else if constexpr (std::is_same_v<T, MetaList>) {
          auto result = Json::array();
          for (const auto &item : v)
            result.push_back(to_json(item));
          return result;
        } else if constexpr (std::is_same_v<T, MetaFields>)
          return to_json(v);
        else if constexpr (std::is_same_v<T, const ast::Node *>)
          return ast::to_json(*v);
        else
          return object("expression", ast::to_json(*v.expression), "constraint",
                        v.constraint);
      },
      value.data);
}
Json to_json(const MetaFields &fields) {
  auto result = Json::object();
  for (const auto &[key, field] : fields)
    result[key] = object("value", to_json(field.value), "span",
                         field.span.json(), "module", field.module);
  return result;
}
} // namespace noscheme
