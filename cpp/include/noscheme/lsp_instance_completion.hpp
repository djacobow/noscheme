#pragma once
#include "noscheme/lsp_erc_completion.hpp"
#include <set>
namespace noscheme {
std::vector<TextRepair> section_repairs(const std::string &text);
std::optional<CompletionResult>
section_completion(const std::string &text, std::size_t offset,
                   const std::vector<CompletionEntry> &catalog);
CompletionResult
connection_choices(const std::string &text, std::size_t offset, std::size_t end,
                   const std::string &fragment, const Json &context, bool pins,
                   bool parameters, bool target_choices,
                   const std::set<std::string> &bound = {},
                   const std::set<std::string> &params = {},
                   bool section = false, bool grouped = false,
                   const std::vector<CompletionEntry> *targets = nullptr);
std::vector<TextRepair> instance_repairs(const std::string &text);
std::optional<CompletionResult>
instance_completion(const std::string &text, std::size_t offset,
                    const std::vector<CompletionEntry> &catalog);
} // namespace noscheme
