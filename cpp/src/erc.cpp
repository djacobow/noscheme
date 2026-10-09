// The ERC scheduler, after Python's erc.check_circuit and erc.apply_erc:
// configuration views, applications, net-only deduplication, waivers, and
// reports. Diagnostic order and report text match the Python engine.
#include "noscheme/erc.hpp"
#include "noscheme/erc_engine.hpp"
#include "noscheme/python_bridge.hpp"
#include "noscheme/validation.hpp"
#include <algorithm>
#include <cmath>
#include <deque>

namespace noscheme {
namespace {
using erc::Context;
using erc::Diagnostic;
using erc::Params;
using erc::Tri;
using erc::View;
using Related = std::vector<std::pair<std::string, Json>>;
const std::string code = "native.erc-bridge";

[[noreturn]] void failure(const std::string &message, const Json &span) {
  throw DiagnosticsFailure(Json::array(
      {object("code", "erc.plugin", "severity", "error", "message", message,
              "span", span, "related", Json::array(), "instance_path", nullptr,
              "configuration", nullptr, "application", nullptr)}));
}
// Python's repr of a sorted list of strings.
std::string repr_list(const std::set<std::string> &items) {
  std::string result = "[";
  for (const auto &item : items)
    result += (result.size() > 1 ? ", " : "") + erc::python_repr(item);
  return result + "]";
}
// json.dumps(value, sort_keys=True) for objects of strings and nulls.
std::string python_json(const Json &value) {
  if (value.is_null())
    return "null";
  if (value.is_string()) {
    std::string result = "\"";
    const auto &text = value.get_ref<const std::string &>();
    auto hex = [&](unsigned code) {
      constexpr char digits[] = "0123456789abcdef";
      result += "\\u";
      for (int shift = 12; shift >= 0; shift -= 4)
        result += digits[(code >> shift) & 15];
    };
    for (std::size_t i = 0; i < text.size();) {
      unsigned char c = text[i];
      unsigned code = c, length = 1;
      if (c >= 0xf0)
        code = c & 7, length = 4;
      else if (c >= 0xe0)
        code = c & 15, length = 3;
      else if (c >= 0xc0)
        code = c & 31, length = 2;
      for (unsigned j = 1; j < length; ++j)
        code = (code << 6) | (static_cast<unsigned char>(text[i + j]) & 63);
      i += length;
      if (code == '"' || code == '\\')
        result += '\\', result += static_cast<char>(code);
      else if (code == '\n')
        result += "\\n";
      else if (code == '\r')
        result += "\\r";
      else if (code == '\t')
        result += "\\t";
      else if (code == '\b')
        result += "\\b";
      else if (code == '\f')
        result += "\\f";
      else if (code < 0x20 || code > 0x7e) {
        if (code > 0xffff) {
          code -= 0x10000;
          hex(0xd800 + (code >> 10));
          hex(0xdc00 + (code & 0x3ff));
        } else
          hex(code);
      } else
        result += static_cast<char>(code);
    }
    return result + "\"";
  }
  std::string result = "{";
  for (const auto &[key, item] : value.items())
    result += (result.size() > 1 ? ", " : "") + python_json(key) + ": " +
              python_json(item);
  return result + "}";
}
Json params_json(const Params &params) {
  auto result = Json::object();
  for (const auto &[name, value] : params)
    result[name] = to_json(value);
  return result;
}
// Plugin arguments use tagged integers to retain precision beyond 64 bits.
// Selectors contain nested lists (Python tuples).
MetaValue from_json(const Json &value) {
  if (value.is_null())
    return {};
  if (value.is_boolean())
    return value.get<bool>();
  if (value.is_object() && value.size() == 3 &&
      value.value("type", "") == "number" &&
      (value.value("tag", "") == "integer" ||
       value.value("tag", "") == "double"))
    return to_meta(decoded(value));
  if (value.is_number_integer())
    return Integer(value.dump());
  if (value.is_number())
    return value.get<double>();
  if (value.is_string())
    return value.get<std::string>();
  if (value.is_array()) {
    MetaList result;
    for (const auto &item : value)
      result.push_back(from_json(item));
    return result;
  }
  throw std::invalid_argument("unsupported rule argument");
}
Diagnostic diagnostic_from_json(const Json &d) {
  Diagnostic result{d.at("code"), d.at("severity"), d.at("message"),
                    d.at("span")};
  for (const auto &r : d.at("related"))
    result.related.emplace_back(r.at("message"), r.at("span"));
  if (d.at("instance_path").is_string())
    result.instance_path = d.at("instance_path").get<std::string>();
  return result;
}

struct Application {
  std::string name, checker;
  Params params;
  std::string missing = "warning";
  Json span;
};
// One configuration's plugin work, computed by the Python bridge.
struct PluginWork {
  // Applications grouped by owner ("net:" or "pin:" + label) and rule set.
  std::map<std::pair<std::string, std::string>, std::vector<Application>>
      applications;
  // Plugin rule results by application identity: diagnostics or a failure.
  std::map<std::string, Json> results;
  // Plugin predicate results by identity, predicate, and pin label.
  std::map<std::string, Json> predicates;
};

class Checker {
  const RegistryDescription &registry;
  const Circuit &circuit;
  Json domains;
  std::set<std::string> rule_sets;

