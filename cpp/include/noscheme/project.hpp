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
  std::string language_version, board_module, board_name, target;
  fs::path state_path() const;
  std::vector<fs::path> source_paths, library_paths, erc_modules;
  std::vector<std::string> erc_rule_sets;
  // The configuration text, which keeps span valid.
  std::shared_ptr<const Source> source;
  Span span;
  std::vector<fs::path> search_paths() const;
};
struct Symbol {
  std::string module, name;
  // The module's shared immutable AST owns this declaration.
  const ast::Definition *declaration = nullptr;
};
using Symbols = std::map<std::string, Symbol>;
// Whether a dotted spelling is a builtin net value such as net.power.
bool builtin_net(const Symbols &symbols, const std::string &spelling);
// Whether a declared type is a net type: a builtin net value, bundle, or net
// type.
bool net_type(const Symbols &symbols, const std::string &spelling);
// The loader's last step for a module: extensions take their bases' kinds and
// declarations become Nets or Instances; the module's own symbols are rebound.
// Lenient classification skips what it cannot classify, for navigation.
std::shared_ptr<const ast::Module> classified_module(const std::string &module,
                                                     const ast::Module &tree,
                                                     Symbols &symbols,
                                                     bool lenient = false);
using ErcExports = std::map<std::string, Symbols>;
using ErcExportLoader = std::function<ErcExports(const ProjectConfig &)>;
struct LoadedModule {
  std::string name;
  fs::path path;
  std::shared_ptr<const ast::Module> ast;
  Symbols symbols;
};
struct Project {
  ProjectConfig config;
  std::map<std::string, LoadedModule> modules;
  Symbol board;
};
struct ParsedSource {
  std::string text;
  std::shared_ptr<const ast::Module> ast;
};
// Caller-owned cache. AST reuse always requires identical source text.
using ParsedSources = std::map<fs::path, ParsedSource>;

std::string source_text(const fs::path &path, const Sources &sources = {});
ProjectConfig read_config(fs::path path = ".", const Sources &sources = {},
                          const std::string &target = "");
Project load_project(fs::path path = ".", const Sources &sources = {},
                     ParsedSources *cache = nullptr,
                     const ErcExportLoader &erc_exports = {},
                     const std::string &target = "");
Json project_summary(const Project &project);
} // namespace noscheme
