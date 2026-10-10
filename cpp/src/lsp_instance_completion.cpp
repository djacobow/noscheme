#include "noscheme/lsp_instance_completion.hpp"
#include "noscheme/lsp_completion_scan.hpp"
#include <algorithm>
#include <set>
namespace noscheme {
namespace {
using completion_scan::name_char;
using completion_scan::space;
using completion_scan::trim;
struct InstanceList {
  std::size_t start, opening, end;
  bool closed;
};
std::vector<InstanceList> lists(const std::string &text) {
  auto mask = code_mask(text);
  std::vector<InstanceList> result;
  for (std::size_t line = 0; line < text.size();) {
    auto end = text.find('\n', line);
    if (end == std::string::npos)
      end = text.size();
    auto start = line;
    while (start < end && space(mask[start]))
      ++start;
    std::smatch declaration;
    auto header = completion_scan::header_end(mask, start);
    auto content = mask.substr(start, header - start);
    std::replace(content.begin(), content.end(), '\n', ' ');
    if (start > line &&
        std::regex_search(content, declaration,
                          completion_scan::declaration_pattern())) {
      auto opening = start + declaration.length();
      int depth = 0;
      while (opening < header) {
        if (mask[opening] == '[')
          ++depth;
        else if (mask[opening] == ']')
          --depth;
        else if (!depth && !space(mask[opening]))
          break;
        ++opening;
      }
      if (opening < header && mask[opening] == '(') {
        auto range = argument_range(text, mask, opening, start - line);
        result.push_back({start, opening, range.end, range.closed});
      }
    }
    line = end + 1;
  }
  return result;
}
std::string unindex(const std::string &s) {
  std::string result;
  int depth = 0;
  for (char c : s) {
    if (c == '[')
      ++depth;
    else if (c == ']')
      --depth;
    else if (!depth)
      result += c;
  }
  return result;
}
} // namespace
std::vector<TextRepair> instance_repairs(const std::string &text) {
  std::vector<TextRepair> result;
  for (const auto &list : lists(text))
    result.push_back({list.opening + 1, list.end + (list.closed ? 1 : 0), ")"});
  return result;
}
std::optional<CompletionResult>
instance_completion(const std::string &text, std::size_t offset,
                    const std::vector<CompletionEntry> &catalog) {
  auto mask = code_mask(text);
  for (const auto &list : lists(text)) {
    if (offset <= list.opening || offset > list.end)
      continue;
    CompletionResult result{offset, offset, {}};
    if (code_mask(text.substr(0, offset) + "x").back() != 'x')
      return result;
    const Json *context = nullptr;
    const std::vector<CompletionEntry> *targets = nullptr;
    for (const auto &entry : catalog)
      if (!entry.instance.is_null() && entry.visible_in.start == list.start) {
        context = &entry.instance;
        targets = entry.targets.get();
      }
    if (!context)
      return result;
    std::vector<std::pair<std::size_t, std::size_t>> args;
    auto begin = list.opening + 1;
    int depth = 0;
    for (auto i = begin; i < list.end; ++i) {
      if (mask[i] == '(' || mask[i] == '[')
        ++depth;
      if (mask[i] == ')' || mask[i] == ']')
        --depth;
      if (mask[i] == ',' && !depth) {
        args.emplace_back(begin, i);
        begin = i + 1;
      }
    }
    args.emplace_back(begin, list.end);
    std::set<std::string> bound, params;
    std::size_t positional = 0;
    bool named = false;
    std::string current;
    for (const auto &[start, end] : args) {
      if (offset >= start && offset <= end) {
        current = mask.substr(start, offset - start);
        continue;
      }
      auto segment = trim(mask.substr(start, end - start));
      auto colon = segment.find(':'), equal = segment.find('=');
      if (colon != std::string::npos && segment.find('[') > colon) {
        auto pin = trim(segment.substr(0, colon));
        if (context->at("pins").contains(pin))
          bound.insert(context->at("pins").at(pin));
        named = true;
      } else if (equal != std::string::npos)
        params.insert(trim(segment.substr(0, equal)));
      else if (!segment.empty())
        ++positional;
    }
    auto separator = current.find(':');
    // Colons inside array selections belong to the reference.
    if (separator != std::string::npos && current.find('[') < separator)
      separator = std::string::npos;
    if (current.find('=') != std::string::npos)
      return std::nullopt;
    bool target = separator != std::string::npos;
    return connection_choices(
        text, offset, list.end,
        target ? current.substr(separator + 1) : current, *context,
        !target && !positional, false,
        target || (!named &&
                   positional < context->at("positional").get<std::size_t>()),
        bound, params, false, false, targets);
  }
  return std::nullopt;
}
CompletionResult connection_choices(
    const std::string &text, std::size_t offset, std::size_t limit,
    const std::string &raw_fragment, const Json &context, bool pin_choices,
    bool parameter_choices, bool target_choices,
    const std::set<std::string> &bound, const std::set<std::string> &params,
    bool section, bool grouped, const std::vector<CompletionEntry> *targets) {
  CompletionResult result{offset, offset, {}};
  auto mask = code_mask(text);

  auto fragment = trim(raw_fragment);
  if (!fragment.empty() && space(raw_fragment.back()))
    return result;
  // A cursor inside an index or a grouped pin list is not a member-name edit.
  int brackets = 0;
  for (char c : fragment) {
    if (c == '[')
      ++brackets;
    if (c == ']')
      --brackets;
    if (brackets < 0 || c == ',')
      return result;
  }
  if (brackets)
    return result;
  auto prefix = unindex(fragment);
  if (section && prefix.starts_with("."))
    prefix.erase(0, 1);
  if (prefix.find_first_of(" \t\r\n(),") != std::string::npos)
    return result;
  auto raw_dot = fragment.rfind('.');
  auto dot = prefix.rfind('.');
  auto qualifier = dot == std::string::npos ? 0 : dot + 1;
  result.start =
      offset - (raw_dot == std::string::npos ? fragment.size()
                                             : fragment.size() - raw_dot - 1);
  result.end = offset;
  while (result.end < limit && name_char(mask[result.end]))
    ++result.end;
  auto next = result.end;
  while (next < limit && space(mask[next]))
    ++next;
  std::set<std::pair<std::string, int>> offered;
  auto offer = [&](const std::string &name, int kind, const std::string &suffix,
                   const std::string &detail) {
    if (!name.starts_with(prefix))
      return;
    auto tail = name.substr(qualifier);
    if ((tail.find('.') != std::string::npos &&
         !(qualifier == 0 && name.starts_with("global.") &&
           name.find('.', 7) == std::string::npos)) ||
        !offered.insert({tail, kind}).second)
      return;
    auto inserted = tail;
    if (kind == 5 && !std::all_of(tail.begin(), tail.end(), name_char))
      inserted = Json(tail).dump();
    result.entries.push_back({tail, detail, kind, {}, {}, inserted + suffix});
  };
  if (pin_choices) {
    auto pin_names = section ? context.at("fields") : Json::array();
    if (!section)
      for (const auto &[pin, canonical] : context.at("pins").items())
        pin_names.push_back(pin);
    for (const auto &value : pin_names) {
      auto pin = value.get<std::string>();
      auto root = pin.substr(0, pin.find('.'));
      auto canonical = context.at("pins").value(root, root);
      if (!bound.contains(canonical))
        offer(pin, 5,
              grouped || (next < limit && mask[next] == ':') ? "" : ": ",
              "named pin connection");
    }
  }
  if (parameter_choices && qualifier == 0) {
    for (const auto &param : context.at("params")) {
      auto name = param.get<std::string>();
      if (!params.contains(name))
        offer(name, 10, next < limit && mask[next] == '=' ? "" : "=",
              "instance parameter");
    }
  }
  if (target_choices && targets)
    for (const auto &entry : *targets)
      offer(entry.name, 6, "", "connection target");
  return result;
}
} // namespace noscheme
