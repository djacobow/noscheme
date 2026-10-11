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
  bool parameters = false;
  for (std::size_t start = 0; start < line;) {
    auto end = mask.find('\n', start);
    if (end == std::string::npos)
      end = mask.size();
    auto content = trim(mask.substr(start, end - start));
    auto spaces = mask.find_first_not_of(" \t\r", start);
    if (!content.empty() && depth == 0 && !parameters) {
      auto n = spaces - start;
      while (!ancestors.empty() && ancestors.back().indent >= n)
        ancestors.pop_back();
      ancestors.push_back({n, content});
    }
    for (auto i = start; i < end; ++i) {
      if (mask[i] == '(' || mask[i] == '[')
        ++depth;
      else if (mask[i] == ')' || mask[i] == ']')
        --depth;
      else if (!depth && mask[i] == '<')
        parameters = true;
      else if (!depth && mask[i] == '>')
        parameters = false;
    }
    start = end + 1;
  }
  if (depth != 0 || parameters)
    return result;
  while (!ancestors.empty() && ancestors.back().indent >= indent)
    ancestors.pop_back();
  std::vector<std::string> words;
  static const std::regex definition(
      R"(^(component|block|bundle|extend)\s+.+:$)");
  static const std::regex import_head(
      R"(^from\s+[A-Za-z_#][A-Za-z_0-9#]*(\.[A-Za-z_#][A-Za-z_0-9#]*)*$)");
  static const std::regex alias_head(
      R"(^from\s+[A-Za-z_#][A-Za-z_0-9#]*(\.[A-Za-z_#][A-Za-z_0-9#]*)*\s+import\s+[A-Za-z_#][A-Za-z_0-9#]*$)");
  static const std::regex element_head(R"(^(\[[^\[\]]+\])+:$)");
  std::smatch match;
  if (indent == 0 && ancestors.empty()) {
    if (head.empty())
      words = {"from", "import", "component", "block", "bundle", "extend"};
    else if (std::regex_match(head, import_head))
      words = {"import"};
    else if (std::regex_match(head, alias_head))
      words = {"as"};

  } else if (!ancestors.empty()) {
    auto parent = ancestors.back().text;
    if (std::regex_match(parent, match, definition)) {
      auto kind = match[1].str();
      bool inherited = kind == "extend";
      if (head.empty()) {
        if (kind == "component")
          words = {"params", "metadata"};
        if (kind == "component")
          words.push_back("pins");
        if (kind == "block")
          words = {"params", "metadata"};
        if (kind == "block")
          words.insert(words.end(), {"ports", "global"});

        if (inherited)
          words = {"pins", "ports", "metadata"};
        if (kind == "bundle")
          words = {"metadata"};
      }
    } else if (head.empty() && ancestors.size() >= 3 &&
               std::regex_match(parent, element_head) &&
               ancestors[ancestors.size() - 2].text == "elements:" &&
               !completion_scan::declaration_type(
                    ancestors[ancestors.size() - 3].text)
                    .empty()) {
      words = {"params", "metadata"};
    } else if (head.empty() &&
               !completion_scan::declaration_type(parent).empty()) {
      words = {"params", "metadata", "elements"};
    }
  }
  for (const auto &word : words)
    if (word.starts_with(prefix))
      result.entries.push_back({word, "Language keyword", 14});
  return result;
}
} // namespace noscheme
