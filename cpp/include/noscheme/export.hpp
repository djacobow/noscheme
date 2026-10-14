#pragma once
#include "noscheme/erc.hpp"
namespace noscheme {
// Prepare every artifact before the shared CLI publisher writes anything.
// Ordered [path, text] pairs commit persistent identity state last.
struct ExportResult {
  Json summary, warnings, files;
};
// Warnings are the caller's identity-state warnings; exports add their own.
ExportResult export_bom(const Project &project, const Circuit &circuit,
                        Json state, Json warnings = Json::array());
// With `schematic`, also the KiCad sheets and symbol library beside that
// root sheet; the caller adds sym-lib-table and removes stale sheets.
ExportResult export_kicad(const Project &project, const Circuit &circuit,
                          const ErcResult &erc, Json state,
                          const Json &footprints, const fs::path &records,
                          const std::string &records_text,
                          Json warnings = Json::array(),
                          const fs::path &schematic = {});
} // namespace noscheme
