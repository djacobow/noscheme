#pragma once
#include <algorithm>
#include <charconv>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <string>
#include <string_view>

namespace noscheme {
using Integer = std::int64_t;
struct IntegerOverflow : std::runtime_error {
  IntegerOverflow()
      : std::runtime_error("integer exceeds configured bit limit") {}
};
inline Integer parse_integer(std::string_view text) {
  std::string digits(text);
  std::erase(digits, '_');
  text = digits;
  bool negative = text.starts_with('-');
  if (negative || text.starts_with('+'))
    text.remove_prefix(1);
  int base = 10;
  if (text.starts_with("0x") || text.starts_with("0X"))
    base = 16;
  else if (text.starts_with("0b") || text.starts_with("0B"))
    base = 2;
  if (base != 10)
    text.remove_prefix(2);
  std::uint64_t result;
  auto [end, error] =
      std::from_chars(text.data(), text.data() + text.size(), result, base);
  if (error == std::errc::result_out_of_range)
    throw IntegerOverflow();
  if (error != std::errc{} || end != text.data() + text.size())
    throw std::invalid_argument("invalid integer");
  auto limit = std::uint64_t(std::numeric_limits<Integer>::max());
  if (result > limit + negative)
    throw IntegerOverflow();
  if (negative && result == limit + 1)
    return std::numeric_limits<Integer>::min();
  return negative ? -static_cast<Integer>(result)
                  : static_cast<Integer>(result);
}
inline Integer integer_from_double(double value) {
  // INT64_MAX rounds up to 2^63 as a double; use an exclusive upper bound.
  if (!std::isfinite(value) || value < -0x1p63 || value >= 0x1p63)
    throw IntegerOverflow();
  return static_cast<Integer>(value);
}
inline Integer integer_add(Integer a, Integer b) {
  Integer result;
  if (__builtin_add_overflow(a, b, &result))
    throw IntegerOverflow();
  return result;
}
inline Integer integer_subtract(Integer a, Integer b) {
  Integer result;
  if (__builtin_sub_overflow(a, b, &result))
    throw IntegerOverflow();
  return result;
}
inline Integer integer_multiply(Integer a, Integer b) {
  Integer result;
  if (__builtin_mul_overflow(a, b, &result))
    throw IntegerOverflow();
  return result;
}
inline bool integer_fits(Integer value, std::size_t bits) {
  if (bits >= 64)
    return true;
  if (bits == 0)
    return value == 0;
  auto bound = Integer(1) << (bits - 1);
  return value >= -bound && value < bound;
}
} // namespace noscheme
