#include "noscheme/lsp_completion_scan.hpp"
#include "noscheme/lsp_instance_completion.hpp"
namespace noscheme {
namespace {
using completion_scan::space;
using completion_scan::trim;
struct Section {
  std::size_t owner, begin, end, indent;
};
struct Ancestor {
  std::size_t start, indent;
  std::string text;
};
std::vector<Section> sections(const std::string &text) {
  auto mask = code_mask(text);
  std::vector<Ancestor> ancestors;
  std::vector<Section> result;
  for (std::size_t line = 0; line < text.size();) {
    auto end = text.find('\n', line);
    if (end == std::string::npos)
      end = text.size();
    auto first = line;
    while (first < end && space(mask[first]))
      ++first;
    auto content = trim(mask.substr(first, end - first));
    if (!content.empty() && content != ")") {
      auto indent = first - line;
      while (!ancestors.empty() && ancestors.back().indent >= indent)
        ancestors.pop_back();
      if (content == "connections:" && !ancestors.empty() &&
          (ancestors.back().text.starts_with("inst ") ||
           ancestors.back().text.starts_with("inst\t"))) {
        auto begin = std::min(end + 1, text.size());
        auto limit = text.size();
        for (auto next = begin; next < text.size();) {
          auto stop = text.find('\n', next);
          if (stop == std::string::npos)
            stop = text.size();
          auto word = next;
          while (word < stop && space(mask[word]))
            ++word;
          if (word < stop && word - next <= indent) {
            limit = next;
            break;
          }
          next = stop + 1;
        }
        result.push_back({ancestors.back().start, begin, limit, indent});
      }
      ancestors.push_back({first, indent, content});
    }
    line = end + 1;
  }
  return result;
}
std::size_t binding_colon(const std::string &text) {
  int depth = 0;
  for (std::size_t i = 0; i < text.size(); ++i) {
    if (text[i] == '[')
      ++depth;
    else if (text[i] == ']')
      --depth;
    else if (text[i] == ':' && !depth)
      return i;
  }
  return std::string::npos;
}
// Logical rows preserve multiline pin lists and retain original byte offsets.
std::vector<std::pair<std::size_t, std::size_t>> rows(const std::string &mask,
                                                      const Section &section) {
  std::vector<std::pair<std::size_t, std::size_t>> result;
  auto begin = section.begin;
  int depth = 0;
  for (auto i = begin; i < section.end; ++i) {
    if (mask[i] == '[')
      ++depth;
    if (mask[i] == ']')
      --depth;
    if (mask[i] == '\n' && depth == 0) {
      result.emplace_back(begin, i);
      begin = i + 1;
    }
  }
  result.emplace_back(begin, section.end);
  return result;
}
// Skip a complete instance-array selector only when followed by its dot.
std::size_t endpoint_head(const std::string &mask, const std::string &literals,
                          std::size_t begin, std::size_t end) {
  while (begin < end && space(mask[begin]) && literals[begin] != '"')
    ++begin;
  auto cursor = begin;
  while (cursor < end && mask[cursor] == '[') {
    int depth = 1;
    for (++cursor; cursor < end && depth; ++cursor) {
      if (mask[cursor] == '[')
        ++depth;
      if (mask[cursor] == ']')
        --depth;
    }
    if (depth)
      return begin;
    while (cursor < end && space(mask[cursor]))
      ++cursor;
  }
  if (cursor < end && mask[cursor] == '.') {
    ++cursor;
    while (cursor < end && space(mask[cursor]))
      ++cursor;
    return cursor;
  }
  return begin;
}
void selected_pins(const std::string &raw, const Json &context,
                   std::set<std::string> &bound) {
  auto pin = trim(raw);
  if (pin.starts_with('"')) {
    auto decoded = Json::parse(pin, nullptr, false);
    if (!decoded.is_string())
      return;
    pin = decoded.get<std::string>();
    if (context.at("pins").contains(pin))
      bound.insert(context.at("pins").at(pin));
    return;
  }
  auto range = pin.find("..");
  if (range != std::string::npos) {
    auto low = trim(pin.substr(0, range)), high = trim(pin.substr(range + 2));
    auto integer = [](const std::string &n) {
      return !n.empty() && (n.size() == 1 || n.front() != '0') &&
             n.find_first_not_of("0123456789") == std::string::npos;
    };
    auto less = [](const std::string &a, const std::string &b) {
      return a.size() == b.size() ? a < b : a.size() < b.size();
    };
    if (!integer(low) || !integer(high))
      return;
    // Compare decimal strings instead of expanding an arbitrarily large range.
    for (const auto &[name, canonical] : context.at("pins").items())
      if (integer(name) && !less(name, low) && !less(high, name))
        bound.insert(canonical);
  } else if (context.at("pins").contains(pin))
    bound.insert(context.at("pins").at(pin));
}
void selected_key(const std::string &text, const std::string &mask,
                  std::size_t begin, std::size_t end, const Json &context,
                  std::set<std::string> &bound) {
  while (begin < end && space(mask[begin])) {
    // A masked quote is a pin ID, not leading whitespace.
    if (text[begin] == '"')
      break;
    ++begin;
  }
  if (begin < end && mask[begin] == '[' && end > begin &&
      trim(mask.substr(begin, end - begin)).ends_with(']')) {
    auto stop = mask.rfind(']', end - 1);
    auto member = begin + 1;
    for (auto i = member; i <= stop; ++i)
      if (i == stop || mask[i] == ',') {
        selected_pins(text.substr(member, i - member), context, bound);
        member = i + 1;
      }
  } else
    selected_pins(text.substr(begin, end - begin), context, bound);
}
std::optional<CompletionResult>
grouped_completion(const std::string &text, const std::string &mask,
                   std::size_t begin, std::size_t end, std::size_t offset,
                   const Json &context, std::set<std::string> bound) {
  while (begin < end && space(mask[begin]))
    ++begin;
  if (begin == end || mask[begin] != '[')
    return std::nullopt;
  auto close = mask.find(']', begin + 1);
  if (close == std::string::npos || close > end)
    close = end;
  auto next = close < end ? close + 1 : end;
  while (next < end && space(mask[next]))
    ++next;
  // [index].port is an instance selection, not a physical pin list.
  if (next < end && (mask[next] == '.' || mask[next] == '['))
    return std::nullopt;
  if (offset <= begin || offset > close)
    return std::nullopt;
  CompletionResult empty{offset, offset, {}};
  if (mask.substr(begin + 1, close - begin - 1).find('[') != std::string::npos)
    return empty;
  auto member = begin + 1, active = member;
  auto active_end = close;
  for (auto i = member; i <= close; ++i) {
    if (i == close || mask[i] == ',') {
      if (offset >= member && offset <= i) {
        active = member;
        active_end = i;
      } else
        selected_pins(text.substr(member, i - member), context, bound);
      member = i + 1;
    }
  }
  auto quote = active;
  while (quote < active_end && space(text[quote]))
    ++quote;
  if (quote < offset && text[quote] == '"') {
    auto finish = quote + 1;
    for (; finish < active_end && text[finish] != '\n' && text[finish] != '\r';
         ++finish) {
      if (text[finish] == '\\' && finish + 1 < active_end)
        ++finish;
      else if (text[finish] == '"')
        break;
    }
    if (offset > finish)
      return empty;
    auto decoded =
        Json::parse(text.substr(quote, offset - quote) + "\"", nullptr, false);
    if (!decoded.is_string())
      return empty;
    CompletionResult result{
        quote,
        finish < active_end && text[finish] == '"' ? finish + 1 : offset,
        {}};
    auto prefix = decoded.get<std::string>();
    for (const auto &[pin, canonical] : context.at("pins").items())
      if (pin.starts_with(prefix) && !bound.contains(canonical))
        result.entries.push_back(
            {pin, "named pin connection", 5, {}, nullptr, Json(pin).dump()});
    return result;
  }
  auto fragment = mask.substr(active, offset - active);
  if (fragment.find('.') != std::string::npos ||
      text.substr(active, offset - active).find('"') != std::string::npos)
    return empty;
  return connection_choices(text, offset, close, fragment, context, true, false,
                            false, bound, {}, false, true);
}
} // namespace
std::vector<TextRepair> section_repairs(const std::string &text) {
  std::vector<TextRepair> result;
  for (const auto &section : sections(text)) {
    // Only the recovered completion snapshot gets this placeholder binding.
    result.push_back({section.begin, section.end,
                      std::string(section.indent + 2, ' ') +
                          "_noscheme_completion: _noscheme_completion\n"});
  }
  return result;
}
std::optional<CompletionResult>
section_completion(const std::string &text, std::size_t offset,
                   const std::vector<CompletionEntry> &catalog) {
  auto mask = code_mask(text);
  auto literals = code_mask(text, true);
  for (const auto &section : sections(text)) {
    if (offset < section.begin || offset > section.end)
      continue;
    auto line = offset ? text.rfind('\n', offset - 1) : std::string::npos;
    line = line == std::string::npos ? 0 : line + 1;
    auto end = text.find('\n', offset);
    if (end == std::string::npos)
      end = text.size();
    auto first = line;
    while (first < offset && space(mask[first]))
      ++first;
    if (first - line <= section.indent)
      continue;
    CompletionResult empty{offset, offset, {}};
    if (code_mask(text.substr(0, offset) + "x", true).back() != 'x')
      return empty;
    const Json *context = nullptr;
    const std::vector<CompletionEntry> *targets = nullptr;
    for (const auto &entry : catalog)
      if (!entry.instance.is_null() &&
          entry.visible_in.at("start_pos").get<std::size_t>() ==
              section.owner) {
        context = &entry.instance;
        targets = entry.targets.get();
      }
    if (!context)
      return empty;
    auto logical = rows(mask, section);
    auto begin = first;
    for (auto [start, stop] : logical)
      if (offset >= start && offset <= stop) {
        begin = start;
        end = stop;
        break;
      }
    auto head = endpoint_head(mask, literals, begin, end);
    auto selection = trim(mask.substr(begin, head - begin));
    std::set<std::string> bound;
    for (auto [start, stop] : logical) {
      if (offset >= start && offset <= stop)
        continue;
      auto separator = binding_colon(mask.substr(start, stop - start));
      if (separator != std::string::npos) {
        auto other_head =
            endpoint_head(mask, literals, start, start + separator);
        auto other_selection = trim(mask.substr(start, other_head - start));
        if (other_selection.empty() || other_selection == selection)
          selected_key(literals, mask, other_head, start + separator, *context,
                       bound);
      }
    }
    if (auto group = grouped_completion(literals, mask, head, end, offset,
                                        *context, bound))
      return group;
    if (code_mask(text.substr(0, offset) + "x").back() != 'x')
      return empty;
    auto current = mask.substr(begin, offset - begin);
    auto colon = binding_colon(current);
    bool target = colon != std::string::npos;
    return connection_choices(
        text, offset, end, target ? current.substr(colon + 1) : current,
        *context, !target, false, target, bound, {}, true, false, targets);
  }
  return std::nullopt;
}
} // namespace noscheme
