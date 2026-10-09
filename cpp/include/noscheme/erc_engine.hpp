#pragma once
// Internal to the native ERC: configuration views, rule contexts, and the
// standard rule families. The scheduler in erc.cpp owns applications and
// waivers; Python plugins run through the bridge.
#include "noscheme/circuit.hpp"
#include <boost/multiprecision/cpp_int.hpp>
#include <functional>

namespace noscheme::erc {
using Rational = boost::multiprecision::cpp_rational;

// Python's str() of a number, and repr() of a string.
std::string python_str(const Value &number);
std::string python_repr(const std::string &text);
// Python's Fraction(str(number)): floats use their shortest decimal spelling.
Rational decimal(const Value &number);
std::optional<Value> number(const MetaValue *value);

struct Diagnostic {
  std::string code, severity, message;
  Json span;
  std::vector<std::pair<std::string, Json>> related{};
  std::optional<std::string> instance_path{}, configuration{}, application{};
  Json json() const;
};
// Raised by a failed rule or application; reported as erc.plugin.
struct Failure : std::runtime_error {
  using std::runtime_error::runtime_error;
};

// One configuration of a circuit: selected role alternatives replace the
// alternatives of records with ERC selections.
class View {
  std::map<std::size_t, std::map<std::string, MetaValue>> selected;

public:
  const Circuit &circuit;
  std::vector<std::string> part_labels;
  // For pin leaves, the part and the physical pin name.
  std::vector<std::optional<std::pair<std::size_t, std::string>>> pins;
  // Per net: physical pins in part order, then declarations with an
  // external role.
  std::vector<std::vector<std::size_t>> endpoints;
  std::vector<std::size_t> origins;
  View(const Circuit &circuit,
       std::map<std::size_t, std::map<std::string, MetaValue>> selected);
  const Leaf &leaf(std::size_t index) const { return circuit.leaves[index]; }
  // A pin's component.pin label, or a declaration's path.
  std::string label(std::size_t leaf) const;
  const MetaValue *attribute(std::size_t leaf, const std::string &key) const;
  // external_role, else role, else none.
  std::vector<std::string> roles(std::size_t leaf) const;
  bool physical(std::size_t leaf) const { return pins[leaf].has_value(); }
  // binding_span when present, else span: where a record is reported.
  Json record_span(std::size_t leaf) const;
};

// Selector terms: ("role", r), ("field", name, value), ("predicate", name).
// Predicates and rule results are three-valued: true, false, or unknown.
using Tri = std::optional<bool>;
struct Context;
using Predicate = std::function<Tri(std::size_t pin, const Context &)>;

// Values shared by the rules of one configuration.
struct Memo {
  std::map<std::string, std::vector<Diagnostic>> results;
  std::map<std::string, std::shared_ptr<const void>> facts;
};
struct Context {
  const View &view;
  std::optional<std::size_t> net, pin;
  std::size_t owner;
  std::string application, checker;
  Json span;
  std::string missing = "warning";
  Memo &memo;
  // Field domains: pin and net sign constraints, and quantities by field.
  const Json &domains;
  const std::function<Tri(const std::string &, std::size_t pin,
                          const Context &)> &predicate;
  const std::vector<std::size_t> &pins() const;
  Diagnostic
  diagnostic(const std::string &code, const std::string &message,
             std::optional<Json> span = {}, std::string severity = "error",
             std::vector<std::pair<std::string, Json>> related = {}) const;
  Diagnostic
  error(const std::string &message,
        std::vector<std::pair<std::string, Json>> related = {}) const;
  Diagnostic
  unknown(const std::string &message,
          std::vector<std::pair<std::string, Json>> related = {}) const;
};
using Params = std::map<std::string, MetaValue>;
using Rule =
    std::function<std::vector<Diagnostic>(const Context &, const Params &)>;
struct StandardRule {
  Rule run;
  // Parameter defaults; other parameters are required.
  Params defaults;
};
// Native implementations of every std rule, predicate, and rule set.
const std::map<std::string, StandardRule> &standard_rules();
const std::map<std::string, Predicate> &standard_predicates();
// std.default: (application name, checker) pairs for a net or pin context.
std::vector<std::pair<std::string, std::string>>
standard_applications(const Context &context);
} // namespace noscheme::erc
