#include "noscheme/lsp_analysis.hpp"
#include "noscheme/validation.hpp"
#include <set>
namespace noscheme {
namespace {
// Parse independent top-level declarations for navigation only. The original
// module remains the authority for diagnostics. Failed declarations contribute
// no names or source ranges, and every surviving span is rebased to the buffer.
std::shared_ptr<const ast::Module>
local_ast(std::shared_ptr<const Source> shared_source) {
  const auto &source = *shared_source;
  auto masked = code_mask(source.text);
  std::vector<std::size_t> starts;
  for (std::size_t line = 0; line < masked.size();) {
    for (auto word :
         {"from", "import", "component", "block", "bundle", "extend"}) {
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
  auto result = std::make_shared<ast::Module>();
  result->span = {&source, 0, source.text.size()};
  result->source = shared_source;
  for (std::size_t i = 0; i + 1 < starts.size(); ++i) {
    try {
      auto fragment = parse_fragment(shared_source, starts[i], starts[i + 1]);
      result->items.insert(result->items.end(), fragment->items.begin(),
                           fragment->items.end());
    } catch (const Failure &) {
    } catch (const DiagnosticsFailure &) {
    }
  }
  return result;
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
    auto ast =
        found != cache.end() && found->second.text == text
            ? found->second.ast
            : local_ast(std::make_shared<const Source>(text, path.string()));
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
    const std::set<std::string> builtins{"role", "net",  "use",
                                         "num",  "bias", "std"};
    for (const auto &item : ast->items) {
      if (auto *definition = ast::as<ast::Definition>(item)) {
        const auto &name = definition->name.text;
        add(name, {"local", name, definition});
      } else if (auto *import = ast::as<ast::Import>(item);
                 import && import->module.size() == 1 &&
                 import->module[0].text == "noscheme") {
        if (import->names.empty())
          for (const auto &name : builtins)
            add("noscheme." + name, {"noscheme", name});
        else
          for (const auto &entry : import->names)
            if (builtins.contains(entry.name.text))
              add(entry.local().text, {"noscheme", entry.name.text});
      }
    }
    try {
      module.ast = classified_module("local", *ast, module.symbols, true);
    } catch (const DiagnosticsFailure &) {
    }
    result.retain(project);
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
