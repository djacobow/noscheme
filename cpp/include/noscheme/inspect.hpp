#pragma once
#include "noscheme/resolve.hpp"
namespace noscheme {
Json evaluate_templates(const Project &project, const Definitions &definitions);
Json unused_parameters(const Definitions &definitions);
} // namespace noscheme
