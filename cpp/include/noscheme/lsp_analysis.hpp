#pragma once
#include "noscheme/cancellation.hpp"
#include "noscheme/lsp_completion.hpp"
#include "noscheme/lsp_references.hpp"
#include "noscheme/lsp_rename.hpp"
#include "noscheme/project.hpp"
#include <optional>
#include <set>
namespace noscheme {
// Local file URIs only. Percent encoding is handled at the editor boundary.
std::optional<fs::path> uri_path(const std::string &uri);
std::string file_uri(const fs::path &path);
struct LspAnalysis {
  Json diagnostics = Json::array();
  Sources sources;
  std::map<fs::path, std::optional<std::string>> disk_inputs;
  std::vector<LspReference> references;
  CompletionCatalog completions;
  std::vector<std::shared_ptr<const RenameContext>> rename_contexts;
  // Keeps alive the sources of reference and completion spans.
  std::vector<std::shared_ptr<const ast::Module>> modules;
  void retain(const Project &project) {
    for (const auto &[name, module] : project.modules)
      modules.push_back(module.ast);
  }
};
bool inputs_current(const LspAnalysis &analysis);
std::vector<ProjectConfig>
workspace_projects(const std::vector<fs::path> &roots, const Sources &sources,
                   std::stop_token stop);
std::optional<fs::path>
workspace_project(const fs::path &file,
                  const std::vector<ProjectConfig> &configs);
void local_index(LspAnalysis &result, const Sources &sources,
                 const ParsedSources &cache,
                 const RegistryDescription &registry,
                 const std::set<fs::path> &indexed, std::stop_token stop);
// Called on the analysis worker; the cache reuses only exactly matching text.
// Cancellation is checked between native phases and while waiting for Python.
LspAnalysis analyze_documents(
    const Sources &sources, const fs::path &project, const fs::path &python,
    ParsedSources &cache, std::stop_token stop = {},
    const std::function<void(const LspAnalysis &)> &ready = {},
    const std::vector<fs::path> &workspace_roots = {},
    std::optional<RegistryDescription> *standard_registry = nullptr);
} // namespace noscheme
