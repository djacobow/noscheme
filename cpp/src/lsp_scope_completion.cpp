#include "noscheme/lsp_completion.hpp"
#include "noscheme/lsp_completion_scan.hpp"
#include <regex>
#include <set>
namespace noscheme {
namespace {
using completion_scan::trim;
struct Row {
  std::size_t indent;
  std::string text;
};
std::vector<Row> rows(const std::string &text) {
  std::vector<Row> result;
  for (std::size_t pos = 0; pos < text.size();) {
    auto end = text.find('\n', pos);
    if (end == std::string::npos)
      end = text.size();
    auto raw = text.substr(pos, end - pos);
    auto first = raw.find_first_not_of(" \t\r");
    if (first != std::string::npos)
      result.push_back({first, trim(raw)});
    pos = end + 1;
  }
  return result;
}
} // namespace
CompletionResult scope_completion(const std::string &text, std::size_t offset,
                                  const std::vector<CompletionEntry> &catalog,
                                  bool expression) {
  CompletionResult result{offset, offset, {}};
  if (offset > text.size() ||
      code_mask(text.substr(0, offset) + "x").back() != 'x')
    return result;
  auto mask = code_mask(text);
  auto line_start = offset ? mask.rfind('\n', offset - 1) : std::string::npos;
  line_start = line_start == std::string::npos ? 0 : line_start + 1;
  auto raw = mask.substr(line_start, offset - line_start);
  auto line = trim(raw);
  auto indent = raw.find_first_not_of(" \t");
  if (indent == std::string::npos)
    indent = raw.size();
  while (result.start && (completion_scan::name_char(mask[result.start - 1]) ||
                          mask[result.start - 1] == '.'))
    --result.start;
  while (
      result.end < text.size() &&
      (completion_scan::name_char(mask[result.end]) || mask[result.end] == '.'))
    ++result.end;
  auto word = mask.substr(result.start, offset - result.start);
  std::vector<Row> parents;
  for (auto row : rows(mask.substr(0, line_start))) {
    while (!parents.empty() && parents.back().indent >= row.indent)
      parents.pop_back();
    parents.push_back(std::move(row));
  }
  while (!parents.empty() && parents.back().indent >= indent)
    parents.pop_back();
  auto has = [&](const std::string &name) {
    return std::ranges::any_of(
        parents, [&](const Row &row) { return row.text == name; });
  };
  static const std::regex definition(
      R"(^(component|block|board|bundle|nettype)\s+([A-Za-z_#][A-Za-z0-9_#]*)[^\n]*:)",
      std::regex::multiline);
  std::smatch owner;
  std::size_t owner_end = 0;
  for (std::sregex_iterator it(mask.begin(), mask.end(), definition), end;
       it != end; ++it) {
    if (static_cast<std::size_t>(it->position()) >= offset)
      break;
    owner = *it;
    owner_end = it->position() + it->length();
  }
  if (owner.empty())
    return result;
  auto following = mask.find('\n', owner_end);
  std::size_t stop = mask.size();
  if (following != std::string::npos) {
    auto rest = mask.substr(following);
    std::smatch next;
    if (std::regex_search(rest, next, definition))
      stop = following + next.position();
  }
  std::string mode = expression ? "expression" : "", scope;
  bool in_instance = std::ranges::any_of(
      parents, [](const Row &r) { return r.text.starts_with("inst "); });
  static const std::regex instance(
      R"(\binst\s+[A-Za-z_#][A-Za-z0-9_#]*(?:\[[^\n]*?\])*\s*:\s*[A-Za-z0-9_#.]+\s*\()");
  auto body_before = mask.substr(owner_end, offset - owner_end);
  std::size_t args_start = std::string::npos;
  for (std::sregex_iterator
           it(body_before.begin(), body_before.end(), instance),
       end;
       it != end; ++it)
    args_start = it->position() + it->length();
  if (args_start != std::string::npos) {
    auto args = body_before.substr(args_start);
    int depth = 0;
    bool closed = false;
    std::size_t current = 0;
    for (std::size_t i = 0; i < args.size(); ++i) {
      if (args[i] == '(' || args[i] == '[')
        ++depth;
      else if (args[i] == ')' || args[i] == ']')
        --depth;
      if (depth < 0) {
        closed = true;
        break;
      }
      if (depth == 0 && args[i] == ',')
        current = i + 1;
    }
    if (!closed) {
      auto argument = args.substr(current);
      if (argument.find('=') != std::string::npos ||
          argument.find('[') != std::string::npos)
        mode = "expression";
      // Connection targets already use the native instance/section catalogs.
    }
  }
  if (mode.empty() &&
      std::ranges::count(line, '[') > std::ranges::count(line, ']') &&
      (line.starts_with("inst ") || line.find(':') != std::string::npos))
    mode = "expression";
  if (mode.empty() && !parents.empty() &&
      (parents.back().text == "params:" ||
       parents.back().text == "defaults:")) {
    if (line.find('=') != std::string::npos ||
        ((in_instance || parents.back().text == "defaults:") &&
         line.find(':') != std::string::npos))
      mode = "expression";
  }
  if (!has("custom:")) {
    if (!parents.empty() && parents.back().text == "metadata:")
      scope = in_instance ? "instance" : owner[1].str();
    else if (!parents.empty() &&
             (parents.back().text.starts_with("net ") ||
              parents.back().text.starts_with("global net ")))
      scope = "net";
    else if (parents.size() >= 2) {
      auto section = parents[parents.size() - 2].text;
      if (section == "pins:" || section == "ports:")
        scope = in_instance          ? "instance_pin"
                : section == "pins:" ? "pin"
                                     : "port";
    }
  }
  std::vector<CompletionEntry> choices;
  const Json *schemas = nullptr;
  for (const auto &entry : catalog)
    if (entry.schemas)
      schemas = entry.schemas.get();
  if (!scope.empty() && schemas && schemas->contains(scope)) {
    const auto &schema = schemas->at(scope);
    auto colon = line.find(':');
    if (colon == std::string::npos) {
      auto suffix = trim(mask.substr(result.end)).starts_with(':') ? "" : ": ";
      for (const auto &field : schema) {
        auto name = field.at("name").get<std::string>();
        CompletionEntry entry{name, field.at("schema").at("kind").dump(), 10};
        entry.insert_text = name + suffix;
        choices.push_back(std::move(entry));
      }
      mode = "keys";
    } else {
      auto key = trim(line.substr(0, colon));
      std::string kind, enum_namespace;
      for (const auto &field : schema)
        if (field.at("name") == key &&
            field.at("schema").at("kind").is_string()) {
          kind = field.at("schema").at("kind");
          enum_namespace = field.at("schema").value("namespace", "");
        }
      auto space = kind == "enum"    ? enum_namespace
                   : kind == "roles" ? "role"
                                     : kind;
      if (space == "role" || space == "unused" || space == "bias") {
        for (const auto &entry : catalog)
          if (entry.kind == 20 && entry.type_kind == space &&
              !entry.shadowed(offset))
            choices.push_back(entry);
        mode = "enum";
      } else if (kind == "bool") {
        choices = {{"false", "boolean", 12}, {"true", "boolean", 12}};
        mode = "enum";
      } else if (std::set<std::string>{"expression", "number", "positive",
                                       "nonnegative", "current", "voltage",
                                       "capacitance", "resistance"}
                     .contains(kind))
        mode = "expression";
    }
  }
  if (mode == "expression") {
    std::set<std::string> names;
    // Recover local parameter declarations without resolving an incomplete
    // value.
    std::string section;
    std::size_t body_indent = std::string::npos,
                field_indent = std::string::npos;
    static const std::regex field(R"(^([A-Za-z_#][A-Za-z0-9_#]*)\s*:)");
    for (const auto &row : rows(mask.substr(owner_end, stop - owner_end))) {
      if (body_indent == std::string::npos)
        body_indent = row.indent;
      if (row.indent == body_indent) {
        section = row.text;
        field_indent = std::string::npos;
      } else if (section == "params:") {
        if (field_indent == std::string::npos)
          field_indent = row.indent;
        std::smatch match;
        if (row.indent == field_indent &&
            std::regex_search(row.text, match, field))
          names.insert(match[1]);
      }
    }
    static const std::regex base_pattern(R"(\bextends\s+([A-Za-z0-9_#.]+))");
    std::smatch base;
    auto header = owner.str();
    bool inherited = std::regex_search(header, base, base_pattern);
    for (const auto &entry : catalog) {
      if (inherited && entry.name == base[1].str())
        names.insert(entry.parameter_names.begin(),
                     entry.parameter_names.end());
      if (entry.type_kind == "num" && !entry.shadowed(offset) &&
          !names.contains(entry.name.substr(0, entry.name.find('.'))))
        choices.push_back(entry);
    }
    for (const auto &name : names)
      choices.push_back({name, "local parameter", 6});
  }
  auto dot = word.rfind('.');
  auto qualifier = dot == std::string::npos ? 0 : dot + 1;
  result.start += qualifier;
  for (auto entry : choices)
    if (entry.name.starts_with(word)) {
      entry.name.erase(0, qualifier);
      if (!entry.insert_text.empty())
        entry.insert_text.erase(0, qualifier);
      result.entries.push_back(std::move(entry));
    }
  return result;
}
} // namespace noscheme
