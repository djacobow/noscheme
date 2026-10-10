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
// Structure that does not depend on other modules; for an extension, the
// checks that depend on its kind wait for kind_errors.
Json validate_structure(const ast::Module &module);
Json kind_errors(const ast::Definition &definition);
Json field_errors(const std::vector<const ast::Field *> &fields);
// One error diagnostic, with the previous declaration as related information.
Json error_json(std::string code, std::string message, const Span &span,
                const Span *previous = nullptr);
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
