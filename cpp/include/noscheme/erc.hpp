#pragma once
#include "noscheme/cancellation.hpp"
#include "noscheme/circuit.hpp"
#include "noscheme/registry.hpp"
namespace noscheme {
struct ErcResult {
  Json report;
  std::string json_report, text_report;
};
// Standard rules, predicates, and rule sets run natively. Plugin rules,
// predicates, and rule sets run in one Python bridge request, made only when
// the circuit uses them. Does not mutate the circuit or write reports.
// Ordinary ERC errors remain in the returned report; configuration and plugin
// failures throw source-located diagnostics.
ErcResult run_erc(const ProjectConfig &config,
                  const RegistryDescription &registry, const Circuit &circuit,
                  const fs::path &python, const Json &configurations = nullptr,
                  std::chrono::milliseconds timeout = std::chrono::seconds(30),
                  std::stop_token stop = {});
} // namespace noscheme