  void compute_domains() {
    const auto &quantities = registry.data.at("quantities");
    auto constraint = [&](const std::string &kind) {
      return quantities.contains(kind)
                 ? quantities.at(kind).at("constraint").get<std::string>()
                 : kind;
    };
    // A shared field can have different domain constraints in different
    // scopes. Use a nonnegative lower bound only if every endpoint domain
    // agrees.
    auto pin = Json::object(), net = Json::object(), quantity = Json::object();
    const auto &schemas = registry.data.at("schemas");
    for (const auto *scope : {"pin", "instance_pin", "port"})
      for (const auto &field : schemas.at(scope)) {
        std::string kind = field.at("schema").at("kind");
        if (kind == "enum" || kind == "mapping")
          continue;
        const std::string key = field.at("name");
        auto value = constraint(kind);
        if (pin.contains(key)) {
          std::string previous = pin.at(key);
          std::set<std::string> both{previous, value};
          value = previous == value ? value
                  : std::ranges::includes(
                        std::set<std::string>{"positive", "nonnegative"}, both)
                      ? "nonnegative"
                      : "number";
        }
        pin[key] = value;
      }
    for (const auto &field : schemas.at("net")) {
      std::string kind = field.at("schema").at("kind");
      if (kind != "enum" && kind != "mapping")
        net[field.at("name").get<std::string>()] = constraint(kind);
    }
    for (const auto *scope : {"pin", "instance_pin", "port", "net"})
      for (const auto &field : schemas.at(scope)) {
        std::string kind = field.at("schema").at("kind");
        if (quantities.contains(kind))
          quantity[field.at("name").get<std::string>()] = kind;
      }
    domains = object("pin", pin, "net", net, "quantity", quantity);
  }

public:
  Checker(const RegistryDescription &registry, const Circuit &circuit)
      : registry(registry), circuit(circuit) {
    compute_domains();
    for (const auto &name : registry.data.at("rule_sets"))
      rule_sets.insert(name.get<std::string>());
  }
  bool native_rule(const std::string &checker) const {
    return erc::standard_rules().contains(checker);
  }
  bool native_predicate(const std::string &name) const {
    return erc::standard_predicates().contains(name);
  }
  // Whether any active rule set, explicit application, or selector needs
  // Python.
  bool needs_plugins() const {
    for (const auto &name : circuit.erc_rule_sets)
      if (name != "std.default")
        return true;
    std::function<bool(const MetaValue &)> plugin_predicate =
        [&](const MetaValue &value) {
          auto *list = value.get_if<MetaList>();
          if (!list)
            return false;
          if (list->size() == 2 && (*list)[0].is<std::string>() &&
              (*list)[0].string() == "predicate" &&
              (*list)[1].is<std::string>())
            return !native_predicate((*list)[1].string());
          return std::ranges::any_of(*list, plugin_predicate);
        };
    for (const auto &leaf : circuit.leaves)
      if (auto *checks = meta_value(leaf.metadata, "checks"))
        for (const auto &[name, entry] : checks->fields()) {
          const auto &fields = entry.value.fields();
          if (!native_rule(fields.at("checker").value.string()))
            return true;
          if (auto *params = meta_value(fields, "params"))
            for (const auto &[key, param] : params->fields())
              if (plugin_predicate(param.value))
                return true;
        }
    return false;
  }
  // Python's validate_params for native rules.
  Params validate(const Application &app, const erc::StandardRule &rule) const {
    const auto &declared = registry.data.at("rules").at(app.checker);
    std::set<std::string> names, unknown, missing;
    for (const auto &parameter : declared.at("parameters"))
      names.insert(parameter.at("name").get<std::string>());
    for (const auto &[name, value] : app.params)
      if (!names.contains(name))
        unknown.insert(name);
    for (const auto &name : names)
      if (!app.params.contains(name) && !rule.defaults.contains(name))
        missing.insert(name);
    if (!unknown.empty() || !missing.empty())
      failure("invalid rule arguments: unknown " + repr_list(unknown) +
                  ", missing " + repr_list(missing),
              app.span);
    auto params = rule.defaults;
    for (const auto &[name, value] : app.params)
      params.insert_or_assign(name, value);
    const auto &quantities = registry.data.at("quantities");
    const auto &predicates = registry.data.at("predicates");
    auto strings = [](const MetaValue &value, std::size_t minimum) {
      auto *list = value.get_if<MetaList>();
      return list && list->size() >= minimum &&
             std::ranges::all_of(*list, [](const MetaValue &v) {
               return v.is<std::string>() && !v.string().empty();
             });
    };
    for (const auto &parameter : declared.at("parameters")) {
      const std::string name = parameter.at("name");
      const auto &value = params.at(name);
      if (value.null() && rule.defaults.contains(name) &&
          rule.defaults.at(name).null())
        continue;
      std::string kind = parameter.at("schema").at("kind");
      auto constraint =
          quantities.contains(kind)
              ? quantities.at(kind).at("constraint").get<std::string>()
              : kind;
      bool valid = true;
      if (constraint == "number" || constraint == "positive" ||
          constraint == "nonnegative") {
        auto number = erc::number(&value);
        valid = number && !(std::holds_alternative<double>(*number) &&
                            !std::isfinite(std::get<double>(*number)));
        if (valid && constraint != "number") {
          auto q = erc::decimal(*number);
          valid = constraint == "positive" ? q > 0 : q >= 0;
        }
      } else if (kind == "string")
        valid = value.is<std::string>();
      else if (kind == "bool")
        valid = value.is<bool>();
      else if (kind == "field")
        valid = value.is<std::string>() && !value.string().empty();
      else if (kind == "net_fields")
        valid = strings(value, 0);
      else if (kind == "selector") {
        auto *terms = value.get_if<MetaList>();
        valid =
            terms && std::ranges::all_of(*terms, [&](const MetaValue &t) {
              auto *term = t.get_if<MetaList>();
              if (!term || term->empty() ||
                  !std::ranges::all_of(
                      term->begin(),
                      term->begin() + std::min<std::size_t>(2, term->size()),
                      [](const MetaValue &v) { return v.is<std::string>(); }))
                return false;
              const auto &kind = (*term)[0].string();
              if (kind == "predicate")
                return term->size() == 2 &&
                       std::ranges::find(predicates,
                                         Json((*term)[1].string())) !=
                           predicates.end();
              return (kind == "role" && term->size() == 2) ||
                     (kind == "field" && term->size() == 3);
            });
      }
      if (!valid)
        failure("rule parameter " + erc::python_repr(name) + " requires " +
                    kind,
                app.span);
    }
    return params;
  }

