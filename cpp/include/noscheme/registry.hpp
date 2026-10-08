#pragma once
#include "noscheme/cancellation.hpp"
#include "noscheme/project.hpp"
#include <chrono>

namespace noscheme {
// Data-only snapshot. Callback execution and default objects stay in Python.
// Ordered field/parameter arrays preserve positional rule argument semantics.
struct RegistryDescription {
  Json data;
  ErcExports exports() const;
};
RegistryDescription registry_description(const Json &response,
                                         const Json &span);
// Linux-first subprocess boundary. Uses argv, never shell command
// interpolation. One process per snapshot prevents stale plugin registrations;
// the future LSP owns snapshot reuse and invalidation. The child inherits
// stderr for plugin logs.
RegistryDescription
load_registry(const ProjectConfig &config, const fs::path &python,
              std::chrono::milliseconds timeout = std::chrono::seconds(30),
              std::stop_token stop = {});
} // namespace noscheme
