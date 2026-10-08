#pragma once
#include "noscheme/source.hpp"
namespace noscheme {
// Canonical two-space layout, preserving spelling, comments, and source form.
std::string format_source(const Source &source);
} // namespace noscheme
