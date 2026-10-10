// Native standard ERC rules. Each family follows the corresponding Python
// module (erc_connections, erc_drivers, erc_compatibility, erc_ports,
// erc_current, erc_voltage, erc_electrical, erc_primitives); messages and
// diagnostic order must stay identical for report parity.
#include "noscheme/erc_engine.hpp"
#include <algorithm>
#include <charconv>
#include <cmath>

namespace noscheme::erc {
namespace {
using Related = std::vector<std::pair<std::string, Json>>;
using Strings = std::set<std::string>;
// Python float repr: the shortest round-trip digits, in fixed notation from
// 1e-4 up to 1e16 and exponent notation otherwise.
std::string float_repr(double value) {
  if (std::isnan(value))
    return "nan";
  if (std::isinf(value))
    return value < 0 ? "-inf" : "inf";
  char buffer[64];
  auto converted = std::to_chars(buffer, buffer + sizeof(buffer), value,
                                 std::chars_format::scientific);
  std::string text(buffer, converted.ptr);
  auto e = text.find('e');
  int exponent = std::stoi(text.substr(e + 1));
  std::string digits = text.substr(0, e), sign;
  if (digits.starts_with('-')) {
    sign = "-";
    digits.erase(0, 1);
  }
  if (auto dot = digits.find('.'); dot != std::string::npos)
    digits.erase(dot, 1);
  if (exponent < -4 || exponent >= 16) {
    auto mantissa = digits.substr(0, 1);
    if (digits.size() > 1)
      mantissa += "." + digits.substr(1);
    auto power = std::to_string(std::abs(exponent));
    if (power.size() < 2)
      power.insert(0, "0");
    return sign + mantissa + "e" + (exponent < 0 ? "-" : "+") + power;
  }
  const int point = exponent + 1;
  std::string result;
  if (point <= 0)
    result = "0." + std::string(-point, '0') + digits;
  else if (point >= static_cast<int>(digits.size()))
    result = digits + std::string(point - digits.size(), '0') + ".0";
  else
    result = digits.substr(0, point) + "." + digits.substr(point);
  return sign + result;
}
} // namespace
std::string python_str(const Value &number) {
  if (auto integer = std::get_if<Integer>(&number))
    return std::to_string(*integer);
  if (auto floating = std::get_if<double>(&number))
    return float_repr(*floating);
  return std::get<std::string>(number);
}
std::string python_repr(const std::string &text) {
  char quote = text.find('\'') != std::string::npos &&
                       text.find('"') == std::string::npos
                   ? '"'
                   : '\'';
  std::string result(1, quote);
  for (unsigned char c : text) {
    if (c == '\\' || c == static_cast<unsigned char>(quote)) {
      result += '\\';
      result += static_cast<char>(c);
    } else if (c == '\n')
      result += "\\n";
    else if (c == '\r')
      result += "\\r";
    else if (c == '\t')
      result += "\\t";
    else if (c < 0x20 || c == 0x7f) {
      constexpr char hex[] = "0123456789abcdef";
      result += "\\x";
      result += hex[c >> 4];
      result += hex[c & 15];
    } else
      result += static_cast<char>(c);
  }
  return result + quote;
}
double floating(const Value &number) {
  if (auto integer = std::get_if<Integer>(&number))
    return static_cast<double>(*integer);
  return std::get<double>(number);
}
std::optional<Value> number(const MetaValue *value) {
  if (!value || !value->number())
    return {};
  return to_value(*value);
}
Json Diagnostic::json() const {
  auto notes = Json::array();
  for (const auto &[text, where] : related)
    notes.push_back(object("message", text, "span", where));
  auto optional = [](const std::optional<std::string> &text) {
    return text ? Json(*text) : Json(nullptr);
  };
  return object("code", code, "severity", severity, "message", message, "span",
                span, "related", std::move(notes), "instance_path",
                optional(instance_path), "configuration",
                optional(configuration), "application", optional(application));
}

View::View(const Circuit &circuit,
           std::map<std::size_t, std::map<std::string, MetaValue>> selected)
    : selected(std::move(selected)), circuit(circuit),
      pins(circuit.leaves.size()) {
  for (std::size_t p = 0; p < circuit.parts.size(); ++p) {
    part_labels.push_back(path_text(circuit.parts[p].path));
    for (const auto &[name, leaf] : circuit.parts[p].pins)
      pins[leaf] = std::make_pair(p, name);
  }
  for (const auto &net : circuit.nets) {
    std::vector<std::size_t> members;
    for (const auto &[part, pin] : net.endpoints)
      members.push_back(circuit.parts[part].pins.at(pin));
    for (auto declaration : net.declarations)
      if (attribute(declaration, "external_role"))
        members.push_back(declaration);
    endpoints.push_back(std::move(members));
    origins.push_back(*std::ranges::find_if(net.declarations, [&](auto i) {
      return circuit.leaves[i].path == net.path;
    }));
  }
}
std::string View::label(std::size_t leaf) const {
  if (const auto &pin = pins[leaf])
    return part_labels[pin->first] + "." + pin->second;
  return path_text(circuit.leaves[leaf].path);
}
const MetaValue *View::attribute(std::size_t leaf,
                                 const std::string &key) const {
  if (auto found = selected.find(leaf); found != selected.end())
    if (auto role = found->second.find(key); role != found->second.end())
      return &role->second;
  return meta_value(circuit.leaves[leaf].metadata, key);
}
std::vector<std::string> View::roles(std::size_t leaf) const {
  auto *roles = attribute(leaf, "external_role");
  if (!roles)
    roles = attribute(leaf, "role");
  return roles ? meta_strings(*roles) : std::vector<std::string>{};
}
Json View::record_span(std::size_t leaf) const {
  const auto &record = circuit.leaves[leaf];
  return record.binding_span ? record.binding_span.json() : record.span.json();
}

const std::vector<std::size_t> &Context::pins() const {
  static const std::vector<std::size_t> none;
  return net ? view.endpoints[*net] : none;
}
Diagnostic Context::diagnostic(const std::string &code,
                               const std::string &message,
                               std::optional<Json> where, std::string severity,
                               Related related) const {
  return {"erc." + code,
          std::move(severity),
          message,
          where ? std::move(*where) : span,
          std::move(related),
          pin ? std::optional(view.label(*pin)) : std::nullopt,
          std::nullopt,
          application};
}
Diagnostic Context::error(const std::string &message, Related related) const {
  return diagnostic(checker, message, {}, "error", std::move(related));
}
Diagnostic Context::unknown(const std::string &message, Related related) const {
  return diagnostic(checker, message, {}, "unknown", std::move(related));
}

namespace {
// Diagnostics of a family, before scheduling assigns their application.
struct Results {
  std::vector<Diagnostic> diagnostics;
  void emit(const std::string &code, const std::string &message, Json span,
            std::optional<std::string> path = {},
            std::string severity = "error", Related related = {}) {
    diagnostics.push_back({"erc." + code, std::move(severity), message,
                           std::move(span), std::move(related),
                           std::move(path)});
  }
};
// Missing bindings are actionable at the instance, not its reusable interface.
void connection_issue(const View &view, Results &results,
                      const std::string &code, const std::string &message,
                      std::size_t leaf) {
  const auto &record = view.leaf(leaf);
  auto span = view.record_span(leaf);
  Related related;
  if (!record.binding_span) {
    Span declaration;
    if (auto pin = view.pins[leaf])
      declaration = view.circuit.parts[pin->first].declaration;
    else
      for (const auto &block : view.circuit.blocks)
        if (block.path == record.scope) {
          declaration = block.declaration;
          break;
        }
    if (declaration) {
      span = declaration.json();
      related.emplace_back("interface declared here", record.span.json());
    }
  }
  results.emit(code, message, span, view.label(leaf), "error",
               std::move(related));
}
template <class T, class F>
const T &shared(const Context &ctx, const std::string &key, F &&calculate) {
  auto &slot = ctx.memo.facts[key];
  if (!slot)
    slot = std::make_shared<const T>(calculate());
  return *static_cast<const T *>(slot.get());
}
bool contains(const std::vector<std::string> &values,
              const std::string &value) {
  return std::ranges::find(values, value) != values.end();
}
bool single(const std::vector<std::string> &roles, const std::string &role) {
  return roles.size() == 1 && roles.front() == role;
}
const std::string *text(const MetaValue *value) {
  return value ? value->get_if<std::string>() : nullptr;
}
std::string joined(const std::vector<std::string> &values) {
  std::string result;
  for (const auto &value : values)
    result += (result.empty() ? "" : ", ") + value;
  return result;
}
std::string joined(const Strings &values) {
  return joined(std::vector<std::string>(values.begin(), values.end()));
}

const Strings PASSIVE{"passive"};
const Strings DIGITAL{"digital_input", "digital_output",
                      "digital_bidirectional", "open_drain", "open_source"};
const Strings ANALOG{"analog_input", "analog_output", "analog_bidirectional"};
const Strings INPUTS{"digital_input", "analog_input", "power_input"};
Strings unite(std::initializer_list<Strings> sets) {
  Strings result;
  for (const auto &set : sets)
    result.insert(set.begin(), set.end());
  return result;
}
const std::map<std::string, Strings> &allowed_roles() {
  static const std::map<std::string, Strings> roles{
      {"tristate", unite({PASSIVE, DIGITAL})},
      {"passive", unite({PASSIVE,
                         DIGITAL,
                         ANALOG,
                         {"power_input", "power_output", "ground"}})},
      {"digital", unite({PASSIVE, DIGITAL})},
      {"bidirectional", unite({PASSIVE, DIGITAL})},
      {"open_drain", unite({PASSIVE, {"digital_input", "open_drain"}})},
      {"open_source", unite({PASSIVE, {"digital_input", "open_source"}})},
      {"analog", unite({PASSIVE, ANALOG})},
      {"power", unite({PASSIVE, INPUTS, {"power_output"}})},
      {"ground", unite({PASSIVE, {"digital_input", "analog_input", "ground"}})},
  };
  return roles;
}
const std::map<std::string, Strings> &port_allowed() {
  static const std::map<std::string, Strings> roles{
      {"passive", allowed_roles().at("passive")},
      {"digital_input", unite({PASSIVE, {"digital_input"}})},
      {"digital_output", unite({PASSIVE, {"digital_input", "digital_output"}})},
      {"digital_bidirectional", unite({PASSIVE, DIGITAL})},
      {"analog_input", unite({PASSIVE, {"analog_input"}})},
      {"analog_output", unite({PASSIVE, {"analog_input", "analog_output"}})},
      {"analog_bidirectional", unite({PASSIVE, ANALOG})},
      {"power_input", unite({PASSIVE, {"power_input"}})},
      {"power_output", unite({PASSIVE, {"power_input", "power_output"}})},
      {"ground", allowed_roles().at("ground")},
      {"open_drain", unite({PASSIVE, {"digital_input", "open_drain"}})},
      {"open_source", unite({PASSIVE, {"digital_input", "open_source"}})},
      {"unchecked", allowed_roles().at("passive")},
      {"do_not_connect", {}},
  };
  return roles;
}
const std::map<std::string, Strings> &port_evidence() {
  static const std::map<std::string, Strings> evidence{
      {"digital_input", {"digital_input"}},
      {"digital_output", {"digital_output"}},
      {"digital_bidirectional", {"digital_bidirectional"}},
      {"analog_input", {"analog_input"}},
      {"analog_output", {"analog_output"}},
      {"analog_bidirectional", {"analog_bidirectional"}},
      {"power_input", {"power_input"}},
      {"power_output", {"power_output"}},
      {"ground", {"ground"}},
      {"open_drain", {"open_drain"}},
      {"open_source", {"open_source"}},
  };
  return evidence;
}
bool intersects(const Strings &a, const Strings &b) {
  return std::ranges::any_of(a, [&](const auto &x) { return b.contains(x); });
}
bool subset(const Strings &a, const Strings &b) {
  return std::ranges::includes(b, a);
}

// erc_facts: endpoint roles and the constraints of a flattened net.
struct Known {
  std::string role, label;
  std::size_t pin;
};
struct NetFacts {
  std::size_t origin;
  std::vector<std::size_t> constraints;
  Strings types;
  std::vector<Known> known;
  std::vector<std::pair<std::string, std::size_t>> uncertain;
};
const NetFacts &net_facts(const Context &ctx) {
  const auto &view = ctx.view;
  return shared<NetFacts>(ctx, "facts:" + std::to_string(*ctx.net), [&] {
    NetFacts facts;
    facts.origin = view.origins[*ctx.net];
    const auto &origin = view.leaf(facts.origin);
    auto fixed_input_port = [&](std::size_t index) {
      const auto &declaration = view.leaf(index);
      auto *role = view.attribute(index, "role");
      return (origin.type == "power" || origin.type == "ground") &&
             declaration.kind == "port" &&
             (declaration.type == "digital" ||
              declaration.type == "bidirectional" ||
              declaration.type == "open_drain") &&
             role && single(meta_strings(*role), "digital_input") &&
             role->is<MetaList>();
    };
    for (auto index : view.circuit.nets[*ctx.net].declarations)
      if (view.leaf(index).type != "unchecked" && !fixed_input_port(index)) {
        facts.constraints.push_back(index);
        facts.types.insert(view.leaf(index).type.value_or(""));
      }
    for (auto pin : ctx.pins()) {
      auto roles = view.roles(pin);
      auto label = view.label(pin);
      if (roles.size() != 1 || roles[0] == "unchecked" ||
          roles[0] == "do_not_connect")
        facts.uncertain.emplace_back(label, pin);
      else
        facts.known.push_back({roles[0], label, pin});
    }
    return facts;
  });
}
Strings allowed(const View &view, std::size_t declaration, std::size_t pin) {
  const auto &type = view.leaf(declaration).type.value_or("");
  if (type == "tristate") {
    auto *capable = view.attribute(pin, "tristate_capable");
    if (!(capable && capable->is<bool>() && *capable->get_if<bool>()))
      return {"passive", "digital_input"};
  }
  return allowed_roles().at(type);
}
struct Conflict {
  std::string label;
  std::size_t pin;
  Strings roles;
  std::vector<std::size_t> incompatible;
  bool capability;
};
struct Compatibility {
  std::vector<Conflict> conflicts;
  bool uncertain = false;
};
const Compatibility &compatibility(const Context &ctx) {
  const auto &view = ctx.view;
  return shared<Compatibility>(
      ctx, "compatibility:" + std::to_string(*ctx.net), [&] {
        const auto &constraints = net_facts(ctx).constraints;
        Compatibility result;
        for (auto pin : ctx.pins()) {
          auto list = view.roles(pin);
          Strings roles(list.begin(), list.end());
          if (roles.empty() || roles.contains("unchecked")) {
            result.uncertain = true;
            continue;
          }
          if (roles == Strings{"do_not_connect"})
            continue;
          auto permitted = roles;
          std::vector<std::size_t> incompatible;
          for (auto declaration : constraints) {
            auto domain = allowed(view, declaration, pin);
            std::erase_if(permitted,
                          [&](const auto &r) { return !domain.contains(r); });
            if (!intersects(roles, domain))
              incompatible.push_back(declaration);
          }
          if (permitted.empty() && incompatible.empty())
            incompatible = constraints;
          if (!incompatible.empty()) {
            bool capability = std::ranges::any_of(incompatible, [&](auto d) {
              return view.leaf(d).type == "tristate" &&
                     subset(roles, allowed_roles().at("tristate"));
            });
            result.conflicts.push_back(
                {view.label(pin), pin, roles, incompatible, capability});
          } else if (std::ranges::any_of(constraints, [&](auto d) {
                       return !subset(roles, allowed(view, d, pin));
                     }))
            result.uncertain = true;
        }
        return result;
      });
}
std::string attribute_text(const View &view, std::size_t leaf,
                           const std::string &key,
                           const std::string &fallback) {
  auto *value = text(view.attribute(leaf, key));
  return value ? *value : fallback;
}
bool attribute_true(const View &view, std::size_t leaf,
                    const std::string &key) {
  auto *value = view.attribute(leaf, key);
  return value && value->is<bool>() && *value->get_if<bool>();
}
// Python compares a role attribute tuple with ("do_not_connect",).
bool exactly(const View &view, std::size_t leaf, const std::string &key,
             const std::string &role) {
  auto *value = view.attribute(leaf, key);
  return value && value->is<MetaList>() && single(meta_strings(*value), role);
}

// erc_connections
std::vector<Diagnostic> connection_results(const Context &ctx) {
  const auto &view = ctx.view;
  Results results;
  const auto &facts = net_facts(ctx);
  const auto &name = view.circuit.nets[*ctx.net].name;
  const auto &endpoints = ctx.pins();
  for (auto pin : endpoints)
    if (view.leaf(pin).kind != "port" && endpoints.size() < 2 &&
        attribute_text(view, pin, "unused", "explicit") != "always" &&
        !exactly(view, pin, "role", "do_not_connect"))
      connection_issue(view, results, "required_connection",
                       view.label(pin) + " on " + python_repr(name) +
                           " needs another physical endpoint",
                       pin);
  if (endpoints.empty() &&
      std::ranges::none_of(
          view.circuit.nets[*ctx.net].declarations,
          [&](auto d) { return attribute_true(view, d, "placeholder"); }))
    results.emit("unconnected_net",
                 "net " + python_repr(name) + " has no physical endpoints",
                 view.record_span(facts.origin));
  return results.diagnostics;
}
std::vector<Diagnostic> pin_results(const Context &ctx) {
  const auto &view = ctx.view;
  Results results;
  auto pin = *ctx.pin;
  const auto &record = view.leaf(pin);
  auto label = view.label(pin);
  auto policy = attribute_text(view, pin, "unused", "explicit");
  auto span = view.record_span(pin);
  if (exactly(view, pin, "role", "do_not_connect")) {
    if (policy == "never")
      results.emit("required_connection",
                   label + ": unused.never conflicts with role.do_not_connect",
                   span, label);
    if (record.net)
      results.emit("do_not_connect", label + " must not be assigned to any net",
                   span, label);
    return results.diagnostics;
  }
  if (record.binding == BindingState::omitted && policy == "explicit")
    connection_issue(view, results, "unused_pin",
                     label + " must connect or explicitly bind to no_connect",
                     pin);
  else if (!record.net && policy == "never")
    connection_issue(view, results, "required_connection",
                     label + " must connect; no_connect cannot override "
                             "unused.never",
                     pin);
  return results.diagnostics;
}

// erc_drivers
std::vector<Diagnostic> driver_results(const Context &ctx) {
  const auto &view = ctx.view;
  Results results;
  const auto &facts = net_facts(ctx);
  const auto &origin = view.leaf(facts.origin);
  auto origin_span = view.record_span(facts.origin);
  const auto &name = view.circuit.nets[*ctx.net].name;
  if (origin.type == "unchecked") {
    return results.diagnostics;
  }
  const auto &known = facts.known;
  const auto &types = facts.types;
  bool compatibility_uncertain = compatibility(ctx).uncertain;
  std::vector<Known> drivers, open_drivers;
  for (const auto &e : known) {
    if (e.role == "digital_output" || e.role == "analog_output" ||
        e.role == "power_output")
      drivers.push_back(e);
    if (e.role == "open_drain" || e.role == "open_source")
      open_drivers.push_back(e);
  }
  // A digital declaration still imposes its ordinary single-driver policy.
  if (types.contains("tristate") && !types.contains("digital")) {
    std::erase_if(drivers, [&](const Known &e) {
      return e.role == "digital_output" &&
             attribute_true(view, e.pin, "tristate_capable");
    });
    std::erase_if(open_drivers, [&](const Known &e) {
      return attribute_true(view, e.pin, "tristate_capable");
    });
  }
  Strings open_roles;
  for (const auto &e : open_drivers)
    open_roles.insert(e.role);
  if (drivers.size() > 1 || (!drivers.empty() && !open_drivers.empty()) ||
      open_roles.size() > 1) {
    auto conflicting = drivers;
    conflicting.insert(conflicting.end(), open_drivers.begin(),
                       open_drivers.end());
    std::vector<std::string> labels;
    Related related;
    for (const auto &e : conflicting) {
      labels.push_back(e.label);
      related.emplace_back(e.label, view.record_span(e.pin));
    }
    results.emit("multiple_drivers",
                 "net " + python_repr(name) +
                     " has multiple active output drivers: " + joined(labels),
                 origin_span, {}, "error", std::move(related));
  }
  bool source = std::ranges::any_of(
      known, [](const Known &e) { return e.role == "power_output"; });
  bool ground = types.contains("ground") ||
                std::ranges::any_of(
                    known, [](const Known &e) { return e.role == "ground"; });
  if (source && ground)
    results.emit("power_ground_conflict",
                 "net " + python_repr(name) +
                     " connects a power source to ground",
                 origin_span);
  // Unknown or selectable endpoint roles cannot be treated as passive.
  // Bidirectional roles need an operating assumption on single-driver nets.
  bool needs_driver = types.contains("digital") || types.contains("power");
  bool ambiguous_driver = !facts.uncertain.empty() ||
                          std::ranges::any_of(known, [](const Known &e) {
                            return e.role == "digital_bidirectional" ||
                                   e.role == "analog_bidirectional";
                          });
  if (compatibility_uncertain && !needs_driver)
    results.emit("incomplete_roles",
                 "net " + python_repr(name) +
                     " has unknown or unselected roles; compatibility checking "
                     "is incomplete",
                 origin_span, {}, "warning");
  if (needs_driver) {
    std::string expected =
        types.contains("power") ? "power_output" : "digital_output";
    bool matching = std::ranges::any_of(
        known, [&](const Known &e) { return e.role == expected; });
    if (ambiguous_driver || compatibility_uncertain)
      results.emit("incomplete_roles",
                   "net " + python_repr(name) +
                       " has unknown or unselected endpoint roles; driver "
                       "checking is incomplete",
                   origin_span, {}, "warning");
    else if (!matching) {
      Strings biases;
      for (auto pin : ctx.pins())
        if (auto *bias = text(view.attribute(pin, "bias"));
            bias && *bias != "none")
          biases.insert(*bias);
      bool biased =
          expected == "digital_output" &&
          (biases == Strings{"pull_up"} || biases == Strings{"pull_down"});
      biased = biased && std::ranges::all_of(open_drivers, [&](const Known &e) {
                 return biases.contains(e.role == "open_drain" ? "pull_up"
                                                               : "pull_down");
               });
      if (!biased)
        results.emit("missing_driver",
                     "net " + python_repr(name) + " requires a " + expected +
                         " endpoint",
                     origin_span);
    }
  }
  return results.diagnostics;
}

// erc_compatibility
std::vector<Diagnostic> compatibility_results(const Context &ctx) {
  const auto &view = ctx.view;
  Results results;
  const auto &name = view.circuit.nets[*ctx.net].name;
  for (const auto &conflict : compatibility(ctx).conflicts) {
    Related related;
    for (auto d : conflict.incompatible)
      related.emplace_back("net." + view.leaf(d).type.value_or("") +
                               " constraint",
                           view.leaf(d).span.json());
    results.emit(
        conflict.capability ? "tristate_capability" : "incompatible_role",
        conflict.capability
            ? conflict.label + " on tri-state net " + python_repr(name) +
                  " requires tristate_capable: true"
            : conflict.label + " roles " + joined(conflict.roles) +
                  " are incompatible with net " + python_repr(name),
        view.record_span(conflict.pin), conflict.label, "error",
        std::move(related));
  }
  return results.diagnostics;
}

// erc_ports
bool within(const InstancePath &path, const InstancePath &scope) {
  return path.size() >= scope.size() &&
         std::equal(scope.begin(), scope.end(), path.begin());
}
void check_port(const View &view, std::size_t port,
                const std::vector<std::size_t> &endpoints, Results &results) {
  const auto &record = view.leaf(port);
  const auto &scope = record.scope;
  auto label = path_text(record.path);
  auto list = [&] {
    auto *role = view.attribute(port, "role");
    return role ? meta_strings(*role) : std::vector<std::string>{};
  }();
  Strings roles(list.begin(), list.end());
  auto policy = attribute_text(view, port, "unused", "explicit");
  auto binding = record.binding;
  auto span = view.record_span(port);
  std::vector<std::size_t> internal, external;
  for (auto pin : endpoints)
    (within(view.leaf(pin).scope, scope) ? internal : external).push_back(pin);
  if (roles == Strings{"do_not_connect"}) {
    if (binding == BindingState::connected)
      results.emit("do_not_connect",
                   "port " + label + " must not connect to a caller net", span,
                   label);
    if (policy == "never")
      results.emit("required_port_connection",
                   "port " + label +
                       ": unused.never conflicts with role.do_not_connect",
                   span, label);
    return;
  }
  // Board ports have no caller. Child ports must honor their caller contract,
  // regardless of how many physical endpoints happen to exist inside them.
  if (!scope.empty()) {
    if (binding == BindingState::omitted && policy == "explicit")
      connection_issue(view, results, "unused_port",
                       "port " + label +
                           " must connect or explicitly bind to no_connect",
                       port);
    else if (policy == "never" && binding != BindingState::connected)
      connection_issue(
          view, results, "required_port_connection",
          "port " + label +
              " must connect; no_connect cannot override unused.never",
          port);
    else if (policy != "always" && binding == BindingState::connected &&
             external.empty())
      connection_issue(view, results, "required_port_connection",
                       "port " + label + " needs an endpoint outside its block",
                       port);
  }
  if (roles.empty() || roles == Strings{"unchecked"})
    return;
  Strings known;
  bool unknown = false;
  std::vector<Strings> alternatives;
  for (auto pin : internal) {
    auto possible_list = view.roles(pin);
    Strings possible(possible_list.begin(), possible_list.end());
    if (possible.size() != 1 || possible.contains("unchecked"))
      unknown = true;
    else
      known.insert(possible.begin(), possible.end());
    if (!possible.empty() && !possible.contains("unchecked"))
      alternatives.push_back(possible);
  }
  // Each permitted role is an alternative aggregate contract. Without a
  // selection we can reject only when none could describe the implementation.
  std::vector<std::string> candidates;
  for (const auto &r : roles)
    if (std::ranges::all_of(alternatives, [&](const Strings &possible) {
          return intersects(possible, port_allowed().at(r));
        }))
      candidates.push_back(r);
  if (candidates.empty()) {
    Related related;
    for (auto pin : internal)
      related.emplace_back(view.label(pin), view.record_span(pin));
    results.emit("port_role",
                 "port " + label + " roles " + joined(roles) +
                     " contradict its internal endpoints (" + joined(known) +
                     ")",
                 span, label, "error", std::move(related));
    return;
  }
  auto has_evidence = [&](const std::string &role) {
    auto evidence = port_evidence().find(role);
    if (evidence == port_evidence().end())
      return true;
    if (intersects(known, evidence->second))
      return true;
    if (role.ends_with("_bidirectional")) {
      auto domain =
          role.substr(0, role.size() - std::string("_bidirectional").size());
      return known.contains(domain + "_input") &&
             known.contains(domain + "_output");
    }
    return false;
  };
  if (unknown || roles.size() > 1)
    results.emit("incomplete_port_role",
                 "port " + label +
                     " has unknown or unselected roles; its contract is not "
                     "fully verified",
                 span, label, "warning");
  else if (std::ranges::none_of(candidates, has_evidence))
    results.emit("port_role",
                 "port " + label + " has no internal endpoint implementing " +
                     *roles.begin(),
                 span, label);
}
std::vector<Diagnostic> port_results(const Context &ctx) {
  Results results;
  for (auto declaration : ctx.view.circuit.nets[*ctx.net].declarations)
    if (ctx.view.leaf(declaration).kind == "port")
      check_port(ctx.view, declaration, ctx.pins(), results);
  return results.diagnostics;
}

// erc_current
using Demand = std::pair<InstancePath, std::optional<Value>>;
struct GroupFacts {
  std::map<std::string, std::vector<Demand>> demands;
  std::set<std::string> covered, annotated;
  std::vector<Diagnostic> diagnostics;
};
Related member_spans(const View &view, const std::vector<std::size_t> &pins) {
  Related related;
  for (auto pin : pins)
    related.emplace_back(view.label(pin), view.record_span(pin));
  return related;
}
// Count each valid group once; invalid groups never hide missing pin budgets.
const GroupFacts &group_facts(const Context &ctx) {
  const auto &view = ctx.view;
  return shared<GroupFacts>(ctx, "current_loads", [&] {
    GroupFacts facts;
    Results results;
    const auto &circuit = view.circuit;
    for (std::size_t p = 0; p < circuit.parts.size(); ++p) {
      const auto &part = circuit.parts[p];
      const auto &path = view.part_labels[p];
      auto *groups = meta_value(part.metadata, "current_loads");
      if (!groups)
        continue;
      for (const auto &[name, group] : groups->fields()) {
        const auto &fields = group.value.fields();
        std::vector<std::size_t> pins;
        std::set<std::optional<std::size_t>> nets;
        for (const auto &number : meta_strings(fields.at("pins").value)) {
          auto leaf = part.pins.at(number);
          pins.push_back(leaf);
          nets.insert(circuit.leaves[leaf].net);
        }
        for (const auto &net : nets)
          if (net)
            facts.annotated.insert(circuit.nets[*net].name);
        auto related = member_spans(view, pins);
        if (nets.contains(std::nullopt) || nets.size() != 1) {
          results.emit("current_load_group",
                       "current load " + path + "." + name +
                           " requires every member pin on the same net",
                       group.span.json(), path, "error", std::move(related));
          continue;
        }
        bool unknown = false, invalid = false;
        for (auto pin : pins) {
          auto roles = view.roles(pin);
          if (!single(roles, "power_input"))
            invalid = true;
          if (roles.size() != 1 || contains(roles, "unchecked"))
            unknown = true;
        }
        if (invalid) {
          results.emit("current_load_group",
                       "current load " + path + "." + name +
                           " requires power_input roles on every member pin",
                       group.span.json(), path, unknown ? "warning" : "error",
                       std::move(related));
          continue;
        }
        auto net = **nets.begin();
        facts.demands[circuit.nets[net].name].emplace_back(
            part.path, number(&fields.at("max_load_current").value));
        for (auto pin : pins)
          facts.covered.insert(view.label(pin));
      }
    }
    facts.diagnostics = std::move(results.diagnostics);
    return facts;
  });
}
// Replace descendants' demands from deepest supply boundary outward.
std::vector<Demand>
aggregate_current_loads(const View &view,
                        const std::vector<std::size_t> &declarations,
                        const std::vector<std::size_t> &endpoints,
                        std::vector<Demand> demands, Results &results) {
  std::vector<std::size_t> boundaries;
  for (auto d : declarations)
    if (view.attribute(d, "aggregate_load"))
      boundaries.push_back(d);
  auto ordered = boundaries;
  std::ranges::stable_sort(ordered, [&](auto a, auto b) {
    auto key = [&](auto d) {
      return std::make_pair(-static_cast<long>(view.leaf(d).scope.size()),
                            path_text(view.leaf(d).path));
    };
    return key(a) < key(b);
  });
  for (auto port : ordered) {
    const auto &record = view.leaf(port);
    const auto &scope = record.scope;
    auto label = path_text(record.path);
    const auto &annotation = record.metadata.at("aggregate_load");
    auto amount =
        *number(&annotation.value.fields().at("max_load_current").value);
    std::vector<std::size_t> internal;
    for (auto pin : endpoints)
      if (within(view.leaf(pin).scope, scope))
        internal.push_back(pin);
    auto roles = view.roles(port);
    auto overlap = std::ranges::count_if(boundaries, [&](auto d) {
                     return view.leaf(d).scope == scope;
                   }) > 1;
    bool sources = std::ranges::any_of(internal, [&](auto pin) {
      return contains(view.roles(pin), "power_output");
    });
    if (scope.empty() || record.kind != "port" || overlap || sources ||
        record.metadata.contains("external_role") ||
        !single(roles, "power_input")) {
      results.emit("aggregate_load_boundary",
                   "aggregate load " + label +
                       " requires a unique power_input boundary with no "
                       "internal source on this net",
                   annotation.span.json(), label);
      continue;
    }
    std::vector<Demand> local, outside;
    for (auto &demand : demands)
      (within(demand.first, scope) ? local : outside).push_back(demand);
    bool unknown_roles = std::ranges::any_of(internal, [&](auto pin) {
      auto possible = view.roles(pin);
      return possible.size() != 1 || contains(possible, "unchecked");
    });
    auto related = member_spans(view, internal);
    if (internal.empty() || unknown_roles ||
        std::ranges::any_of(local, [](const Demand &d) { return !d.second; }))
      results.emit("incomplete_aggregate_load",
                   "aggregate load " + label +
                       " has incomplete internal load information",
                   annotation.span.json(), label, "warning", related);
    double total = 0;
    for (const auto &demand : local)
      if (demand.second)
        total += floating(*demand.second);
    if (total > floating(amount))
      results.emit("aggregate_current_overload",
                   "known internal loads exceed aggregate " + label + " (" +
                       format_quantity(amount, "current") + ")",
                   annotation.span.json(), label, "error", related);
    outside.emplace_back(scope, amount);
    demands = std::move(outside);
  }
  return demands;
}
std::vector<Diagnostic> group_results(const Context &ctx, const Params &) {
  const auto &view = ctx.view;
  const auto &[part, pin] = *view.pins[*ctx.pin];
  const auto &pins = view.circuit.parts[part].pins;
  if (pin != pins.begin()->first)
    return {};
  std::vector<Diagnostic> result;
  for (const auto &d : group_facts(ctx).diagnostics)
    if (d.instance_path == view.part_labels[part])
      result.push_back(d);
  return result;
}
std::vector<Diagnostic> current_results(const Context &ctx) {
  const auto &view = ctx.view;
  Results results;
  const auto &facts = net_facts(ctx);
  const auto &name = view.circuit.nets[*ctx.net].name;
  const auto &endpoints = ctx.pins();
  const auto &declarations = view.circuit.nets[*ctx.net].declarations;
  const auto &groups = group_facts(ctx);
  std::vector<Demand> demands;
  for (auto pin : endpoints)
    if (!groups.covered.contains(view.label(pin)) &&
        single(view.roles(pin), "power_input") && [&] {
          auto *roles = view.attribute(pin, "external_role");
          if (!roles)
            roles = view.attribute(pin, "role");
          return roles && roles->is<MetaList>();
        }())
      demands.emplace_back(view.leaf(pin).scope,
                           number(view.attribute(pin, "max_load_current")));
  if (auto found = groups.demands.find(name); found != groups.demands.end())
    demands.insert(demands.end(), found->second.begin(), found->second.end());
  demands = aggregate_current_loads(view, declarations, endpoints,
                                    std::move(demands), results);
  if (view.leaf(facts.origin).type == "unchecked")
    return results.diagnostics;
  std::vector<std::size_t> sources;
  for (const auto &e : facts.known)
    if (e.role == "power_output")
      sources.push_back(e.pin);
  bool annotated =
      std::ranges::any_of(
          declarations,
          [&](auto d) { return view.attribute(d, "aggregate_load"); }) ||
      groups.annotated.contains(name) ||
      std::ranges::any_of(endpoints, [&](auto pin) {
        return view.attribute(pin, "max_source_current") ||
               view.attribute(pin, "max_load_current");
      });
  if (!annotated)
    return results.diagnostics;
  std::vector<std::optional<Value>> capacities;
  for (auto source : sources)
    capacities.push_back(number(view.attribute(source, "max_source_current")));
  auto origin_span = view.record_span(facts.origin);
  bool missing =
      std::ranges::any_of(capacities, [](const auto &x) { return !x; }) ||
      std::ranges::any_of(demands, [](const Demand &d) { return !d.second; });
  if (!facts.uncertain.empty() || sources.size() != 1 || missing)
    results.emit("incomplete_budget",
                 "net " + python_repr(name) +
                     " lacks a single annotated source or complete load "
                     "information",
                 origin_span, {}, "warning");
  if (sources.size() == 1 && capacities[0]) {
    // Accumulate electrical loads in circuit order using binary64.
    double total = 0;
    for (const auto &demand : demands)
      if (demand.second)
        total += floating(*demand.second);
    if (total > floating(*capacities[0]))
      results.emit("current_overload",
                   "known maximum loads on " + python_repr(name) +
                       " exceed source capacity (" +
                       format_quantity(*capacities[0], "current") + ")",
                   origin_span);
  }
  return results.diagnostics;
}

// erc_voltage
std::vector<Diagnostic> voltage_limits(const Context &ctx, const Params &) {
  const auto &view = ctx.view;
  std::vector<Diagnostic> result;
  struct Range {
    Value low, high;
    std::size_t declaration;
  };
  std::vector<Range> ranges;
  auto below = [](const Value &a, const Value &b) {
    return floating(a) < floating(b);
  };
  for (auto declaration : view.circuit.nets[*ctx.net].declarations) {
    auto low = number(view.attribute(declaration, "min_voltage"));
    auto high = number(view.attribute(declaration, "max_voltage"));
    if (!low && !high)
      continue;
    if (!low || !high || below(*high, *low))
      result.push_back(ctx.diagnostic(
          "voltage_limits",
          "net voltage requires both bounds with min_voltage <= max_voltage",
          view.leaf(declaration).span.json()));
    else
      ranges.push_back({*low, *high, declaration});
  }
  std::set<std::pair<double, double>> distinct;
  for (const auto &range : ranges)
    distinct.emplace(floating(range.low), floating(range.high));
  if (distinct.size() > 1) {
    Related related;
    for (const auto &range : ranges)
      related.emplace_back("voltage range",
                           view.leaf(range.declaration).span.json());
    result.push_back(
        ctx.diagnostic("voltage_limits",
                       "connected net declarations specify different voltage "
                       "ranges",
                       {}, "error", std::move(related)));
  }
  for (auto pin : ctx.pins()) {
    auto label = view.label(pin);
    auto low = number(view.attribute(pin, "min_voltage"));
    auto high = number(view.attribute(pin, "max_voltage"));
    if (!low && !high)
      continue;
    const auto &metadata = view.leaf(pin).metadata;
    auto record =
        (metadata.contains("min_voltage") ? metadata.at("min_voltage")
                                          : metadata.at("max_voltage"))
            .span.json();
    auto pin_diagnostic = [&](const std::string &message, std::string severity,
                              Related related) {
      auto d = ctx.diagnostic("voltage_limits", message, record,
                              std::move(severity), std::move(related));
      d.instance_path = label;
      return d;
    };
    if (low && high && below(*high, *low)) {
      result.push_back(pin_diagnostic(
          label + ": min_voltage exceeds max_voltage", "error", {}));
      continue;
    }
    if (ranges.empty())
      result.push_back(pin_diagnostic(
          label + ": pin voltage limits cannot be checked without a complete "
                  "net voltage range",
          "unknown", {}));
    for (const auto &range : ranges)
      if ((low && below(range.low, *low)) || (high && below(*high, range.high)))
        result.push_back(pin_diagnostic(
            label + ": net voltage range [" +
                format_quantity(range.low, "voltage") + ", " +
                format_quantity(range.high, "voltage") + "] exceeds pin limits",
            "error",
            {{"net voltage range", view.leaf(range.declaration).span.json()}}));
  }
  return result;
}

// erc_electrical
std::vector<Diagnostic> bias_reference(const Context &ctx,
                                       const Params &params) {
  const auto &view = ctx.view;
  const auto &circuit = view.circuit;
  const auto &reference = params.at("pin").string();
  auto label = view.label(*ctx.pin);
  auto *bias = text(view.attribute(*ctx.pin, "bias"));
  std::optional<std::string> expected;
  if (bias && *bias == "pull_up")
    expected = "power";
  else if (bias && *bias == "pull_down")
    expected = "ground";
  const auto &part = circuit.parts[view.pins[*ctx.pin]->first];
  std::optional<std::size_t> target;
  if (auto found = part.pins.find(reference); found != part.pins.end())
    target = found->second;
  else
    for (const auto &[number, leaf] : part.pins)
      if (auto *aliases = meta_value(circuit.leaves[leaf].metadata, "aliases");
          aliases && contains(meta_strings(*aliases), reference)) {
        target = leaf;
        break;
      }
  std::optional<std::string> reason;
  if (!expected)
    reason = "requires bias.pull_up or bias.pull_down";
  else if (!target)
    reason = "unknown reference pin " + python_repr(reference) +
             " on this component";
  else if (!circuit.leaves[*target].net)
    reason = "reference pin " + python_repr(reference) + " is unconnected";
  else if (ctx.net && *circuit.leaves[*target].net == *ctx.net)
    reason = "reference terminal is connected to the signal net itself";
  else {
    Strings types;
    for (auto d : circuit.nets[*circuit.leaves[*target].net].declarations)
      types.insert(circuit.leaves[d].type.value_or(""));
    auto opposite = *expected == "power" ? "ground" : "power";
    if (!types.contains(*expected) || types.contains(opposite))
      reason = "reference pin " + python_repr(reference) +
               " must connect to a declared " + *expected + " net";
  }
  if (!reason)
    return {};
  Related related;
  if (target)
    related.emplace_back("reference terminal", view.record_span(*target));
  auto d = ctx.diagnostic("bias_reference", label + ": " + *reason, {}, "error",
                          std::move(related));
  d.instance_path = label;
  return {d};
}

// erc_primitives: floating-point intervals with infinite bounds.
struct Bound {
  int infinite = 0;
  double value = 0;
  static Bound of(double q) {
    if (std::isnan(q))
      throw Failure("reducer must return an ordered Interval");
    return std::isinf(q) ? inf(q < 0 ? -1 : 1) : Bound{0, q};
  }
  static Bound inf(int sign = 1) { return {sign, 0}; }
  bool operator==(const Bound &o) const {
    return infinite == o.infinite && (infinite || value == o.value);
  }
  bool operator<(const Bound &o) const {
    if (infinite != o.infinite)
      return infinite < o.infinite;
    return !infinite && value < o.value;
  }
  bool operator<=(const Bound &o) const { return !(o < *this); }
  Bound operator+(const Bound &o) const {
    if (infinite && o.infinite && infinite != o.infinite)
      throw Failure("reducer must return an ordered Interval");
    if (infinite || o.infinite)
      return inf(infinite ? infinite : o.infinite);
    return of(value + o.value);
  }
  bool zero() const { return !infinite && value == 0; }
  Bound reciprocal() const { return infinite ? of(0) : of(double(1) / value); }
};
struct Interval {
  Bound lower, upper;
  bool optional = false, unknown = false;
  bool complete() const { return lower == upper && !unknown && !optional; }
};
Bound numeric(const MetaValue &value) {
  auto n = number(&value);
  if (!n)
    throw Failure("reduction values must be numbers");
  return Bound::of(floating(*n));
}
Interval total(const std::vector<Interval> &values) {
  Bound low = Bound::of(0), high = Bound::of(0);
  bool unknown = false;
  for (const auto &v : values) {
    low = low + (v.optional ? std::min(Bound::of(0), v.lower) : v.lower);
    high = high + (v.optional ? std::max(Bound::of(0), v.upper) : v.upper);
    unknown = unknown || !v.complete();
  }
  return {low, high, false, unknown};
}
Interval parallel(const std::vector<Interval> &values) {
  if (std::ranges::any_of(values, [](const Interval &v) {
        return v.lower < Bound::of(0) || v.upper <= Bound::of(0);
      }))
    throw Failure("parallel resistance requires positive values");
  Bound low_g = Bound::of(0), high_g = Bound::of(0);
  bool unknown = false;
  for (const auto &v : values) {
    low_g = low_g + (v.optional || v.upper.infinite ? Bound::of(0)
                                                    : v.upper.reciprocal());
    high_g = high_g + (v.lower.zero() ? Bound::inf() : v.lower.reciprocal());
    unknown = unknown || !v.complete();
  }
  Bound lower = high_g.infinite  ? Bound::of(0)
                : !high_g.zero() ? high_g.reciprocal()
                                 : Bound::inf();
  Bound upper = !low_g.zero() ? low_g.reciprocal() : Bound::inf();
  return {lower, upper, false, unknown};
}
const MetaValue &param(const Params &params, const std::string &name) {
  return params.at(name);
}
std::optional<Value> bound(const Params &params, const std::string &name) {
  return number(&param(params, name));
}
Related pin_spans(const View &view, const std::vector<std::size_t> &pins) {
  Related related;
  for (auto pin : pins)
    related.emplace_back(view.label(pin), view.leaf(pin).span.json());
  return related;
}
Tri roles(const View &view, std::size_t pin, const Strings &accepted) {
  auto possible = view.roles(pin);
  if (possible.empty() || contains(possible, "unchecked"))
    return std::nullopt;
  auto matches = std::ranges::count_if(
      possible, [&](const auto &r) { return accepted.contains(r); });
  if (matches == static_cast<long>(possible.size()))
    return true;
  if (matches == 0)
    return false;
  return std::nullopt;
}
// Three-valued selection: every term must hold; unknown terms make it None.
Tri selected(std::size_t pin, const Context &ctx, const MetaValue &where) {
  Tri state = true;
  for (const auto &term_value : where.list()) {
    const auto &term = term_value.list();
    const auto &kind = term[0].string();
    Tri result;
    if (kind == "role")
      result = roles(ctx.view, pin, {term[1].string()});
    else if (kind == "predicate")
      result = ctx.predicate(term[1].string(), pin, ctx);
    else {
      auto *actual = ctx.view.attribute(pin, term[1].string());
      if (auto *list = actual ? actual->get_if<MetaList>() : nullptr) {
        bool found = std::ranges::any_of(
            *list, [&](const MetaValue &v) { return equivalent(v, term[2]); });
        result = found && list->size() > 1 ? Tri() : Tri(found);
      } else
        result = actual ? Tri(equivalent(*actual, term[2])) : Tri();
    }
    if (result == false)
      return false;
    if (!result)
      state = std::nullopt;
  }
  return state;
}
std::string shown(const Value &value, const std::optional<std::string> &q) {
  return q ? format_quantity(value, *q) : python_str(value);
}
std::vector<Diagnostic> compare(const Context &ctx, const Interval &result,
                                const std::optional<Value> &minimum,
                                const std::optional<Value> &maximum,
                                const std::vector<std::size_t> &pins,
                                const std::optional<std::string> &quantity) {
  if (result.upper < result.lower)
    throw Failure("reducer must return an ordered Interval");
  if (!minimum && !maximum)
    throw Failure("a bounds assertion requires min and/or max");
  if (minimum && maximum && floating(*maximum) < floating(*minimum))
    throw Failure("min exceeds max");
  std::vector<Diagnostic> diagnostics;
  auto related = pin_spans(ctx.view, pins);
  std::optional<Bound> low, high;
  if (minimum)
    low = Bound::of(floating(*minimum));
  if (maximum)
    high = Bound::of(floating(*maximum));
  if (low && result.upper < *low)
    diagnostics.push_back(ctx.error(
        "reduction is below minimum " + shown(*minimum, quantity), related));
  if (high && *high < result.lower)
    diagnostics.push_back(ctx.error(
        "reduction exceeds maximum " + shown(*maximum, quantity), related));
  bool uncertain = (low && result.lower < *low && *low <= result.upper) ||
                   (high && result.lower <= *high && *high < result.upper);
  if (uncertain || !result.complete())
    diagnostics.push_back(ctx.unknown(
        "some contributions are unknown; the reduction is incomplete",
        related));
  return diagnostics;
}
std::optional<std::string> domain(const Context &ctx, const std::string &scope,
                                  const std::string &field) {
  const auto &table = ctx.domains.at(scope);
  return table.contains(field)
             ? std::optional(table.at(field).get<std::string>())
             : std::nullopt;
}
Rule reducer(Interval (*reduce)(const std::vector<Interval> &)) {
  return [reduce](const Context &ctx, const Params &params) {
    auto field = param(params, "field").string();
    auto pin_domain = domain(ctx, "pin", field);
    bool nonnegative = pin_domain == "nonnegative" || pin_domain == "positive";
    std::vector<Interval> samples;
    std::vector<std::size_t> pins;
    for (auto pin : ctx.pins()) {
      auto state = selected(pin, ctx, param(params, "where"));
      if (state == false)
        continue;
      pins.push_back(pin);
      auto *amount = ctx.view.attribute(pin, field);
      Interval sample =
          amount ? Interval{numeric(*amount), numeric(*amount)}
                 : Interval{nonnegative ? Bound::of(0) : Bound::inf(-1),
                            Bound::inf(), false, true};
      if (!state)
        sample = {sample.lower, sample.upper, true, true};
      samples.push_back(sample);
    }
    const auto &net_metadata =
        ctx.view.leaf(ctx.view.origins[*ctx.net]).metadata;
    for (const auto &name : meta_strings(param(params, "net"))) {
      auto *amount = meta_value(net_metadata, name);
      auto net_domain = domain(ctx, "net", name);
      samples.push_back(amount ? Interval{numeric(*amount), numeric(*amount)}
                               : Interval{net_domain == "positive" ||
                                                  net_domain == "nonnegative"
                                              ? Bound::of(0)
                                              : Bound::inf(-1),
                                          Bound::inf(), false, true});
    }
    return compare(ctx, reduce(samples), bound(params, "min"),
                   bound(params, "max"), pins, domain(ctx, "quantity", field));
  };
}
std::vector<Diagnostic> count_rule(const Context &ctx, const Params &params) {
  std::vector<std::size_t> pins;
  Integer matched = 0;
  for (auto pin : ctx.pins()) {
    auto state = selected(pin, ctx, param(params, "what"));
    if (state == false)
      continue;
    pins.push_back(pin);
    if (state == true)
      ++matched;
  }
  Interval result{Bound::of(double(matched)),
                  Bound::of(double(Integer(pins.size())))};
  return compare(ctx, result, bound(params, "min"), bound(params, "max"), pins,
                 {});
}
std::vector<Diagnostic> all_rule(const Context &ctx, const Params &params) {
  std::vector<std::size_t> pins;
  for (auto pin : ctx.pins())
    if (selected(pin, ctx, param(params, "where")) != false)
      pins.push_back(pin);
  bool failed = false, unknown = false;
  for (auto pin : pins) {
    auto result = selected(pin, ctx, param(params, "what"));
    Tri state = result == true                                ? Tri(true)
                : !selected(pin, ctx, param(params, "where")) ? Tri()
                                                              : result;
    failed = failed || state == false;
    unknown = unknown || !state;
  }
  std::vector<Diagnostic> diagnostics;
  auto related = pin_spans(ctx.view, pins);
  if (failed)
    diagnostics.push_back(ctx.error(
        "not every selected endpoint satisfies the condition", related));
  if (unknown)
    diagnostics.push_back(
        ctx.unknown("some endpoint conditions are unknown", related));
  return diagnostics;
}

// erc_standard: CHECKS, their families, and std.default.
const std::vector<std::string> &checks() {
  static const std::vector<std::string> codes{
      "do_not_connect",
      "required_connection",
      "unused_pin",
      "unconnected_net",
      "missing_driver",
      "multiple_drivers",
      "power_ground_conflict",
      "incomplete_roles",
      "unchecked_net",
      "aggregate_load_boundary",
      "aggregate_current_overload",
      "incomplete_aggregate_load",
      "current_load_group",
      "current_overload",
      "incomplete_budget",
      "waiver_scope",
      "waiver_target",
      "incompatible_role",
      "tristate_capability",
      "port_role",
      "incomplete_port_role",
      "required_port_connection",
      "unused_port",
  };
  return codes;
}
using Family = std::vector<Diagnostic> (*)(const Context &);
const std::map<std::string, std::vector<std::pair<std::string, Family>>> &
families() {
  static const std::pair<std::string, Family> ports{"ports", port_results},
      connections{"connections", connection_results},
      drivers{"drivers", driver_results}, current{"current", current_results},
      compatible{"compatibility", compatibility_results};
  static const std::map<std::string,
                        std::vector<std::pair<std::string, Family>>>
      table{
          {"do_not_connect", {ports}},
          {"required_connection", {connections}},
          {"unused_pin", {}},
          {"unconnected_net", {connections}},
          {"missing_driver", {drivers}},
          {"multiple_drivers", {drivers}},
          {"power_ground_conflict", {drivers}},
          {"incomplete_roles", {drivers}},
          {"unchecked_net", {}}, // Compatibility name; reported as a count.
          {"aggregate_load_boundary", {current}},
          {"aggregate_current_overload", {current}},
          {"incomplete_aggregate_load", {current}},
          {"current_load_group", {}},
          {"current_overload", {current}},
          {"incomplete_budget", {current}},
          {"incompatible_role", {compatible}},
          {"tristate_capability", {compatible}},
          {"port_role", {ports}},
          {"incomplete_port_role", {ports}},
          {"required_port_connection", {ports}},
          {"unused_port", {ports}},
      };
  return table;
}
const std::vector<Diagnostic> &
cached(const Context &ctx, const std::string &key,
       const std::function<std::vector<Diagnostic>()> &calculate) {
  auto found = ctx.memo.results.find(key);
  if (found == ctx.memo.results.end())
    found = ctx.memo.results.emplace(key, calculate()).first;
  return found->second;
}
Rule net_check(const std::string &code) {
  return [code](const Context &ctx, const Params &) {
    std::vector<Diagnostic> result;
    for (const auto &[name, family] : families().at(code))
      for (const auto &d : cached(ctx, name + ":" + std::to_string(*ctx.net),
                                  [&] { return family(ctx); }))
        if (d.code == "erc." + code) {
          result.push_back(d);
          if (code.starts_with("incomplete_"))
            result.back().severity = "unknown";
        }
    return result;
  };
}
Rule pin_check(const std::string &code) {
  return [code](const Context &ctx, const Params &) {
    std::vector<Diagnostic> result;
    for (const auto &d : cached(ctx, "pin:" + std::to_string(*ctx.pin),
                                [&] { return pin_results(ctx); }))
      if (d.code == "erc." + code)
        result.push_back(d);
    return result;
  };
}
const std::vector<std::string> pin_codes{"do_not_connect",
                                         "required_connection", "unused_pin"};
} // namespace
const std::map<std::string, StandardRule> &standard_rules() {
  static const auto rules = [] {
    std::map<std::string, StandardRule> table;
    for (const auto &code : checks())
      if (code != "waiver_scope" && code != "waiver_target")
        table["std." + code] = {net_check(code), {}};
    for (const auto &code : pin_codes)
      table["std.pin_" + code] = {pin_check(code), {}};
    table["std.current_groups"] = {group_results, {}};
    Params reduction{{"where", MetaList{}},
                     {"net", MetaList{}},
                     {"min", MetaValue{}},
                     {"max", MetaValue{}}};
    table["std.sum"] = {reducer(total), reduction};
    table["std.parallel"] = {reducer(parallel), reduction};
    table["std.count"] = {count_rule,
                          {{"min", MetaValue{}}, {"max", MetaValue{}}}};
    table["std.any"] = {[](const Context &ctx, const Params &params) {
                          auto bounded = params;
                          bounded["min"] = Integer(1);
                          bounded["max"] = MetaValue{};
                          return count_rule(ctx, bounded);
                        },
                        {}};
    table["std.all"] = {all_rule, {{"where", MetaList{}}}};
    table["std.bias_reference"] = {bias_reference, {}};
    table["std.voltage_limits"] = {voltage_limits, {}};
    return table;
  }();
  return rules;
}
const std::map<std::string, Predicate> &standard_predicates() {
  static const auto predicates = [] {
    std::map<std::string, Predicate> table;
    for (const auto &[name, accepted] : std::map<std::string, Strings>{
             {"active_digital_driver", {"digital_output"}},
             {"power_source", {"power_output"}},
             {"digital_compatible",
              {"passive", "digital_input", "digital_output",
               "digital_bidirectional", "open_drain", "open_source"}},
             {"open_drain_compatible",
              {"passive", "digital_input", "open_drain"}},
             {"open_source_compatible",
              {"passive", "digital_input", "open_source"}},
         })
      table["std." + name] = [accepted](std::size_t pin, const Context &ctx) {
        return roles(ctx.view, pin, accepted);
      };
    table["std.tristate_capable"] = [](std::size_t pin, const Context &ctx) {
      if (roles(ctx.view, pin, {"passive", "digital_input"}) == true)
        return Tri(true);
      if (roles(ctx.view, pin,
                {"digital_output", "digital_bidirectional", "open_drain",
                 "open_source"}) == true)
        return Tri(attribute_true(ctx.view, pin, "tristate_capable"));
      return Tri();
    };
    return table;
  }();
  return predicates;
}
std::vector<std::pair<std::string, std::string>>
standard_applications(const Context &ctx) {
  std::vector<std::pair<std::string, std::string>> result;
  if (ctx.pin) {
    for (const auto &code : pin_codes)
      result.emplace_back(code, "std.pin_" + code);
    result.emplace_back("current_load_group", "std.current_groups");
    return result;
  }
  if (ctx.view.leaf(ctx.owner).type != "unchecked")
    result.emplace_back("voltage_limits", "std.voltage_limits");
  for (const auto &code : checks())
    if (code != "waiver_scope" && code != "waiver_target" &&
        code != "current_load_group")
      result.emplace_back(code, "std." + code);
  return result;
}
} // namespace noscheme::erc
