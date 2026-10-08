#pragma once
#include "noscheme/resolve.hpp"
namespace noscheme {
struct LspReference {
  Json span, target;
  std::string description;
  Json instance = nullptr;
  std::string pin{};
};
std::vector<LspReference> reference_index(const Project &project,
                                          const Definitions &definitions,
                                          const RegistryDescription &registry);
void elaborate_hover(std::vector<LspReference> &references,
                     const Project &project, const Json &circuit);
} // namespace noscheme
