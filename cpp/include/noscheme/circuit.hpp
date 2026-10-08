#pragma once
#include "noscheme/resolve.hpp"

namespace noscheme {
struct ExpansionLimits {
  std::size_t instances = 10000, leaves = 100000, hierarchy_depth = 128,
              type_depth = 128;
};
// Requires resolve_all output. Metadata quantities use the same lossless number
// encoding as the metadata probe; this internal representation is not a CAD
// API.
Json elaborate_project(const Project &project,
                       const RegistryDescription &registry,
                       const Definitions &definitions,
                       ExpansionLimits limits = {},
                       Limits expression_limits = {});
} // namespace noscheme
