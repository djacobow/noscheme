#include "noscheme/lsp_code_actions.hpp"
#include "noscheme/lsp_completion_scan.hpp"
#include "noscheme/source.hpp"
#include <regex>
namespace noscheme {
namespace {
Json action_edits(const std::string &text, std::size_t start, const Json &pins,
                  const Json &original_params, bool metadata) {
  auto mask = code_mask(text);
  auto line_start = start ? text.rfind('\n', start - 1) : std::string::npos;
  line_start = line_start == std::string::npos ? 0 : line_start + 1;
  auto indent = text.substr(line_start, start - line_start);
  std::string newline = text.find("\r\n") == std::string::npos ? "\n" : "\r\n";
  auto header_end = text.size();
  int depth = 0;
  for (auto i = start; i < mask.size(); ++i) {
    if (mask[i] == '(' || mask[i] == '[')
      ++depth;
    if (mask[i] == ')' || mask[i] == ']')
      --depth;
    if (mask[i] == '\n' && depth == 0) {
      header_end = i + 1;
      break;
    }
  }
  struct Section {
    std::string name;
    std::size_t start;
  };
  std::vector<Section> sections;
  auto stop = text.size();
  auto default_body_indent = indent + (indent.empty() ? "    " : indent);
  auto body_indent = default_body_indent;
  for (auto pos = header_end; pos < text.size();) {
    auto end = text.find('\n', pos);
    if (end == std::string::npos)
      end = text.size();
    auto row = mask.substr(pos, end - pos);
    auto first = row.find_first_not_of(" \t\r");
    if (first != std::string::npos) {
      if (first <= indent.size()) {
        stop = pos;
        break;
      }
      if (sections.empty() && body_indent == default_body_indent)
        body_indent = row.substr(0, first);
      auto content = completion_scan::trim(row);
      if (first == body_indent.size() &&
          (content == "params:" || content == "metadata:" ||
           content == "elements:"))
        sections.push_back({content, pos});
    }
    pos = end + 1;
  }
  auto step = body_indent.substr(indent.size());
  auto region = text.substr(start, stop - start);
  Json params = Json::array();
  for (const auto &param : original_params) {
    auto name = param.at("name").get<std::string>();
    if (param.at("required").get<bool>() ||
        region.find("// " + name + ": TODO_parameter_" + name) ==
            std::string::npos)
      params.push_back(param);
  }
  metadata = metadata && region.find("// metadata:") == std::string::npos;
  auto used = text;
  auto placeholder = [&](std::string name) {
    while (used.find(name) != std::string::npos)
      name += "_";
    used += " " + name;
    return name;
  };
  std::map<std::size_t, std::string> edits;
  auto connections_end = sections.empty() ? stop : sections.front().start;
  static const std::regex plain(R"([A-Za-z_#][A-Za-z0-9_#]*|[0-9]+)");
  for (std::size_t i = 0; i < pins.size(); ++i) {
    auto name = pins[i].get<std::string>();
    if (!std::regex_match(name, plain))
      name = Json(name).dump();
    edits[connections_end] +=
        body_indent + name + ": " +
        placeholder("TODO_connection_" + std::to_string(i + 1)) + newline;
  }
  if (!params.empty()) {
    auto section =
        std::find_if(sections.begin(), sections.end(),
                     [](const Section &s) { return s.name == "params:"; });
    auto at = connections_end;
    if (section != sections.end())
      at = std::next(section) == sections.end() ? stop
                                                : std::next(section)->start;
    bool commented = section == sections.end() &&
                     std::ranges::none_of(params, [](const Json &p) {
                       return p.at("required").get<bool>();
                     });
    if (section == sections.end())
      edits[at] +=
          body_indent + (commented ? "// params:" : "params:") + newline;
    for (const auto &param : params) {
      auto name = param.at("name").get<std::string>();
      edits[at] += body_indent + step +
                   (param.at("required").get<bool>() ? "" : "// ") + name +
                   ": " + placeholder("TODO_parameter_" + name) + newline;
    }
  }
  if (metadata)
    edits[stop] += body_indent + "// metadata:" + newline + body_indent +
                   "//     Add metadata fields here." + newline;
  Json result = Json::array();
  for (const auto &[at, inserted] : edits)
    result.push_back(
        object("offset", at, "text",
               (at && text[at - 1] != '\n' ? newline : "") + inserted));
  return result;
}
} // namespace
Json instance_actions(const Source &source, std::size_t offset,
                      const std::vector<CompletionEntry> &catalog) {
  Json result = Json::array();
  try {
    parse(std::make_shared<const Source>(source));
  } catch (const std::exception &) {
    return result;
  }
  for (const auto &entry : catalog) {
    if (entry.instance.is_null() || !entry.instance.contains("actions") ||
        !entry.visible_in || offset < entry.visible_in.start ||
        offset >= entry.visible_in.end)
      continue;
    const auto &data = entry.instance.at("actions");
    if (!data.at("safe").get<bool>())
      return result;
    for (bool expanded : {false, true}) {
      Json pins = Json::array(), params = Json::array();
      for (const auto &pin : data.at("pins"))
        if (expanded || pin.at("required").get<bool>())
          pins.push_back(pin.at("name"));
      for (const auto &param : data.at("params"))
        if (expanded || param.at("required").get<bool>())
          params.push_back(param);
      std::sort(params.begin(), params.end(), [](const Json &a, const Json &b) {
        return a.at("name") < b.at("name");
      });
      auto edits = action_edits(source.text, entry.visible_in.start, pins,
                                params, false);
      if (!edits.empty())
        result.push_back(object("title",
                                expanded
                                    ? "Add all connections and parameters"
                                    : "Add required connections and parameters",
                                "edits", edits));
    }
    if (!data.at("metadata").get<bool>()) {
      auto edits = action_edits(source.text, entry.visible_in.start,
                                Json::array(), Json::array(), true);
      if (!edits.empty())
        result.push_back(
            object("title", "Add metadata template", "edits", edits));
    }
    break;
  }
  return result;
}
} // namespace noscheme
