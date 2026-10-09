#include "noscheme/lsp_erc_completion.hpp"
#include "noscheme/lsp_completion_scan.hpp"
#include <algorithm>
#include <set>
namespace noscheme {
namespace {
using completion_scan::name_char;
using completion_scan::space;
using completion_scan::trim;
struct Call {
  std::string name;
  std::size_t begin, name_end, opening, end;
  bool closed;
  std::string scope;
};
std::vector<Call> calls(const std::string &text) {
  auto mask = code_mask(text);
  std::vector<Call> result;
  std::vector<std::pair<std::size_t, std::string>> ancestors;
  std::size_t skip = 0;
  for (std::size_t line = 0; line < text.size();) {
    auto end = text.find('\n', line);
    if (end == std::string::npos)
      end = text.size();
    auto first = line;
    while (first < end && space(mask[first]))
      ++first;
    if (first < end && first >= skip) {
      auto indent = first - line;
      while (!ancestors.empty() && ancestors.back().first >= indent)
        ancestors.pop_back();
      bool checks = false, custom = false;
      std::string scope = "net";
      for (const auto &[depth, name] : ancestors) {
        if (name == "checks:")
          checks = true;
        if (name == "custom:")
          custom = true;
        if (name == "pins:")
          scope = "pin";
      }
      auto colon = mask.find(':', first);
      if (checks && !custom && colon < end) {
        auto begin = colon + 1;
        while (begin < end && space(mask[begin]))
          ++begin;
        auto opening = begin;
        while (opening < end &&
               (name_char(mask[opening]) || mask[opening] == '.'))
          ++opening;
        auto name = mask.substr(begin, opening - begin);
        auto name_end = opening;
        while (opening < end && space(mask[opening]))
          ++opening;
        if (!name.empty() && opening < end && mask[opening] == '(') {
          auto range = argument_range(text, mask, opening, indent);
          result.push_back(
              {name, begin, name_end, opening, range.end, range.closed, scope});
          skip = range.end + (range.closed ? 1 : 0);
        } else if (opening == end) {
          result.push_back({name, begin, name_end, end, end, false, scope});
        }
      }
      ancestors.emplace_back(indent, trim(mask.substr(first, end - first)));
    }
    line = end + 1;
  }
  return result;
}
} // namespace
ArgumentRange argument_range(const std::string &text, const std::string &mask,
                             std::size_t opening, std::size_t indent) {
  auto limit = text.size();
  auto line_end = text.find('\n', opening);
  for (auto next = line_end == std::string::npos ? text.size() : line_end + 1;
       next < text.size();) {
    auto stop = text.find('\n', next);
    if (stop == std::string::npos)
      stop = text.size();
    auto content = next;
    while (content < stop && space(mask[content]))
      ++content;
    if (content < stop && content - next <= indent && mask[content] != ')') {
      limit = next;
      break;
    }
    next = stop + 1;
  }
  auto close = opening + 1;
  int depth = 1;
  while (close < limit) {
    if (mask[close] == '(')
      ++depth;
    if (mask[close] == ')' && !--depth)
      return {close, true};
    ++close;
  }
  while (close > opening + 1 &&
         (text[close - 1] == '\n' || text[close - 1] == '\r'))
    --close;
  return {close, false};
}
std::vector<TextRepair> erc_call_repairs(const std::string &text) {
  std::vector<TextRepair> result;
  for (const auto &call : calls(text)) {
    if (call.opening == call.end && !call.closed)
      result.push_back({call.begin, call.end, "0"});
    else
      result.push_back(
          {call.opening + 1, call.end + (call.closed ? 1 : 0), ")"});
  }
  return result;
}
std::optional<CompletionResult>
erc_completion(const std::string &text, std::size_t offset,
               const std::vector<CompletionEntry> &catalog) {
  auto mask = code_mask(text);
  for (const auto &call : calls(text)) {
    if (offset >= call.begin && offset <= call.name_end) {
      CompletionResult result{call.begin, call.name_end, {}};
      auto prefix = text.substr(call.begin, offset - call.begin);
      auto dot = prefix.rfind('.');
      auto skip = dot == std::string::npos ? 0 : dot + 1;
      result.start += skip;
      for (const auto &entry : catalog) {
        if (entry.rule.is_null() || entry.rule.at("scope") != call.scope ||
            !entry.name.starts_with(prefix))
          continue;
        if (entry.shadowed(offset))
          continue;
        auto name = entry.name.substr(skip);
        if (!skip || name.find('.') == std::string::npos)
          result.entries.push_back({name, entry.description, entry.kind});
      }
      return result;
    }
    if (offset <= call.opening || offset > call.end)
      continue;
    CompletionResult result{offset, offset, {}};
    const CompletionEntry *rule = nullptr;
    for (const auto &entry : catalog)
      if (entry.name == call.name && !entry.rule.is_null()) {
        if (!entry.shadowed(offset))
          rule = &entry;
      }
    if (!rule)
      return result;
    // A sentinel distinguishes code from masked comments/strings even when
    // the cursor follows whitespace.
    if (code_mask(text.substr(0, offset) + "x", true).back() != 'x')
      return result;
    std::vector<std::pair<std::size_t, std::size_t>> arguments;
    int depth = 0;
    auto begin = call.opening + 1;
    for (auto pos = begin; pos < call.end; ++pos) {
      if (mask[pos] == '(' || mask[pos] == '[')
        ++depth;
      if (mask[pos] == ')' || mask[pos] == ']')
        --depth;
      if (mask[pos] == ',' && !depth) {
        arguments.emplace_back(begin, pos);
        begin = pos + 1;
      }
    }
    arguments.emplace_back(begin, call.end);
    const auto &params = rule->rule.at("parameters");
    std::set<std::string> used;
    bool preceding_keyword = false;
    std::size_t positional = 0, current_index = 0;
    std::string current;
    std::size_t current_start = offset;
    for (const auto &[start, end] : arguments) {
      auto segment = trim(mask.substr(start, end - start));
      if (offset >= start && offset <= end) {
        current = mask.substr(start, offset - start);
        current_start = start;
        current_index = positional;
        if (!segment.empty() && segment.find('=') == std::string::npos)
          ++positional;
        continue;
      }
      auto equal = segment.find('=');
      if (equal != std::string::npos) {
        used.insert(trim(segment.substr(0, equal)));
        if (start < offset)
          preceding_keyword = true;
      } else if (!segment.empty()) {
        if (positional < params.size())
          used.insert(params.at(positional).at("name"));
        ++positional;
      }
    }
    int nested = 0;
    for (char c : current) {
      if (c == '(')
        ++nested;
      else if (c == ')')
        --nested;
    }
    if (nested)
      return result;
    auto word_start = offset;
    while (word_start > current_start &&
           (name_char(mask[word_start - 1]) || mask[word_start - 1] == '.'))
      --word_start;
    auto word_end = offset;
    while (word_end < call.end && name_char(mask[word_end]))
      ++word_end;
    auto prefix = mask.substr(word_start, offset - word_start);
    result.start = word_start;
    result.end = word_end;
    std::set<std::string> offered;
    auto offer = [&](std::string name, std::string detail, int kind,
                     std::string insert) {
      if (name.starts_with(prefix) && offered.insert(name).second)
        result.entries.push_back({name, detail, kind, {}, {}, insert});
    };
    auto equal = current.find('=');
    std::string kind;
    if (equal == std::string::npos) {
      auto partial = trim(current);
      if (std::all_of(partial.begin(), partial.end(), name_char)) {
        auto next = word_end;
        while (next < call.end && space(mask[next]))
          ++next;
        auto suffix = next < call.end && mask[next] == '=' ? "" : "=";
        for (const auto &param : params) {
          auto name = param.at("name").get<std::string>();
          if (!used.contains(name))
            offer(name, param.at("schema").at("kind"), 10, name + suffix);
        }
        if (!used.contains("missing"))
          offer("missing", "missing-data policy", 10,
                "missing" + std::string(suffix));
      }
      if (!preceding_keyword && current_index < params.size())
        kind = params.at(current_index).at("schema").at("kind");
    } else {
      auto keyword = trim(current.substr(0, equal));
      if (keyword == "missing")
        kind = "missing";
      else
        for (const auto &param : params)
          if (param.at("name") == keyword)
            kind = param.at("schema").at("kind");
    }
    if (code_mask(text.substr(0, offset) + "x").back() != 'x') {
      result.entries.clear();
      if (kind != "missing" || equal == std::string::npos)
        return result;
      auto quote = current_start + equal + 1;
      auto literals = code_mask(text, true);
      while (quote < offset && space(literals[quote]))
        ++quote;
      if (quote >= offset || literals[quote] != '"')
        return result;
      auto decoded = Json::parse(text.substr(quote, offset - quote) + "\"",
                                 nullptr, false);
      if (!decoded.is_string())
        return result;
      auto finish = offset;
      for (; finish < call.end && text[finish] != '\n' && text[finish] != '\r';
           ++finish) {
        if (text[finish] == '\\' && finish + 1 < call.end)
          ++finish;
        else if (text[finish] == '"')
          break;
      }
      result.start = quote;
      result.end =
          finish < call.end && text[finish] == '"' ? finish + 1 : offset;
      for (const auto &value : rule->rule.at("values").at(kind)) {
        auto spelling = value.get<std::string>();
        auto name = Json::parse(spelling).get<std::string>();
        if (name.starts_with(decoded.get<std::string>()))
          result.entries.push_back(
              {spelling, "missing-data policy", 12, {}, {}, spelling});
      }
      return result;
    }
    if (std::set<std::string>{"number", "positive", "nonnegative", "current",
                              "voltage", "capacitance", "resistance"}
            .contains(kind)) {
      auto scoped = scope_completion(text, offset, catalog, true);
      if (equal != std::string::npos || prefix.find('.') != std::string::npos)
        return scoped;
      for (auto &entry : scoped.entries)
        result.entries.push_back(std::move(entry));
    }
    if (rule->rule.at("values").contains(kind))
      for (const auto &name : rule->rule.at("values").at(kind))
        offer(name, kind + " argument", 12, name);
    return result;
  }
  return std::nullopt;
}
} // namespace noscheme
