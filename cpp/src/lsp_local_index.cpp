#include "noscheme/lsp_analysis.hpp"
#include "noscheme/validation.hpp"
#include <set>
namespace noscheme {
namespace {
// Parse independent top-level declarations for navigation only. The original
// module remains the authority for diagnostics. Failed declarations contribute
// no names or source ranges, and every surviving span is rebased to the buffer.
Json local_ast(const Source &source) {
  auto masked = code_mask(source.text);
  std::vector<std::size_t> starts;
  for (std::size_t line = 0; line < masked.size();) {
    for (auto word : {"from", "import", "component", "block", "board", "bundle",
                      "nettype"}) {
      auto length = std::char_traits<char>::length(word);
      if (masked.compare(line, length, word) == 0 &&
          line + length < masked.size() &&
          (masked[line + length] == ' ' || masked[line + length] == '\t')) {
        starts.push_back(line);
        break;
      }
    }
    auto end = masked.find('\n', line);
    if (end == std::string::npos)
      break;
    line = end + 1;
  }
  starts.push_back(source.text.size());
  auto items = Json::array();
  for (std::size_t i = 0; i + 1 < starts.size(); ++i) {
    try {
      auto ast =
          parse(Source(source.text.substr(starts[i], starts[i + 1] - starts[i]),
                       source.filename));
      std::function<void(Json &)> rebase = [&](Json &value) {
        if (value.is_array()) {
          for (auto &v : value)
            rebase(v);
        } else if (value.is_object()) {
          if (value.contains("start_pos") && value.contains("end_pos") &&
              value.contains("filename")) {
            value = source.span(
                starts[i] + value.at("start_pos").get<std::size_t>(),
                std::min(source.text.size(),
                         starts[i] + value.at("end_pos").get<std::size_t>()));
          } else
            for (auto &[key, v] : value.items())
              rebase(v);
        }
      };
      rebase(ast);
      for (const auto &item : ast.at("items"))
        items.push_back(item);
    } catch (const Failure &) {
    } catch (const DiagnosticsFailure &) {
    }
  }
  return object("items", items);
}
} // namespace
void local_index(LspAnalysis &result, const Sources &sources,
                 const ParsedSources &cache,
                 const RegistryDescription &registry,
                 const std::set<fs::path> &indexed, std::stop_token stop) {
  for (const auto &[path, text] : sources) {
    check_cancelled(stop);
    if (path.extension() != ".nosch" || indexed.contains(path))
      continue;
    auto found = cache.find(path);
    auto ast = found != cache.end() && found->second.text == text
                   ? found->second.ast
                   : std::make_shared<const Json>(
                         local_ast(Source(text, path.string())));
    Project project;
    auto &module = project.modules["local"];
    module = {"local", path, ast, {}};
    auto add = [&](std::string name, Symbol symbol) {
      // Ambiguous local/import spellings are deliberately unavailable.
      if (module.symbols.contains(name))
        module.symbols[name] = Symbol{};
      else
        module.symbols.emplace(std::move(name), std::move(symbol));
    };
    const std::set<std::string> builtins{"role", "nettype", "unused",
                                         "num",  "bias",    "std"};
    for (const auto &item : ast->at("items")) {
      if (item.at("node") == "Definition") {
        auto name = item.at("name").at("text").get<std::string>();
        add(name, {"local", name, &item});
      } else if (item.at("node") == "Import" && item.at("module").size() == 1 &&
                 item.at("module")[0].at("text") == "noscheme") {
        if (item.at("names").empty())
          for (const auto &name : builtins)
            add("noscheme." + name, {"noscheme", name});
        else
          for (const auto &entry : item.at("names")) {
            auto name = entry.at("name").at("text").get<std::string>();
            if (builtins.contains(name))
              add((entry.at("alias").is_null() ? entry.at("name")
                                               : entry.at("alias"))
                      .at("text"),
                  {"noscheme", name});
          }
      }
    }
    Definitions definitions;
    try {
      definitions = resolve_interfaces(project, registry);
    } catch (const DiagnosticsFailure &) {
    }
    try {
      auto refs = reference_index(project, definitions, registry);
      result.references.insert(result.references.end(), refs.begin(),
                               refs.end());
    } catch (const DiagnosticsFailure &) {
    }
    // Full project completion recovery takes precedence over a local index.
    if (!result.completions.contains(path)) {
      auto catalog = completion_catalog(project, registry);
      try {
        interface_completions(catalog, project, registry, definitions);
      } catch (const DiagnosticsFailure &) {
      }
      result.completions.merge(catalog);
    }
  }
}
} // namespace noscheme
