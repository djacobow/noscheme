#pragma once
#include "noscheme/lsp_references.hpp"
#include <optional>
namespace noscheme {
struct RenameContext {
  Project project;
  RegistryDescription registry;
  Definitions definitions;
  Json circuit;
};
// Source-only workspace edits. No CAD artifacts or identity state are written.
Json rename_plan(const RenameContext &context, const Sources &sources,
                 const std::vector<LspReference> &references,
                 const fs::path &path, std::size_t offset,
                 const std::optional<std::string> &new_name);
} // namespace noscheme
