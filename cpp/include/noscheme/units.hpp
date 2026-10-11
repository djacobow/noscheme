#pragma once
#include <algorithm>
#include <map>
#include <string>
#include <string_view>

namespace noscheme {
inline const std::map<std::string, std::string> quantity_units{
    {"voltage", "V"},     {"current", "A"},    {"resistance", "Ohm"},
    {"capacitance", "F"}, {"inductance", "H"}, {"frequency", "Hz"},
    {"time", "s"},        {"power", "W"}};
inline std::string normalized_number(std::string text) {
  std::erase(text, '_');
  if (auto micro = text.find("µ"); micro != std::string::npos)
    text.replace(micro, std::string("µ").size(), "u");
  return text;
}
// R is the unity decimal mark in literal spellings; displays omit it.
inline constexpr std::string_view numeric_prefixes = "afpnumRkMGTP";
inline int prefix_exponent(char prefix) {
  return (static_cast<int>(numeric_prefixes.find(prefix)) - 6) * 3;
}
} // namespace noscheme
