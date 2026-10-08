#pragma once
#include "noscheme/source.hpp"
#include <filesystem>
#include <functional>
#include <map>
#include <memory>
#include <string_view>

namespace noscheme {
namespace fs = std::filesystem;
bool is_identifier(std::string_view text);
// Editor snapshots use resolved absolute filenames, like the Python loader.
using Sources = std::map<fs::path, std::string>;
struct ProjectConfig {
  fs::path path, output_dir;
  std::string language_version, board_module, board_name;
  std::vector<fs::path> source_paths, library_paths, erc_modules;
  std::vector<std::string> erc_rule_sets;
  Json span;
  std::vector<fs::path> search_paths() const;
};
struct Symbol {
  std::string module, name;
  // The module's shared immutable AST owns this declaration.
  const Json *declaration = nullptr;
};
using Symbols = std::map<std::string, Symbol>;
using ErcExports = std::map<std::string, Symbols>;
using ErcExportLoader = std::function<ErcExports(const ProjectConfig &)>;
struct LoadedModule {
  std::string name;
  fs::path path;
  std::shared_ptr<const Json> ast;
  Symbols symbols;
};
struct Project {
  ProjectConfig config;
  std::map<std::string, LoadedModule> modules;
  Symbol board;
};
struct ParsedSource {
  std::string text;
  std::shared_ptr<const Json> ast;
};
// Caller-owned cache. AST reuse always requires identical source text.
using ParsedSources = std::map<fs::path, ParsedSource>;

std::string source_text(const fs::path &path, const Sources &sources = {});
ProjectConfig read_config(fs::path path = ".", const Sources &sources = {});
Project load_project(fs::path path = ".", const Sources &sources = {},
                     ParsedSources *cache = nullptr,
                     const ErcExportLoader &erc_exports = {});
Json project_summary(const Project &project);
} // namespace noscheme