  struct Scheduled {
    Application app;
    std::string identity;
    bool waivable;
    std::size_t owner;
    std::optional<std::size_t> net, pin;
  };
  struct Configuration {
    Json name;
    Json choices = Json::array();
    std::map<std::size_t, std::map<std::string, MetaValue>> selected;
  };
  std::vector<Configuration> configurations(const Json &requested) const {
    const auto &declared = circuit.configurations;
    std::vector<std::string> names;
    if (requested.is_null() || requested.empty())
      for (const auto &[name, unused] : declared)
        names.push_back(name);
    else
      for (const auto &name : requested)
        if (std::ranges::find(names, name.get<std::string>()) == names.end())
          names.push_back(name);
    for (const auto &name : names)
      if (!declared.contains(name))
        throw DiagnosticsFailure(Json::array({object(
            "code", "erc.unknown_configuration", "severity", "error", "message",
            "unknown ERC configuration " + erc::python_repr(name), "span",
            circuit.source.json(), "related", Json::array(), "instance_path",
            nullptr, "configuration", nullptr, "application", nullptr)}));
    std::vector<Configuration> result;
    // No configurations means one unconfigured check.
    auto all = names.empty() ? std::vector<std::optional<std::string>>(1)
                             : std::vector<std::optional<std::string>>(
                                   names.begin(), names.end());
    // Choice environments resolve from the board inward.
    std::vector<const ChoiceScope *> scopes;
    for (const auto &scope : circuit.choice_scopes)
      scopes.push_back(&scope);
    std::ranges::stable_sort(
        scopes, {}, [](const ChoiceScope *s) { return s->scope.size(); });
    using Environment = std::map<std::string, std::optional<std::string>>;
    auto select =
        [](const Meta &binding,
           const Environment &environment) -> std::optional<std::string> {
      if (auto *text = binding.value.get_if<std::string>())
        return *text;
      const auto &fields = binding.value.fields();
      auto found = environment.find(fields.at("choice").value.string());
      if (found == environment.end() || !found->second)
        return std::nullopt;
      return fields.at("cases")
          .value.fields()
          .at(*found->second)
          .value.string();
    };
    for (const auto &name : all) {
      static const MetaFields none;
      const auto &root = name ? declared.at(*name).value.fields() : none;
      std::vector<std::pair<InstancePath, Environment>> environments;
      auto environment_of =
          [&](const InstancePath &path) -> const Environment * {
        for (const auto &[scope, values] : environments)
          if (scope == path)
            return &values;
        return nullptr;
      };
      for (const auto *scope : scopes) {
        static const Environment empty;
        const auto *parent = environment_of(scope->parent_scope);
        const auto &bindings = scope->scope.empty() ? root : scope->bindings;
        Environment values;
        for (const auto &[key, unused] : scope->choices) {
          auto binding = bindings.find(key);
          values[key] = binding == bindings.end()
                            ? std::nullopt
                            : select(binding->second, parent ? *parent : empty);
        }
        environments.emplace_back(scope->scope, std::move(values));
      }
      Configuration configuration;
      configuration.name = name ? Json(*name) : Json(nullptr);
      for (std::size_t i = 0; i < circuit.leaves.size(); ++i) {
        const auto &selections = circuit.leaves[i].selections;
        if (!selections)
          continue;
        for (const auto &[role, selection] : selections->roles)
          if (auto chosen =
                  select(selection.selection, *environment_of(selection.scope)))
            configuration.selected[i][role] = MetaList{*chosen};
      }
      for (const auto &[scope, values] : environments) {
        if (values.empty())
          continue;
        auto selections = Json::object();
        for (const auto &[key, value] : values)
          selections[key] = value ? Json(*value) : Json(nullptr);
        configuration.choices.push_back(
            object("scope", to_json(scope), "selections", selections));
      }
      result.push_back(std::move(configuration));
    }
    return result;
  }

