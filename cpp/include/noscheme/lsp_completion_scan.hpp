#pragma once
#include <regex>
#include <string>
#include <vector>
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
// A body line `Type<params> name`, from its first character: a net or an
// instance, told apart by the type. Group 1 is the type; group 2 the first
// name.
inline const std::regex &declaration_pattern() {
  static const std::regex pattern(
      R"(^(?:global[ \t]+)?([A-Za-z_#][A-Za-z0-9_#]*(?:\.[A-Za-z_#][A-Za-z0-9_#]*)*)(?:[ \t]*<[^\n]*?>)?[ \t]+([A-Za-z_#][A-Za-z0-9_#]*))");
  return pattern;
}
// The type a declaration line declares, or empty for any other line. The text
// starts at the line's first character, past its indentation.
inline std::string declaration_type(const std::string &content) {
  std::smatch match;
  return std::regex_search(content, match, declaration_pattern())
             ? match[1].str()
             : std::string();
}
struct ParameterList {
  std::size_t opening, end;
  bool closed;
  std::string type;
};
inline std::vector<ParameterList> parameter_lists(const std::string &mask) {
  std::vector<ParameterList> result;
  static const std::regex header(
      R"(^([ \t]*)(extend[ \t]+|global[ \t]+)?([A-Za-z_#][A-Za-z0-9_#]*(?:\.[A-Za-z_#][A-Za-z0-9_#]*)*)[ \t]*$)");
  for (auto opening = mask.find('<'); opening != std::string::npos;
       opening = mask.find('<', opening + 1)) {
    auto line = opening ? mask.rfind('\n', opening - 1) : std::string::npos;
    line = line == std::string::npos ? 0 : line + 1;
    auto before = mask.substr(line, opening - line);
    std::smatch match;
    if (!std::regex_match(before, match, header) ||
        (match[2].str().starts_with("extend") == !match[1].str().empty()))
      continue;
    int depth = 0;
    auto end = opening + 1;
    for (; end < mask.size(); ++end) {
      if (mask[end] == '(' || mask[end] == '[')
        ++depth;
      else if (mask[end] == ')' || mask[end] == ']')
        --depth;
      else if (mask[end] == '>' && depth == 0)
        break;
      else if (mask[end] == '\n' && depth == 0) {
        auto next = mask.find_first_not_of(" \t\r\n", end);
        if (next == std::string::npos)
          break;
        auto start = mask.rfind('\n', next);
        if (next - start - 1 <= static_cast<std::size_t>(match[1].length()) &&
            mask[next] != '>')
          break;
      }
    }
    result.push_back(
        {opening, end, end < mask.size() && mask[end] == '>', match[3]});
    opening = end == mask.size() ? end - 1 : end;
  }
  return result;
}
// The offset of the line break that ends a declaration header at first, past
// its bracketed lists: `(...)`, `[...]`, and `<...>`. Text is masked.
inline std::size_t header_end(const std::string &mask, std::size_t first) {
  int depth = 0;
  bool parameters = false;
  for (auto at = first; at < mask.size(); ++at) {
    auto c = mask[at];
    if (c == '(' || c == '[')
      ++depth;
    else if (c == ')' || c == ']')
      --depth;
    else if (!depth && c == '<')
      parameters = true;
    else if (!depth && c == '>')
      parameters = false;
    else if (c == '\n' && depth == 0 && !parameters)
      return at;
  }
  return mask.size();
}
} // namespace noscheme::completion_scan
