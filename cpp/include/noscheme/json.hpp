#pragma once
#include <nlohmann/json.hpp>
#include <utility>

namespace noscheme {
using Json = nlohmann::json;

namespace detail {
inline void members(Json &) {}
template <class Key, class Value, class... Rest>
void members(Json &result, Key &&key, Value &&value, Rest &&...rest) {
  result[std::forward<Key>(key)] = std::forward<Value>(value);
  members(result, std::forward<Rest>(rest)...);
}
} // namespace detail

// Unlike JSON initializer lists, this preserves moves of temporary subtrees.
template <class... Members> Json object(Members &&...members) {
  static_assert(sizeof...(Members) % 2 == 0);
  Json result = Json::object();
  detail::members(result, std::forward<Members>(members)...);
  return result;
}
} // namespace noscheme