  // check_circuit for one configuration.
  // Waiver records stay in place while active waivers refer to them.
  void check(const View &view, const PluginWork &plugins,
             std::vector<Diagnostic> &diagnostics,
             std::deque<Json> &waiver_records) const {
    for (const auto &name : circuit.erc_rule_sets)
      if (!rule_sets.contains(name))
        failure("unknown ERC rule set " + erc::python_repr(name),
                circuit.source.json());
    erc::Memo memo;
    std::string current_identity;
    std::function<Tri(const std::string &, std::size_t, const Context &)>
        predicate = [&](const std::string &name, std::size_t pin,
                        const Context &ctx) -> Tri {
      if (auto native = erc::standard_predicates().find(name);
          native != erc::standard_predicates().end())
        return native->second(pin, ctx);
      auto label = view.label(pin);
      auto identity = plugins.predicates.find(ctx.application);
      if (identity == plugins.predicates.end() ||
          !identity->second.contains(name) ||
          !identity->second.at(name).contains(label))
        bridge_error("invalid ERC plugin response: no " + name + " value for " +
                         label,
                     ctx.span, code);
      const auto &value = identity->second.at(name).at(label);
      if (value.is_object())
        throw erc::Failure(value.at("failure").get<std::string>());
      return value.is_null() ? Tri() : Tri(value.get<bool>());
    };
    auto context = [&](std::size_t owner, std::optional<std::size_t> net,
                       std::optional<std::size_t> pin) {
      return Context{view,
                     net,
                     pin,
                     owner,
                     "",
                     "",
                     view.leaf(owner).span.json(),
                     "warning",
                     memo,
                     domains,
                     predicate};
    };
    auto owner_key = [&](const Context &ctx) {
      return std::string(ctx.pin ? "pin:" : "net:") + view.label(ctx.owner);
    };
    auto applications = [&](const Context &ctx, bool defaults) {
      std::vector<Application> items;
      if (defaults)
        for (const auto &name : circuit.erc_rule_sets) {
          if (name == "std.default") {
            for (auto &[app, checker] : erc::standard_applications(ctx))
              items.push_back({app, checker, {}, "warning", ctx.span});
          } else if (auto found =
                         plugins.applications.find({owner_key(ctx), name});
                     found != plugins.applications.end())
            for (auto app : found->second) {
              if (app.span.is_null())
                app.span = ctx.span;
              items.push_back(std::move(app));
            }
        }
      if (auto *checks = view.attribute(ctx.owner, "checks"))
        for (const auto &[name, entry] : checks->fields()) {
          const auto &fields = entry.value.fields();
          Application app{name,
                          fields.at("checker").value.string(),
                          {},
                          "warning",
                          entry.span.json()};
          if (auto *params = meta_value(fields, "params"))
            for (const auto &[key, value] : params->fields())
              app.params[key] = value.value;
          if (auto *missing = meta_value(fields, "missing"))
            app.missing = missing->string();
          items.push_back(std::move(app));
        }
      std::vector<Scheduled> result;
      std::set<std::string> seen;
      const auto &rules = registry.data.at("rules");
      for (auto &app : items) {
        if (!seen.insert(app.name).second)
          failure("duplicate application " + erc::python_repr(app.name) +
                      " on " + view.label(ctx.owner),
                  app.span);
        if (!rules.contains(app.checker))
          failure("unknown rule " + erc::python_repr(app.checker), app.span);
        const auto &rule = rules.at(app.checker);
        if (rule.at("scope") != (ctx.pin ? "pin" : "net"))
          failure("rule " + erc::python_repr(app.checker) +
                      " has the wrong scope",
                  app.span);
        if (app.missing != "warning" && app.missing != "error" &&
            app.missing != "ignore")
          failure("invalid missing-data policy", app.span);
        auto identity = owner_key(ctx) + ":" + app.name;
        result.push_back({std::move(app), identity,
                          rule.at("waivable").get<bool>(), ctx.owner, ctx.net,
                          ctx.pin});
      }
      return result;
    };
    std::map<std::string, std::vector<Scheduled>> schedules;
    for (std::size_t n = 0; n < circuit.nets.size(); ++n) {
      const auto &net = circuit.nets[n];
      // A net type's application reaches a net through every declaration of
      // that type. Identical applications of a net-only rule run once, on the
      // outermost declaration; other rules may inspect ctx.owner, so each
      // declaration keeps its application.
      auto declarations = net.declarations;
      std::ranges::stable_sort(declarations, {}, [&](std::size_t d) {
        return view.leaf(d).path != net.path;
      });
      std::set<std::string> seen;
      const auto &rules = registry.data.at("rules");
      for (auto declaration : declarations) {
        auto ctx = context(declaration, n, {});
        std::vector<Scheduled> entries;
        for (auto &entry :
             applications(ctx, view.leaf(declaration).path == net.path)) {
          auto key = entry.app.name + "\n" + entry.app.checker + "\n" +
                     entry.app.missing + "\n" + entry.app.span.dump() + "\n" +
                     params_json(entry.app.params).dump();
          bool net_only = rules.at(entry.app.checker).at("net_only");
          if (!net_only || !seen.contains(key)) {
            seen.insert(key);
            entries.push_back(std::move(entry));
          }
        }
        schedules["net:" + view.label(declaration)] = std::move(entries);
      }
    }
    for (std::size_t p = 0; p < circuit.parts.size(); ++p)
      for (const auto &[name, leaf] : circuit.parts[p].pins)
        schedules["pin:" + view.label(leaf)] =
            applications(context(leaf, circuit.leaves[leaf].net, leaf), true);

    std::map<std::string, Params> default_params;
    using Waivers = std::map<std::string, Json *>;
    auto run = [&](const std::string &owner, const Waivers &waivers) {
      for (const auto &entry : schedules.at(owner)) {
        const auto &app = entry.app;
        auto active = context(entry.owner, entry.net, entry.pin);
        active.application = entry.identity;
        active.checker = app.checker;
        active.span = app.span;
        active.missing = app.missing;
        std::vector<Diagnostic> results;
        if (auto native = erc::standard_rules().find(app.checker);
            native != erc::standard_rules().end()) {
          Params params;
          if (app.params.empty()) {
            // Cache only successful default validation, preserving the first
            // application span for errors.
            auto cached = default_params.find(app.checker);
            if (cached == default_params.end())
              cached = default_params
                           .emplace(app.checker, validate(app, native->second))
                           .first;
            params = cached->second;
          } else
            params = validate(app, native->second);
          try {
            results = native->second.run(active, params);
          } catch (const erc::Failure &e) {
            failure("application " + erc::python_repr(entry.identity) + " (" +
                        app.checker + ") failed: " + e.what(),
                    app.span);
          }
        } else {
          auto found = plugins.results.find(entry.identity);
          if (found == plugins.results.end())
            bridge_error("invalid ERC plugin response: no result for " +
                             entry.identity,
                         app.span, code);
          const auto &outcome = found->second;
          if (outcome.contains("failure"))
            failure(outcome.at("failure"), app.span);
          for (const auto &d : outcome.at("diagnostics"))
            results.push_back(diagnostic_from_json(d));
        }
        for (auto &diagnostic : results) {
          if (diagnostic.severity == "unknown" && app.missing == "ignore")
            continue;
          diagnostic.application = entry.identity;
          if (diagnostic.severity == "unknown")
            diagnostic.severity = app.missing;
          // A waiver names applications anywhere on its flattened net.
          auto waiver = waivers.find(app.name);
          if (waiver != waivers.end() && entry.waivable &&
              (!waiver->second->contains("pins") ||
               (diagnostic.instance_path &&
                std::ranges::find(waiver->second->at("pins"),
                                  Json(*diagnostic.instance_path)) !=
                    waiver->second->at("pins").end())))
            (*waiver->second)["diagnostics"].push_back(diagnostic.json());
          else
            diagnostics.push_back(std::move(diagnostic));
        }
      }
    };
    std::vector<Waivers> by_net(circuit.nets.size());
    for (std::size_t n = 0; n < circuit.nets.size(); ++n) {
      const auto &net = circuit.nets[n];
      auto origin = view.origins[n];
      Waivers active;
      std::set<std::string> physical;
      for (auto pin : view.endpoints[n])
        if (view.leaf(pin).kind != "port")
          physical.insert(view.label(pin));
      for (auto declaration : net.declarations) {
        auto *waivers = view.attribute(declaration, "waivers");
        if (!waivers)
          continue;
        for (const auto &[check, waiver] : waivers->fields()) {
          bool valid = declaration == origin;
          auto *targeted = waiver.value.get_if<MetaFields>();
          auto record = object("net", net.name, "check", check, "reason",
                               targeted ? targeted->at("reason").value.string()
                                        : waiver.value.string(),
                               "span", waiver.span.json(), "valid_scope", valid,
                               "diagnostics", Json::array());
          if (targeted) {
            auto prefix = path_text(view.leaf(declaration).scope);
            auto pins = Json::array();
            std::set<std::string> missing;
            for (const auto &pin : meta_strings(targeted->at("pins").value)) {
              auto label = prefix.empty() ? pin : prefix + "." + pin;
              pins.push_back(label);
              if (!physical.contains(label))
                missing.insert(label);
            }
            record["pins"] = std::move(pins);
            record["valid_targets"] = missing.empty();
            if (!missing.empty()) {
              std::string names;
              for (const auto &label : missing)
                names += (names.empty() ? "" : ", ") + label;
              diagnostics.push_back(
                  {"erc.waiver_target", "error",
                   "waiver pins are not physical endpoints on " +
                       erc::python_repr(net.name) + ": " + names,
                   waiver.span.json()});
            }
          }
          waiver_records.push_back(std::move(record));
          auto &stored = waiver_records.back();
          if (valid && stored.value("valid_targets", true)) {
            bool any = false, waivable = true;
            for (const auto &[owner, entries] : schedules)
              for (const auto &entry : entries)
                if (entry.net == n && entry.app.name == check) {
                  any = true;
                  waivable = waivable && entry.waivable;
                }
            if (!any || !waivable)
              diagnostics.push_back({"erc.waiver_target", "error",
                                     "waiver " + erc::python_repr(check) +
                                         " names no waivable application on " +
                                         erc::python_repr(net.name),
                                     waiver.span.json()});
            else
              active[check] = &stored;
          } else if (!valid)
            diagnostics.push_back(
                {"erc.waiver_scope",
                 "error",
                 "waiver for " + check + " on " + erc::python_repr(net.name) +
                     " must be on its outermost net declaration",
                 waiver.span.json(),
                 {{"outermost declaration", view.leaf(origin).span.json()}}});
        }
      }
      by_net[n] = active;
      run("net:" + view.label(origin), active);
      for (auto declaration : net.declarations)
        if (declaration != origin)
          run("net:" + view.label(declaration), active);
    }
    for (std::size_t p = 0; p < circuit.parts.size(); ++p)
      for (const auto &[name, leaf] : circuit.parts[p].pins) {
        auto net = circuit.leaves[leaf].net;
        run("pin:" + view.label(leaf), net ? by_net[*net] : Waivers{});
      }
  }

