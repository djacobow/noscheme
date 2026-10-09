#pragma once
#include "noscheme/lsp_completion.hpp"
namespace noscheme {
Json instance_actions(const Source &source, std::size_t offset,
                      const std::vector<CompletionEntry> &catalog);
}
