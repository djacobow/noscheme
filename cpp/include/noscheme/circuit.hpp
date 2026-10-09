#pragma once
#include "noscheme/resolve.hpp"
#include <optional>
#include <variant>

namespace noscheme {
struct ExpansionLimits {
  std::size_t instances = 10000, leaves = 100000, hierarchy_depth = 128,
              type_depth = 128;
};
// An instance or net path: names and array indices.
using PathPart = std::variant<std::string, std::size_t>;
using InstancePath = std::vector<PathPart>;
// Dotted names with bracketed indices, such as leds[2].anode.
std::string path_text(const InstancePath &path);
Json to_json(const InstancePath &path);

enum class BindingState { omitted, connected, no_connect };
std::string_view binding_name(BindingState state);

// Role alternatives that an enclosing block's ERC choice selects.
struct RoleSelection {
  // The choice and its cases, with their source.
  Meta selection;
  // The block scope whose choices resolve the selection.
  InstancePath scope;
};
struct ErcSelections {
  // Keyed by role or external_role.
  std::map<std::string, RoleSelection> roles;
  Span span;
  std::string module;
};
// One expanded scalar: a physical pin, a port, or a net declaration.
struct Leaf {
  InstancePath path, scope;
  // pin, port, or net.
  std::string kind;
  // The electrical net type of ports and nets; pins have none.
  std::optional<std::string> type;
  // A user net type that refines the electrical type.
  std::optional<std::string> nettype;
  MetaFields metadata;
  std::optional<ErcSelections> selections;
  Span span;
  BindingState binding = BindingState::omitted;
  Span binding_span;
  // Index into Circuit::nets once connectivity is known.
  std::optional<std::size_t> net;
};
struct Part {
  InstancePath path;
  std::string identity, definition;
  Environment parameters;
  std::optional<Value> value;
  MetaFields metadata;
  Span declaration, source;
  // Physical pin names to leaves.
  std::map<std::string, std::size_t> pins;
};
struct CircuitNet {
  std::string name;
  InstancePath path;
  // Non-pin leaves, ordered by path text.
  std::vector<std::size_t> declarations;
  // Parts and physical pin names, in part order.
  std::vector<std::pair<std::size_t, std::string>> endpoints;
};
struct Block {
  InstancePath path;
  std::string definition, identity;
  Span declaration, source;
  // Port names to their leaves.
  std::map<std::string, std::vector<std::size_t>> ports;
};
struct ChoiceScope {
  InstancePath scope, parent_scope;
  MetaFields choices, bindings;
};
// Requires resolve_all output. The circuit refers to the project's ASTs, which
// must outlive it. This internal representation is not a CAD API.
struct Circuit {
  std::vector<std::string> erc_modules, erc_rule_sets;
  std::string board;
  Span source;
  std::vector<Leaf> leaves;
  // Ordered by path text.
  std::vector<Part> parts;
  std::vector<Block> blocks;
  // Ordered by name.
  std::vector<CircuitNet> nets;
  std::vector<ChoiceScope> choice_scopes;
  MetaFields configurations;
  Json warnings = Json::array();
};
Circuit elaborate_project(const Project &project,
                          const RegistryDescription &registry,
                          const Definitions &definitions,
                          ExpansionLimits limits = {},
                          Limits expression_limits = {});
// The comparison form shared with Python's circuit data, with metadata
// numbers in tagged encodings.
Json to_json(const Circuit &circuit);
} // namespace noscheme
