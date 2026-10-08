#pragma once
#include "noscheme/source.hpp"
namespace noscheme {
struct DiagnosticsFailure : std::runtime_error {
  Json diagnostics;
  int exit_code;
  explicit DiagnosticsFailure(Json diagnostics, int exit_code = 1)
      : runtime_error("diagnostics emitted"),
        diagnostics(std::move(diagnostics)), exit_code(exit_code) {}
};
Json validate_structure(const Json &module);
} // namespace noscheme
