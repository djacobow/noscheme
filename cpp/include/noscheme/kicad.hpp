#pragma once
#include "noscheme/json.hpp"
#include <set>
#include <string>
#include <utility>
#include <vector>
// KiCad export helpers shared by the netlist and schematic writers.
namespace noscheme::kicad {
std::string uuid5(const std::string &space, const std::string &identity);
// A metadata field's value, or `fallback` when absent.
Json attribute(const Json &item, const std::string &name,
               Json fallback = nullptr);
std::string part_value(const Json &part);
// The KiCad pin type of one pin record.
std::string pin_kind(const Json &pin);
// Path steps with array indices attached: [bank, 0, A] -> [bank[0], A].
std::vector<std::string> segments(const Json &path, size_t begin, size_t end);
std::string joined(const std::vector<std::string> &parts, const char *sep);
// Exported net name; `blocks` holds every block instance path.
std::string net_name(const Json &path, const std::set<Json> &blocks);
// {file name, text} for the root sheet `stem`.kicad_sch, its sub-sheets, and
// the noscheme symbol library; the same files as the Python exporter.
std::vector<std::pair<std::string, std::string>>
render_schematic(const Json &circuit, const Json &state,
                 const std::string &project, const std::string &stem);
} // namespace noscheme::kicad
