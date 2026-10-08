#include "noscheme/metadata.hpp"
#include "noscheme/validation.hpp"
#include <algorithm>
#include <limits>

namespace noscheme {
namespace {
std::string path(const Json &expr) {
  const Json *current = &expr;
  std::vector<std::string> parts;
  while (!current->is_null() && current->at("node") == "Member") {
    parts.push_back(current->at("name").at("text"));
    current = &current->at("base");
  }
  if (current->is_null() || current->at("node") != "Name")
    return {};
  std::string result = current->at("text");
  for (auto it = parts.rbegin(); it != parts.rend(); ++it)
    result += "." + *it;
  return result;
}
const Json *field_schema(const Json &schema, const std::string &name) {
  for (const auto &entry : schema)
    if (entry.at("name") == name)
      return &entry.at("schema");
  return nullptr;
}
bool contains(const Json &values, const Json &value) {
  return std::ranges::find(values, value) != values.end();
}
bool numeric(const Json &value) {
  return value.is_object() && value.contains("type") &&
         value.at("type").is_string() && value.at("type") == "number";
}
} // namespace
MetadataReader::MetadataReader(const Project &project,
                               const RegistryDescription &registry,
                               std::string module, std::set<std::string> locals)
    : project(project), registry(registry), module(std::move(module)),
      locals(std::move(locals)) {}
[[noreturn]] void MetadataReader::error(const std::string &code,
                                        const std::string &message,
                                        const Json &span) const {
  const Symbols symbols;
  Evaluator(symbols).error(code, message, span);
}
Json MetadataReader::wrapped(Json value, const Json &span) const {
  return object("value", std::move(value), "span", span, "module", module);
}
const Symbol *MetadataReader::namespace_symbol(const Json &expr,
                                               bool respect_locals) const {
  auto name = path(expr);
  if (name.empty() ||
      (respect_locals && locals.contains(name.substr(0, name.find('.')))))
    return nullptr;
  const auto &symbols = project.modules.at(module).symbols;
  auto found = symbols.find(name);
  return found == symbols.end() ? nullptr : &found->second;
}
Json MetadataReader::enumeration(const Json &expr, const std::string &space,
                                 const Json &values) {
  if (expr.at("node") == "Member") {
    const auto *symbol = namespace_symbol(expr.at("base"));
    if (symbol && symbol->module == "noscheme" && symbol->name == space &&
        contains(values, expr.at("name").at("text")))
      return expr.at("name").at("text");
  }
  error("metadata.enum", "expected an explicitly imported " + space + " value",
        expr.at("span"));
}
Json MetadataReader::literal(const Json &expr, unsigned depth) {
  if (depth > 128)
    error("native.metadata-limit", "metadata nesting exceeds limit",
          expr.at("span"));
  const auto &node = expr.at("node");
  if (node == "String")
    return expr.at("value");
  if (node == "Name" &&
      (expr.at("text") == "true" || expr.at("text") == "false"))
    return expr.at("text") == "true";
  if (node == "Number") {
    auto spelling = expr.at("spelling").get<std::string>();
    // Follow the Python provider's decimal conversion limit, including zero
    // (unlimited), rather than imposing the evaluator's separate bit limit.
    auto decimal_limit =
        registry.data.at("literal_integer_digits").get<std::size_t>();
    if (decimal_limit && spelling.size() > decimal_limit &&
        std::ranges::all_of(spelling,
                            [](char c) { return c >= '0' && c <= '9'; }))
      error("metadata.value", "metadata number exceeds supported literal size",
            expr.at("span"));
    Limits limits;
    limits.integer_bits = std::numeric_limits<std::size_t>::max();
    Evaluator evaluator(project.modules.at(module).symbols, limits);
    try {
      return encoded(evaluator.evaluate(expr));
    } catch (const DiagnosticsFailure &) {
      error("metadata.value", "metadata numbers must be finite and valid",
            expr.at("span"));
    }
  }
  if (node == "Unary" && expr.at("operand").at("node") == "Number") {
    auto value = decoded(literal(expr.at("operand"), depth + 1));
    if (expr.at("operator") == "-") {
      if (auto number = std::get_if<Integer>(&value))
        *number = -*number;
      else
        std::get<double>(value) = -std::get<double>(value);
    }
    return encoded(value);
  }
  if (node == "ListExpr") {
    auto result = Json::array();
    for (const auto &item : expr.at("items"))
      result.push_back(literal(item, depth + 1));
    return result;
  }
  error("metadata.value", "expected a literal metadata value", expr.at("span"));
}
Json MetadataReader::read_value(const Json &schema, const Json &expr,
                                const std::string &scope) {
  const std::string kind = schema.at("kind");
  const auto &node = expr.at("node");
  if (kind == "enum") {
    if (node == "String" && contains(schema.at("values"), expr.at("value")))
      return expr.at("value");
    return enumeration(expr, schema.at("namespace"), schema.at("values"));
  }
  if (kind == "expression")
    return expr;
  if (kind == "role" || kind == "roles") {
    auto result = Json::array();
    if (kind == "roles" && node == "ListExpr") {
      for (const auto &item : expr.at("items"))
        result.push_back(
            enumeration(item, "role", registry.data.at("enums").at("role")));
    } else
      result.push_back(
          enumeration(expr, "role", registry.data.at("enums").at("role")));
    if (result.empty())
      error("metadata.value", "role list cannot be empty", expr.at("span"));
    return result;
  }
  if (kind == "unused")
    return enumeration(expr, "unused", registry.data.at("enums").at("unused"));
  if (kind == "aliases") {
    if (node != "ListExpr")
      error("metadata.value", "aliases require a list of pin names",
            expr.at("span"));
    auto result = Json::array();
    for (const auto &item : expr.at("items")) {
      std::string name;
      if (item.at("node") == "Name")
        name = item.at("text");
      else if (item.at("node") == "String")
        name = item.at("value");
      else if (item.at("node") == "Number") {
        name = item.at("spelling");
        if (!std::ranges::all_of(name,
                                 [](char c) { return c >= '0' && c <= '9'; }))
          name.clear();
      }
      if (name.empty())
        error("metadata.value", "aliases hold names, strings, or integers",
              expr.at("span"));
      result.push_back(std::move(name));
    }
    return result;
  }
  std::string constraint = kind;
  if (registry.data.at("quantities").contains(kind))
    constraint = registry.data.at("quantities").at(kind).at("constraint");
  bool number = constraint == "number" || constraint == "positive" ||
                constraint == "nonnegative";
  bool immediate =
      node == "Number" || node == "String" || node == "ListExpr" ||
      (node == "Unary" && expr.at("operand").at("node") == "Number") ||
      (node == "Name" &&
       (expr.at("text") == "true" || expr.at("text") == "false"));
  if (number && !immediate)
    return object("expression", expr, "constraint", constraint);
  Json value;
  if (scope == "attributes" && node == "Member") {
    const auto *symbol = namespace_symbol(expr.at("base"), false);
    if (!symbol ||
        !std::set<std::string>{"bias", "role", "nettype", "unused"}.contains(
            symbol->name))
      error("metadata.value", "expected a literal or imported enum value",
            expr.at("span"));
    value = enumeration(expr, symbol->name,
                        registry.data.at("enums").at(symbol->name));
  } else
    value = literal(expr);
  bool valid = kind == "literal" || (kind == "string" && value.is_string()) ||
               (kind == "bool" && value.is_boolean());
  if (number && numeric(value)) {
    auto decoded_value = decoded(value);
    bool positive, nonnegative;
    if (auto integer = std::get_if<Integer>(&decoded_value)) {
      positive = *integer > 0;
      nonnegative = *integer >= 0;
    } else {
      auto floating = std::get<double>(decoded_value);
      positive = floating > 0;
      nonnegative = floating >= 0;
    }
    valid = constraint == "number" ||
            (constraint == "positive" ? positive : nonnegative);
  }
  if (!valid)
    error("metadata.value", "invalid " + kind + " metadata", expr.at("span"));
  return value;
}
Json MetadataReader::read_fields(const Json &fields, const Json &schema,
                                 const std::string &scope, unsigned depth) {
  auto result = Json::object();
  for (const auto &field : fields) {
    if (depth > 128)
      error("native.metadata-limit", "metadata nesting exceeds limit",
            field.at("span"));
    if (!field.at("key").at("last").is_null() ||
        !field.at("key").at("items").empty())
      error("metadata.key", "metadata field names cannot be ranges",
            field.at("key").at("span"));
    const std::string key = field.at("key").at("first").at("text");
    Json spec;
    if (scope == "custom" || scope == "attributes")
      spec = object("kind", field.at("fields").empty() ? "literal" : scope);
    else {
      auto found = field_schema(schema, key);
      if (!found)
        error(
            std::set<std::string>{"erc", "differential_pair", "permitted_roles"}
                    .contains(key)
                ? "metadata.unsupported"
                : "metadata.unknown",
            "unsupported metadata field '" + key + "'", field.at("span"));
      spec = *found;
    }
    const std::string kind = spec.at("kind");
    const auto &expr = field.at("value");
    const auto &children = field.at("fields");
    Json value;
    if (kind == "mapping" || kind == "attributes" || kind == "custom" ||
        kind == "aggregate_load") {
      if (!expr.is_null() || (kind != "custom" && children.empty()))
        error(kind == "aggregate_load" ? "metadata.current_load"
                                       : "metadata.value",
              "metadata requires an indented mapping", field.at("span"));
      Json nested = kind == "mapping" ? spec.at("fields")
                    : kind == "aggregate_load"
                        ? registry.data.at("schemas").at("aggregate_load")
                        : Json::array();
      value = read_fields(children, nested, kind, depth + 1);
    } else if (kind == "choice_declarations" || kind == "choice_bindings" ||
               kind == "configurations") {
      value = choices(field, kind);
    } else if (scope == "instance_pin" && (kind == "role" || kind == "roles") &&
               !children.empty()) {
      value = selection(field, true);
    } else if (kind == "waivers") {
      value = waivers(field);
    } else if (kind == "checks") {
      value = checks(field, scope);
    } else if (kind == "current_loads") {
      value = Json::object();
      for (const auto *group : mapping(field)) {
        auto entries = read_fields(
            group->at("fields"), registry.data.at("schemas").at("current_load"),
            "current_load", depth + 1);
        if (!group->at("value").is_null() || entries.empty())
          error("metadata.current_load",
                "each current load requires pins and max_load_current",
                group->at("span"));
        if (entries.contains("pins") && entries.at("pins").at("value").empty())
          error("metadata.current_load", "current load pins cannot be empty",
                group->at("span"));
        value[group->at("key").at("first").at("text").get<std::string>()] =
            wrapped(std::move(entries), group->at("span"));
      }
    } else {
      if (expr.is_null() || !children.empty())
        error("metadata.value", "metadata requires one value",
              field.at("span"));
      value = read_value(spec, expr, scope);
    }
    result[key] = wrapped(std::move(value), field.at("span"));
  }
  return result;
}
// Keep source order for diagnostics, independently of JSON object's key order.
std::vector<const Json *> MetadataReader::mapping(const Json &field) const {
  if (!field.at("value").is_null() || field.at("fields").empty())
    error("metadata.erc", "expected a nonempty indented mapping",
          field.at("span"));
  std::vector<const Json *> result;
  std::set<std::string> seen;
  for (const auto &child : field.at("fields")) {
    const auto &key = child.at("key");
    if (!key.at("last").is_null() || !key.at("items").empty() ||
        !seen.insert(key.at("first").at("text")).second)
      error("metadata.erc", "mapping keys must be distinct names",
            child.at("span"));
    result.push_back(&child);
  }
  return result;
}
namespace {

// Python str.strip recognizes Unicode whitespace, not just ASCII spaces.
bool blank(const std::string &text) {
  for (std::size_t i = 0; i < text.size();) {
    unsigned char c = text[i++];
    unsigned cp = c;
    unsigned remaining = 0;
    if (c >= 0xf0) {
      cp = c & 7;
      remaining = 3;
    } else if (c >= 0xe0) {
      cp = c & 15;
      remaining = 2;
    } else if (c >= 0xc0) {
      cp = c & 31;
      remaining = 1;
    }
    while (remaining && i < text.size()) {
      --remaining;
      cp = (cp << 6) | (static_cast<unsigned char>(text[i++]) & 63);
    }
    if (!((cp >= 9 && cp <= 13) || (cp >= 0x1c && cp <= 0x20) || cp == 0x85 ||
          cp == 0xa0 || cp == 0x1680 || (cp >= 0x2000 && cp <= 0x200a) ||
          cp == 0x2028 || cp == 0x2029 || cp == 0x202f || cp == 0x205f ||
          cp == 0x3000))
      return false;
  }
  return true;
}
bool string_value(const Json &expr) {
  return !expr.is_null() && expr.at("node") == "String";
}
std::string key_name(const Json &field) {
  return field.at("key").at("first").at("text");
}
} // namespace
Json MetadataReader::choice_string(const Json &expr) const {
  if (!string_value(expr) || blank(expr.at("value")))
    error("metadata.erc", "expected a nonempty literal string",
          expr.at("span"));
  return expr.at("value");
}
Json MetadataReader::selection(const Json &field, bool roles) {
  auto entries = mapping(field);
  const Json *choice = nullptr, *cases = nullptr;
  for (const auto *entry : entries) {
    if (key_name(*entry) == "choice")
      choice = entry;
    if (key_name(*entry) == "cases")
      cases = entry;
  }
  if (entries.size() != 2 || !choice || !cases)
    error("metadata.erc", "a selection requires exactly choice and cases",
          field.at("span"));
  if (choice->at("value").is_null() || !choice->at("fields").empty())
    error("metadata.erc", "choice requires a local choice name string",
          choice->at("span"));
  auto name = choice_string(choice->at("value"));
  if (!is_identifier(name.get<std::string>()))
    error("metadata.erc", "choice references must be local names",
          choice->at("span"));
  auto values = Json::object();
  for (const auto *entry : mapping(*cases)) {
    const auto &expr = entry->at("value");
    if (expr.is_null() || !entry->at("fields").empty())
      error("metadata.erc", "each case requires one value", entry->at("span"));
    auto value =
        roles ? enumeration(expr, "role", registry.data.at("enums").at("role"))
              : choice_string(expr);
    values[key_name(*entry)] = wrapped(std::move(value), entry->at("span"));
  }
  return object("choice", wrapped(name, choice->at("span")), "cases",
                wrapped(std::move(values), cases->at("span")));
}
Json MetadataReader::choices(const Json &field, const std::string &kind) {
  auto result = Json::object();
  for (const auto *entry : mapping(field)) {
    auto name = key_name(*entry);
    if (!is_identifier(name))
      error("metadata.erc",
            "choice and configuration names must be identifiers",
            entry->at("span"));
    const auto &expr = entry->at("value");
    Json value;
    if (kind == "configurations")
      value = choices(*entry, "configuration_values");
    else if (kind == "choice_declarations") {
      if (expr.is_null() || expr.at("node") != "ListExpr" ||
          !entry->at("fields").empty() || expr.at("items").empty())
        error("metadata.erc",
              "a public choice requires a nonempty list of string alternatives",
              entry->at("span"));
      value = Json::array();
      std::set<std::string> unique;
      for (const auto &item : expr.at("items")) {
        auto text = choice_string(item);
        value.push_back(text);
        unique.insert(text);
      }
      if (unique.size() != value.size())
        error("metadata.erc", "choice alternatives must be distinct",
              entry->at("span"));
    } else if (kind == "choice_bindings" && !entry->at("fields").empty())
      value = selection(*entry);
    else {
      if (expr.is_null() || !entry->at("fields").empty())
        error("metadata.erc", "choice selection requires a string",
              entry->at("span"));
      value = choice_string(expr);
    }
    result[name] = wrapped(std::move(value), entry->at("span"));
  }
  return result;
}
Json MetadataReader::waivers(const Json &field) {
  if (!field.at("value").is_null() || field.at("fields").empty())
    error("metadata.waiver", "waivers requires check names and string reasons",
          field.at("span"));
  auto result = Json::object();
  for (const auto *entry : mapping(field)) {
    auto name = key_name(*entry);
    if (name.find(':') != std::string::npos ||
        (contains(registry.data.at("checks"), name) &&
         !contains(registry.data.at("waivable_checks"), name)))
      error("metadata.waiver", "check is not waivable", entry->at("span"));
    const Json *reason = &entry->at("value"), *pins = nullptr;
    Json targets = Json::array();
    if (!entry->at("fields").empty()) {
      auto fields = mapping(*entry);
      const Json *reason_field = nullptr;
      for (const auto *f : fields) {
        if (key_name(*f) == "reason")
          reason_field = f;
        if (key_name(*f) == "pins")
          pins = f;
      }
      if (fields.size() != 2 || !reason_field || !pins)
        error("metadata.waiver",
              "targeted waivers require reason and pins fields",
              entry->at("span"));
      reason = &reason_field->at("value");
      const auto &expr = pins->at("value");
      if (!reason_field->at("fields").empty() || !pins->at("fields").empty() ||
          expr.is_null() || expr.at("node") != "ListExpr")
        error("metadata.waiver",
              "pins requires a nonempty list of literal paths",
              entry->at("span"));
      std::set<std::string> unique;
      for (const auto &item : expr.at("items")) {
        if (string_value(item) && !blank(item.at("value"))) {
          targets.push_back(item.at("value"));
          unique.insert(item.at("value"));
        }
      }
      if (targets.empty() || targets.size() != expr.at("items").size() ||
          unique.size() != targets.size())
        error("metadata.waiver",
              "pins requires distinct nonempty literal paths",
              pins->at("span"));
    }
    if (!string_value(*reason) || blank(reason->at("value")))
      error("metadata.waiver", "each waiver requires a nonempty literal reason",
            entry->at("span"));
    Json value = reason->at("value");
    if (pins)
      value = object("reason", wrapped(value, reason->at("span")), "pins",
                     wrapped(targets, pins->at("span")));
    result[name] = wrapped(std::move(value), entry->at("span"));
  }
  return result;
}
std::string MetadataReader::registered_name(const Json &expr) const {
  auto symbol = namespace_symbol(expr);
  if (symbol && symbol->module == "<erc>")
    return symbol->name;
  if (!expr.is_null() && expr.at("node") == "Member") {
    symbol = namespace_symbol(expr.at("base"));
    if (symbol && symbol->module == "noscheme" && symbol->name == "std")
      return "std." + expr.at("name").at("text").get<std::string>();
  }
  return {};
}
const Json *MetadataReader::endpoint_schema(const std::string &name,
                                            bool net) const {
  if (net)
    return field_schema(registry.data.at("schemas").at("net"), name);
  const Json *result = nullptr;
  for (const auto *scope : {"pin", "instance_pin", "port"}) {
    if (auto found = field_schema(registry.data.at("schemas").at(scope), name))
      result = found;
  }
  return result;
}
Json MetadataReader::selector(const Json &expr) {
  auto items =
      expr.at("node") == "ListExpr" ? expr.at("items") : Json::array({expr});
  auto result = Json::array();
  for (const auto &item : items) {
    auto name = registered_name(item);
    auto base = item.at("node") == "Member"
                    ? namespace_symbol(item.at("base"), false)
                    : nullptr;
    if (contains(registry.data.at("predicates"), name))
      result.push_back(Json::array({"predicate", name}));
    else if (base && base->module == "noscheme" && base->name == "role")
      result.push_back(Json::array(
          {"role",
           enumeration(item, "role", registry.data.at("enums").at("role"))}));
    else if (base && base->module == "noscheme" && base->name == "bias")
      result.push_back(Json::array(
          {"field", "bias",
           enumeration(item, "bias", registry.data.at("enums").at("bias"))}));
    else if (item.at("node") == "Name" && endpoint_schema(item.at("text")) &&
             endpoint_schema(item.at("text"))->at("kind") == "bool")
      result.push_back(Json::array({"field", item.at("text"), true}));
    else
      error("erc.application",
            "expected predicate, role, bias, boolean field, or list",
            item.at("span"));
  }
  return result;
}
Json MetadataReader::argument(const Json &schema, const Json &expr) {
  const std::string kind = schema.at("kind");
  if (kind == "field") {
    if (expr.at("node") != "Name" || !endpoint_schema(expr.at("text")))
      error("erc.application", "expected an endpoint field name",
            expr.at("span"));
    return expr.at("text");
  }
  if (kind == "net_fields") {
    auto items =
        expr.at("node") == "ListExpr" ? expr.at("items") : Json::array({expr});
    auto result = Json::array();
    for (const auto &item : items) {
      if (item.at("node") != "Name" || !endpoint_schema(item.at("text"), true))
        error("erc.application", "expected net field names", expr.at("span"));
      result.push_back(item.at("text"));
    }
    return result;
  }
  if (kind == "selector")
    return selector(expr);
  return read_value(schema, expr, "rule");
}
Json MetadataReader::checks(const Json &field, const std::string &scope) {
  auto result = Json::object();
  std::string expected =
      scope == "pin" || scope == "instance_pin" ? "pin" : "net";
  for (const auto *entry : mapping(field)) {
    const auto &call = entry->at("value");
    if (call.is_null() || !entry->at("fields").empty() ||
        !entry->at("default").is_null())
      error("erc.application", "each application is one rule call",
            entry->at("span"));
    bool is_call = call.at("node") == "Call";
    auto name = is_call ? registered_name(call.at("callee")) : "";
    if (!registry.data.at("rules").contains(name))
      error("erc.application", "expected an imported rule call",
            (is_call ? call.at("callee") : call).at("span"));
    const auto &rule = registry.data.at("rules").at(name);
    const auto &params = rule.at("parameters");
    if (call.at("arguments").size() > params.size())
      error("erc.application", "too many positional arguments",
            call.at("span"));
    std::vector<std::pair<std::string, const Json *>> args;
    std::set<std::string> seen;
    const Json *missing = nullptr;
    for (std::size_t i = 0; i < call.at("arguments").size(); ++i) {
      std::string key = params[i].at("name");
      seen.insert(key);
      args.emplace_back(key, &call.at("arguments")[i]);
    }
    for (const auto &kw : call.at("keywords")) {
      const std::string key = kw.at("name").at("text");
      if (key != "missing" && !field_schema(params, key))
        error("erc.application", "unknown rule parameter", kw.at("span"));
      if (!seen.insert(key).second)
        error("erc.application", "duplicate rule argument", kw.at("span"));
      if (key == "missing")
        missing = &kw.at("value");
      else
        args.emplace_back(key, &kw.at("value"));
    }
    if (rule.at("scope") != expected)
      error("erc.application", "rule has incorrect pin/net scope",
            call.at("callee").at("span"));
    auto values =
        object("checker", wrapped(name, call.at("callee").at("span")));
    if (!args.empty()) {
      auto bound = Json::object();
      for (const auto &[key, expr] : args)
        bound[key] = wrapped(argument(*field_schema(params, key), *expr),
                             expr->at("span"));
      values["params"] = wrapped(std::move(bound), call.at("span"));
    }
    if (missing) {
      if (!string_value(*missing) ||
          !contains(registry.data.at("enums").at("missing"),
                    missing->at("value")))
        error("erc.application", "invalid missing-data policy",
              missing->at("span"));
      values["missing"] = wrapped(missing->at("value"), missing->at("span"));
    }
    result[key_name(*entry)] = wrapped(std::move(values), entry->at("span"));
  }
  return result;
}
Json MetadataReader::read(const Json &fields, const std::string &scope) {
  return read_fields(fields,
                     scope == "custom" || scope == "attributes"
                         ? Json::array()
                         : registry.data.at("schemas").at(scope),
                     scope, 0);
}
Json MetadataReader::pin(const Json &field, const std::string &scope) {
  auto result = read(field.at("fields"), scope);
  const auto &expr = field.at("value");
  if (expr.is_null())
    return result;
  std::vector<std::pair<std::string, Json>> groups;
  auto items =
      expr.at("node") == "ListExpr" ? expr.at("items") : Json::array({expr});
  for (const auto &item : items) {
    auto symbol = item.at("node") == "Member"
                      ? namespace_symbol(item.at("base"))
                      : nullptr;
    if (!symbol || symbol->module != "noscheme" ||
        !std::set<std::string>{"role", "bias", "unused"}.contains(symbol->name))
      error("metadata.shorthand",
            "expected builtin role, bias, or unused value", item.at("span"));
    auto found = std::ranges::find(groups, symbol->name,
                                   &std::pair<std::string, Json>::first);
    if (found == groups.end())
      groups.emplace_back(symbol->name, Json::array({item}));
    else
      found->second.push_back(item);
  }
  for (const auto &[key, values] : groups) {
    auto schema = field_schema(registry.data.at("schemas").at(scope), key);
    if (result.contains(key) || !schema)
      error("metadata.shorthand", "field already set or not allowed",
            values.front().at("span"));
    if (values.size() > 1 && schema->at("kind") != "roles")
      error("metadata.shorthand", "field takes one value",
            values[1].at("span"));
    auto value = values.size() == 1 ? values.front()
                                    : object("node", "ListExpr", "span",
                                             expr.at("span"), "items", values);
    result[key] = wrapped(read_value(*schema, value, scope), value.at("span"));
  }
  return result;
}
namespace {
Json merge(const Json &parent, const Json &child,
           std::vector<std::string> path) {
  auto result = parent;
  for (const auto &[key, value] : child.items()) {
    auto found = result.find(key);
    if (found != result.end()) {
      const auto &previous = found->at("value");
      const auto &next = value.at("value");
      if (key == "checker" && path.size() >= 2 &&
          path[path.size() - 2] == "checks" &&
          std::ranges::find(path, "custom") == path.end() && previous != next) {
        const Symbols symbols;
        Evaluator(symbols).error(
            "erc.application_checker",
            "an inherited application cannot change checker", value.at("span"));
      }
      // Wrapped field maps are distinct from encoded numbers and expression
      // ASTs.
      auto mapping = [](const Json &v) {
        return v.is_object() && std::ranges::all_of(v, [](const Json &entry) {
                 return entry.is_object() && entry.contains("value") &&
                        entry.contains("span") && entry.contains("module");
               });
      };
      if (mapping(previous) && mapping(next)) {
        auto nested = path;
        nested.push_back(key);
        auto combined = merge(previous, next, std::move(nested));
        result[key] = value;
        result[key]["value"] = std::move(combined);
        continue;
      }
    }
    result[key] = value;
  }
  return result;
}
} // namespace
Json merge_metadata(const Json &parent, const Json &child) {
  return merge(parent, child, {});
}
} // namespace noscheme
