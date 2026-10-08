#include "noscheme/lsp_analysis.hpp"
#include <set>
namespace noscheme {
std::vector<ProjectConfig>
workspace_projects(const std::vector<fs::path> &roots, const Sources &sources,
                   std::stop_token stop) {
  std::set<fs::path> paths;
  for (const auto &root : roots) {
    std::error_code error;
    fs::recursive_directory_iterator cursor(
        root, fs::directory_options::skip_permission_denied, error),
        end;
    while (cursor != end) {
      check_cancelled(stop);
      auto name = cursor->path().filename().string();
      if (cursor->is_directory(error)) {
        if (name.starts_with('.') || name == "build" || name == "dist" ||
            name == "__pycache__" || name == "node_modules")
          cursor.disable_recursion_pending();
      } else if (name == "noscheme.toml")
        paths.insert(fs::weakly_canonical(cursor->path()));
      cursor.increment(error);
      if (error)
        break;
    }
  }
  for (const auto &[path, text] : sources)
    if (path.filename() == "noscheme.toml")
      for (const auto &root : roots) {
        auto relative = path.lexically_relative(root);
        if (!relative.empty() && *relative.begin() != "..")
          paths.insert(path);
      }
  std::vector<ProjectConfig> result;
  for (const auto &path : paths) {
    check_cancelled(stop);
    try {
      result.push_back(read_config(path, sources));
    } catch (const std::exception
                 &) { /* Unrelated invalid project configs are ignored. */
    }
  }
  return result;
}
std::optional<fs::path>
workspace_project(const fs::path &file,
                  const std::vector<ProjectConfig> &configs) {
  std::size_t deepest = 0;
  std::set<fs::path> matches;
  for (const auto &config : configs)
    for (const auto &root : config.search_paths()) {
      auto relative = file.lexically_relative(root);
      if (relative.empty() || *relative.begin() == "..")
        continue;
      auto depth =
          static_cast<std::size_t>(std::distance(root.begin(), root.end()));
      if (depth > deepest) {
        deepest = depth;
        matches.clear();
      }
      if (depth == deepest)
        matches.insert(config.path);
    }
  if (matches.size() > 1)
    throw std::runtime_error("Multiple NoScheme projects contain this file; "
                             "start noscheme-lsp with --project PATH.");
  return matches.empty() ? std::nullopt : std::optional(*matches.begin());
}
} // namespace noscheme
