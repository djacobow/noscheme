#include "noscheme/registry.hpp"
#include "noscheme/python_bridge.hpp"
#include "noscheme/validation.hpp"
#include <algorithm>
#include <set>

namespace noscheme {
namespace {
[[noreturn]] void fail(const std::string &message, const Json &span) {
  bridge_error(message, span, "native.erc-registry");
}
void require(bool condition, const char *message) {
  if (!condition)
    throw std::invalid_argument(message);
}
void strings(const Json &values) {
  require(values.is_array(), "expected an array of names");
  std::set<std::string> seen;
  for (const auto &value : values) {
    require(value.is_string() && !value.get_ref<const std::string &>().empty(),
            "expected a nonempty name");
    require(seen.insert(value.get<std::string>()).second, "duplicate name");
  }
}
void fields(const Json &values, bool parameters = false, unsigned depth = 0) {
  require(depth < 64, "schema nesting exceeds bridge limit");
  require(values.is_array(), "expected ordered schema fields");
  std::set<std::string> seen;
  for (const auto &field : values) {
    const auto name = field.at("name").get<std::string>();
    require(!name.empty() && seen.insert(name).second,
            "empty or duplicate field name");
    if (parameters)
      require(field.at("has_default").is_boolean(), "invalid has_default flag");
    const auto &schema = field.at("schema");
    auto kind = schema.at("kind").get<std::string>();
    require(!kind.empty(), "empty schema kind");
    if (kind == "mapping")
      fields(schema.at("fields"), false, depth + 1);
    if (kind == "enum") {
      require(schema.at("namespace").is_string(), "invalid enum namespace");
      strings(schema.at("values"));
      require(!schema.at("values").empty(), "empty enum values");
    }
  }
}
} // namespace
ErcExports RegistryDescription::exports() const {
  ErcExports result;
  for (const auto &[space, names] : data.at("exports").items())
    for (const auto &[name, qualified] : names.items())
      result[space][name] = {"<erc>", qualified.get<std::string>()};
  return result;
}
RegistryDescription registry_description(const Json &response,
                                         const Json &span) {
  try {
    validate_bridge_response(response, span, "native.erc-registry");
    const auto &data = response.at("registry");
    if (data.contains("help")) {
      require(data.at("help").is_object(), "invalid editor help");
      for (const auto &[space, entries] : data.at("help").items()) {
        require(entries.is_object(), "invalid editor help namespace");
        for (const auto &[name, description] : entries.items())
          require(description.is_string(), "invalid editor help text");
      }
    }
    require(data.at("schemas").is_object(), "invalid metadata schemas");
    for (const auto &[scope, schema] : data.at("schemas").items())
      fields(schema);
    require(data.at("enums").is_object(), "invalid enum table");
    for (const auto &[name, values] : data.at("enums").items())
      strings(values);
    require(data.at("quantities").is_object(), "invalid quantities");
    for (const auto &[name, quantity] : data.at("quantities").items())
      require(quantity.at("unit").is_string() &&
                  quantity.at("constraint").is_string(),
              "invalid quantity");
    require(data.at("rules").is_object(), "invalid rules");
    for (const auto &[name, rule] : data.at("rules").items()) {
      require(rule.at("scope") == "pin" || rule.at("scope") == "net",
              "invalid rule scope");
      require(rule.at("waivable").is_boolean() &&
                  rule.at("net_only").is_boolean() &&
                  rule.at("signature").is_string(),
              "invalid rule flags");
      fields(rule.at("parameters"), true);
    }
    for (const auto *key :
         {"predicates", "reducers", "rule_sets", "checks", "waivable_checks"})
      strings(data.at(key));
    require(data.at("exports").is_object(), "invalid registry exports");
    for (const auto &[space, names] : data.at("exports").items()) {
      require(names.is_object(), "invalid registry namespace");
      for (const auto &[name, target] : names.items()) {
        require(target.is_string() && target == space + "." + name,
                "invalid exported name");
        require(data.at("rules").contains(target.get<std::string>()) ||
                    std::ranges::find(data.at("predicates"), target) !=
                        data.at("predicates").end(),
                "unknown exported rule or predicate");
      }
    }
    return {data};
  } catch (const DiagnosticsFailure &) {
    throw;
  } catch (const std::exception &e) {
    fail("invalid registry description: " + std::string(e.what()), span);
  }
}
RegistryDescription load_registry(const ProjectConfig &config,
                                  const fs::path &python,
                                  std::chrono::milliseconds timeout,
                                  std::stop_token stop) {
  auto paths = Json::array();
  for (const auto &path : config.erc_modules)
    paths.push_back(path.string());
  auto span = config.span.json();
  auto request = object("version", 1, "method", "describe_registry",
                        "erc_modules", std::move(paths), "span", span);
  return registry_description(python_request(request, python, span, timeout,
                                             "native.erc-registry", stop),
                              span);
}
} // namespace noscheme
