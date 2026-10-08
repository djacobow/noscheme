#pragma once
#include <map>
#include <string>
#include <string_view>

namespace noscheme {
inline const std::map<std::string, std::string> quantity_units{
    {"voltage", "V"},     {"current", "A"},    {"resistance", "Ohm"},
    {"capacitance", "F"}, {"inductance", "H"}, {"frequency", "Hz"},
    {"time", "s"},        {"power", "W"}};
// R is the unity decimal mark in literal spellings; displays omit it.
inline constexpr std::string_view numeric_prefixes = "afpnumRkMGTP";
inline int prefix_exponent(char prefix) {
  return (static_cast<int>(numeric_prefixes.find(prefix)) - 6) * 3;
}
} // namespace noscheme
