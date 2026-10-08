#pragma once
#include "noscheme/lsp_completion.hpp"
#include <optional>
namespace noscheme {
struct TextRepair {
  std::size_t start, end;
  std::string text;
};
struct ArgumentRange {
  std::size_t end;
  bool closed;
};
ArgumentRange argument_range(const std::string &text, const std::string &mask,
                             std::size_t opening, std::size_t indent);
std::vector<TextRepair> erc_call_repairs(const std::string &text);
std::optional<CompletionResult>
erc_completion(const std::string &text, std::size_t offset,
               const std::vector<CompletionEntry> &catalog);
} // namespace noscheme
