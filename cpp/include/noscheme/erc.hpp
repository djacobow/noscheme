#pragma once
#include "noscheme/cancellation.hpp"
#include "noscheme/registry.hpp"
namespace noscheme {
struct ErcResult {
  Json report;
  std::string json_report, text_report;
};
// Does not mutate the circuit or write reports. Ordinary ERC errors remain in
// the returned report; protocol/plugin failures throw source-located
// diagnostics.
ErcResult run_erc(const ProjectConfig &config,
                  const RegistryDescription &registry, const Json &circuit,
                  const fs::path &python, const Json &configurations = nullptr,
                  std::chrono::milliseconds timeout = std::chrono::seconds(30),
                  std::stop_token stop = {});
} // namespace noscheme
