#pragma once
#include "noscheme/erc.hpp"
namespace noscheme {
// Prepare every artifact before the shared CLI publisher writes anything.
// Ordered [path, text] pairs commit persistent identity state last.
struct ExportResult {
  Json summary, warnings, files;
};
ExportResult export_bom(const Project &project, const Circuit &circuit,
                        Json state);
ExportResult export_kicad(const Project &project, const Circuit &circuit,
                          const ErcResult &erc, Json state,
                          const Json &footprints, const fs::path &records,
                          const std::string &records_text);
} // namespace noscheme
