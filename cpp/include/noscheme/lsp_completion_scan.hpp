#pragma once
#include <string>
namespace noscheme::completion_scan {
inline bool name_char(char c) {
  return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
         (c >= '0' && c <= '9') || c == '_' || c == '#';
}
inline bool space(char c) {
  return c == ' ' || c == '\t' || c == '\r' || c == '\n';
}
inline std::string trim(std::string text) {
  auto first = text.find_first_not_of(" \t\r\n");
  return first == std::string::npos
             ? ""
             : text.substr(first, text.find_last_not_of(" \t\r\n") - first + 1);
}
} // namespace noscheme::completion_scan