  std::vector<std::string> rule_order() const {
    std::vector<std::string> order;
    for (const auto &name : registry.data.at("rule_order"))
      order.push_back(name);
    return order;
  }
};

std::string text_report(const Json &report) {
  auto header = "ERC: " + report.at("errors").dump() + " errors, " +
                report.at("warnings").dump() + " warnings\n";
  auto names = [](const Json &list) {
    std::string result;
    for (const auto &item : list)
      result += (result.empty() ? "" : ", ") + item.get<std::string>();
    return result;
  };
  header += "Implemented checks: " + names(report.at("checks")) + "\n";
  header += "Pending checks: " + names(report.at("pending")) + "\n";
  for (const auto &configuration : report.at("configurations")) {
    auto label = configuration.at("name").is_null()
                     ? std::string("unconfigured")
                     : configuration.at("name").get<std::string>();
    header += "Configuration " + label + ": " +
              configuration.at("errors").dump() + " errors, " +
              configuration.at("warnings").dump() + " warnings\n";
    for (const auto &scope : configuration.at("choices")) {
      InstancePath path;
      for (const auto &part : scope.at("scope"))
        if (part.is_string())
          path.emplace_back(std::in_place_index<0>, part.get<std::string>());
        else
          path.emplace_back(std::in_place_index<1>, part.get<std::size_t>());
      auto text = path_text(path);
      header += "  " + (text.empty() ? "(board)" : text) + ": " +
                python_json(scope.at("selections")) + "\n";
    }
  }
  for (const auto &waiver : report.at("waivers")) {
    const auto &span = waiver.at("span");
    std::string status = !waiver.at("valid_scope").get<bool>() ? "invalid scope"
                         : !waiver.value("valid_targets", true)
                             ? "invalid pin targets"
                             : std::to_string(waiver.at("diagnostics").size()) +
                                   " diagnostics waived";
    std::string targets =
        waiver.contains("pins") ? " pins " + names(waiver.at("pins")) : "";
    auto configuration = waiver.value("configuration", Json(nullptr));
    header += span.at("filename").get<std::string>() + ":" +
              span.at("line").dump() + ":" + span.at("column").dump() +
              ": waiver " + waiver.at("check").get<std::string>() + " on " +
              waiver.at("net").get<std::string>() + targets + " [" +
              (configuration.is_string() ? configuration.get<std::string>()
                                         : "unconfigured") +
              "]: " + waiver.at("reason").get<std::string>() + " (" + status +
              ")\n";
    for (const auto &d : waiver.at("diagnostics"))
      header += "  waived " + d.at("severity").get<std::string>() + " " +
                d.at("code").get<std::string>() + ": " +
                d.at("message").get<std::string>() + "\n";
  }
  std::string lines;
  for (const auto &d : report.at("diagnostics")) {
    if (!lines.empty())
      lines += "\n";
    const auto &s = d.at("span");
    auto location = [](const Json &s) {
      return s.at("filename").get<std::string>() + ":" + s.at("line").dump() +
             ":" + s.at("column").dump();
    };
    lines += location(s) + ": " + d.at("severity").get<std::string>() + " " +
             d.at("code").get<std::string>() +
             (d.at("configuration").is_string()
                  ? " [" + d.at("configuration").get<std::string>() + "]"
                  : "") +
             (d.at("application").is_string() &&
                      !d.at("application").get<std::string>().empty()
                  ? " (" + d.at("application").get<std::string>() + ")"
                  : "") +
             ": " + d.at("message").get<std::string>();
    for (const auto &r : d.at("related"))
      lines += "\n" + location(r.at("span")) +
               ": note: " + r.at("message").get<std::string>();
  }
  return header + lines + "\n";
}

// One bridge request returns every plugin result the circuit needs.
std::vector<PluginWork>
plugin_work(const ProjectConfig &config, const RegistryDescription &registry,
            const Circuit &circuit, const Json &names, const fs::path &python,
            std::chrono::milliseconds timeout, std::stop_token stop) {
  auto span = config.span.json();
  auto paths = Json::array();
  for (const auto &path : config.erc_modules)
    paths.push_back(path.string());
  auto native_rules = Json::array(), native_predicates = Json::array();
  for (const auto &[name, rule] : erc::standard_rules())
    native_rules.push_back(name);
  for (const auto &[name, predicate] : erc::standard_predicates())
    native_predicates.push_back(name);
  auto request = object(
      "version", 1, "method", "plugin_checks", "erc_modules", std::move(paths),
      "span", span, "registry", registry.data, "circuit", to_json(circuit),
      "configurations", names, "native_rules", std::move(native_rules),
      "native_predicates", std::move(native_predicates));
  auto response = python_request(request, python, span, timeout, code, stop);
  validate_bridge_response(response, span, code);
  std::vector<PluginWork> result;
  try {
    if (response.at("configurations").size() != names.size())
      throw std::invalid_argument("configuration count differs from request");
    for (const auto &configuration : response.at("configurations")) {
      PluginWork work;
      for (const auto &[owner, sets] : configuration.at("applications").items())
        for (const auto &[rule_set, apps] : sets.items())
          for (const auto &app : apps) {
            Application item{app.at("name"),
                             app.at("checker"),
                             {},
                             app.at("missing"),
                             app.at("span")};
            for (const auto &[key, value] : app.at("params").items())
              item.params[key] = from_json(value);
            work.applications[{owner, rule_set}].push_back(std::move(item));
          }
      for (const auto &[identity, outcome] :
           configuration.at("results").items()) {
        if (outcome.contains("failure"))
          (void)outcome.at("failure").get<std::string>();
        else
          for (const auto &d : outcome.at("diagnostics"))
            diagnostic_from_json(d);
        work.results[identity] = outcome;
      }
      for (const auto &[identity, values] :
           configuration.at("predicates").items()) {
        for (const auto &[name, pins] : values.items())
          for (const auto &[pin, value] : pins.items())
            if (!value.is_null() && !value.is_boolean() &&
                !value.at("failure").is_string())
              throw std::invalid_argument("invalid predicate value");
        work.predicates[identity] = values;
      }
      result.push_back(std::move(work));
    }
  } catch (const std::exception &e) {
    bridge_error("invalid ERC plugin response: " + std::string(e.what()), span,
                 code);
  }
  return result;
}
} // namespace

ErcResult run_erc(const ProjectConfig &config,
                  const RegistryDescription &registry, const Circuit &circuit,
                  const fs::path &python, const Json &requested,
                  std::chrono::milliseconds timeout, std::stop_token stop) {
  Checker checker(registry, circuit);
  auto configurations = checker.configurations(requested);
  std::vector<PluginWork> plugins(configurations.size());
  if (checker.needs_plugins()) {
    auto names = Json::array();
    for (const auto &configuration : configurations)
      names.push_back(configuration.name);
    plugins =
        plugin_work(config, registry, circuit, names, python, timeout, stop);
  }
  check_cancelled(stop);
  auto waivers = Json::array(), reports = Json::array(),
       diagnostics = Json::array();
  std::size_t errors = 0, warnings = 0;
  for (std::size_t i = 0; i < configurations.size(); ++i) {
    auto &configuration = configurations[i];
    View view(circuit, std::move(configuration.selected));
    std::vector<Diagnostic> local;
    std::deque<Json> local_waivers;
    checker.check(view, plugins[i], local, local_waivers);
    std::size_t local_errors = 0, local_warnings = 0;
    for (auto &d : local) {
      if (configuration.name.is_string())
        d.configuration = configuration.name.get<std::string>();
      local_errors += d.severity == "error";
      local_warnings += d.severity == "warning";
      diagnostics.push_back(d.json());
    }
    for (auto &waiver : local_waivers) {
      waiver["configuration"] = configuration.name;
      for (auto &d : waiver.at("diagnostics"))
        d["configuration"] = configuration.name;
      waivers.push_back(std::move(waiver));
    }
    errors += local_errors;
    warnings += local_warnings;
    reports.push_back(object("name", configuration.name, "choices",
                             configuration.choices, "errors", local_errors,
                             "warnings", local_warnings));
  }
  auto report =
      object("schema_version", 1, "checks", checker.rule_order(), "pending",
             Json::array(), "errors", errors, "warnings", warnings, "waivers",
             std::move(waivers), "configurations", std::move(reports),
             "diagnostics", std::move(diagnostics));
  auto text = text_report(report);
  return {report, report.dump(2) + "\n", std::move(text)};
}
} // namespace noscheme
