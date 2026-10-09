#pragma once
#include "noscheme/source.hpp"
#include <unordered_map>
namespace noscheme {
struct DiagnosticsFailure : std::runtime_error {
  Json diagnostics;
  int exit_code;
  explicit DiagnosticsFailure(Json diagnostics, int exit_code = 1)
      : runtime_error("diagnostics emitted"),
        diagnostics(std::move(diagnostics)), exit_code(exit_code) {}
};
Json validate_structure(const ast::Module &module);
// Order-preserving diagnostic list that skips exact duplicates in linear time.
class UniqueDiagnostics {
  Json items_ = Json::array();
  std::unordered_multimap<std::size_t, std::size_t> index_;

public:
  void add(const Json &diagnostic);
  void add_all(const Json &diagnostics) {
    for (const auto &d : diagnostics)
      add(d);
  }
  const Json &items() const { return items_; }
  Json take() { return std::exchange(items_, Json::array()); }
};
} // namespace noscheme
