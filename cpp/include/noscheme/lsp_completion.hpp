#pragma once
#include "noscheme/resolve.hpp"
#include <algorithm>
namespace noscheme {
struct CompletionEntry {
  std::string name, description;
  int kind;
  std::vector<Span> shadowed_in{};
  // Empty when visible everywhere in the file.
  Span visible_in{};
  std::string insert_text{};
  Json rule = nullptr;
  Json instance = nullptr;
  std::shared_ptr<const std::vector<CompletionEntry>> candidates{};
  std::shared_ptr<const std::vector<CompletionEntry>> targets{};
  // A local declaration hides this name at the offset.
  bool shadowed(std::size_t offset) const {
    return std::ranges::any_of(shadowed_in, [&](const Span &span) {
      return offset >= span.start && offset < span.end;
    });
  }
};
using CompletionCatalog = std::map<fs::path, std::vector<CompletionEntry>>;
std::string code_mask(const std::string &text, bool preserve_strings = false);
std::string completion_recovery(const std::string &text,
                                std::vector<std::size_t> *offsets = nullptr,
                                bool recover_calls = false);
std::string builtin_description(const Symbols &symbols,
                                const RegistryDescription &registry,
                                const std::string &name);
CompletionCatalog completion_catalog(const Project &project,
                                     const RegistryDescription &registry);
void interface_completions(CompletionCatalog &catalog, const Project &project,
                           const RegistryDescription &registry,
                           const Definitions &definitions);
struct CompletionResult {
  std::size_t start, end;
  std::vector<CompletionEntry> entries;
};
CompletionResult keyword_completion(const std::string &text,
                                    std::size_t offset);
CompletionResult complete(const std::string &text, std::size_t offset,
                          const std::vector<CompletionEntry> &catalog);
} // namespace noscheme
