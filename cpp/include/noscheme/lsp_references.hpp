#pragma once
#include "noscheme/circuit.hpp"
namespace noscheme {
// An empty target has no declaration, such as a builtin. Pin references
// record the instance declaration they belong to.
struct LspReference {
  Span span, target;
  std::string description;
  Span instance{};
  std::string pin{};
};
std::vector<LspReference> reference_index(const Project &project,
                                          const Definitions &definitions,
                                          const RegistryDescription &registry);
void elaborate_hover(std::vector<LspReference> &references,
                     const Project &project, const Circuit &circuit);
} // namespace noscheme
