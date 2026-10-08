#include "noscheme/lsp_completion.hpp"
#include "noscheme/lsp_completion_scan.hpp"
#include <regex>
namespace noscheme {
// Lexical completion deliberately does not require a valid AST. Indentation
// ancestors constrain suggestions; masked literals and open groups are opaque.
CompletionResult keyword_completion(const std::string &text,
                                    std::size_t offset) {
  using completion_scan::name_char;
  using completion_scan::trim;
  CompletionResult result{offset, offset, {}};
  if (offset > text.size() ||
      code_mask(text.substr(0, offset) + "x").back() != 'x')
    return result;
  auto mask = code_mask(text);
  auto line = offset ? text.rfind('\n', offset - 1) : std::string::npos;
  line = line == std::string::npos ? 0 : line + 1;
  auto first = line;
  while (first < offset && (text[first] == ' ' || text[first] == '\t'))
    ++first;
  auto indent = first - line;
  result.start = offset;
  while (result.start > first && name_char(text[result.start - 1]))
    --result.start;
  while (result.end < text.size() && name_char(text[result.end]))
    ++result.end;
  auto prefix = text.substr(result.start, offset - result.start);
  auto head = trim(mask.substr(first, result.start - first));
  struct Line {
    std::size_t indent;
    std::string text;
  };
  std::vector<Line> ancestors;
  int depth = 0;
  for (std::size_t start = 0; start < line;) {
    auto end = mask.find('\n', start);
    if (end == std::string::npos)
      end = mask.size();
    auto content = trim(mask.substr(start, end - start));
    auto spaces = mask.find_first_not_of(" \t\r", start);
    if (!content.empty() && depth == 0) {
      auto n = spaces - start;
      while (!ancestors.empty() && ancestors.back().indent >= n)
        ancestors.pop_back();
      ancestors.push_back({n, content});
    }
    for (auto i = start; i < end; ++i) {
      if (mask[i] == '(' || mask[i] == '[')
        ++depth;
      if (mask[i] == ')' || mask[i] == ']')
        --depth;
    }
    start = end + 1;
  }
  if (depth != 0)
    return result;
  while (!ancestors.empty() && ancestors.back().indent >= indent)
    ancestors.pop_back();
  std::vector<std::string> words;
  static const std::regex definition(
      R"(^(component|block|board|bundle|nettype)\s+[A-Za-z_#][A-Za-z_0-9#]*(\s+extends\s+[^:]+)?:$)");
  static const std::regex extends_head(
      R"(^(component|block|nettype)\s+[A-Za-z_#][A-Za-z_0-9#]*$)");
  static const std::regex import_head(
      R"(^from\s+[A-Za-z_#][A-Za-z_0-9#]*(\.[A-Za-z_#][A-Za-z_0-9#]*)*$)");
  static const std::regex alias_head(
      R"(^from\s+[A-Za-z_#][A-Za-z_0-9#]*(\.[A-Za-z_#][A-Za-z_0-9#]*)*\s+import\s+[A-Za-z_#][A-Za-z_0-9#]*$)");
  static const std::regex element_head(R"(^(\[[^\[\]]+\])+:$)");
  std::smatch match;
  if (indent == 0 && ancestors.empty()) {
    if (head.empty())
      words = {"from",  "import", "component", "block",
               "board", "bundle", "nettype"};
    else if (std::regex_match(head, import_head))
      words = {"import"};
    else if (std::regex_match(head, alias_head))
      words = {"as"};
    else if (std::regex_match(head, extends_head))
      words = {"extends"};
  } else if (!ancestors.empty()) {
    auto parent = ancestors.back().text;
    if (std::regex_match(parent, match, definition)) {
      auto kind = match[1].str();
      bool inherited = match[2].matched;
      if (head.empty()) {
        if (kind == "component")
          words = {inherited ? "defaults" : "params", "metadata"};
        if (kind == "component")
          words.push_back("pins");
        if (kind == "block")
          words = {inherited ? "defaults" : "params", "metadata"};
        if (kind == "block")
          words.push_back("ports");
        if (kind == "block" && !inherited)
          words.insert(words.end(), {"net", "inst"});
        if (kind == "board")
          words = {"metadata", "net", "global", "inst"};
        if (kind == "nettype" && inherited)
          words = {"defaults"};
        if (kind == "bundle")
          words = {"metadata"};
      } else if (kind == "board" && head == "global")
        words = {"net"};
    } else if (head.empty() && ancestors.size() >= 3 &&
               std::regex_match(parent, element_head) &&
               ancestors[ancestors.size() - 2].text == "elements:" &&
               ancestors[ancestors.size() - 3].text.starts_with("inst ")) {
      words = {"params", "metadata"};
    } else if (head.empty() && parent.starts_with("inst ")) {
      words = {"connections", "params", "metadata", "elements"};
    }
  }
  for (const auto &word : words)
    if (word.starts_with(prefix))
      result.entries.push_back({word, "Language keyword", 14});
  return result;
}
} // namespace noscheme
