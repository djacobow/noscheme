#include "noscheme/metadata.hpp"
#include "noscheme/validation.hpp"
#include <algorithm>
#include <limits>

namespace noscheme {
namespace {
using ast::as;
using ast::Kind;
const Json *field_schema(const Json &schema, const std::string &name) {
  for (const auto &entry : schema)
    if (entry.at("name") == name)
      return &entry.at("schema");
  return nullptr;
}
bool contains(const Json &values, const std::string &value) {
  return std::ranges::find(values, Json(value)) != values.end();
}
bool boolean_name(const ast::Node &expr) {
  auto *name = as<ast::Name>(&expr);
  return name && (name->text == "true" || name->text == "false");
}
bool negative_number(const ast::Node &expr) {
  auto *unary = as<ast::Unary>(&expr);
  return unary && unary->operand->kind == Kind::Number;
}
// A list's items, or the expression itself.
std::vector<const ast::Node *> items(const ast::Node &expr) {
  if (auto *list = as<ast::ListExpr>(&expr)) {
    std::vector<const ast::Node *> result;
    for (const auto &item : list->items)
      result.push_back(item.get());
    return result;
  }
  return {&expr};
}
} // namespace
MetadataReader::MetadataReader(const Project &project,
                               const RegistryDescription &registry,
                               std::string module, std::set<std::string> locals)
    : project(project), registry(registry), module(std::move(module)),
      locals(std::move(locals)) {}
[[noreturn]] void MetadataReader::error(const std::string &code,
                                        const std::string &message,
                                        const Span &span) const {
  const Symbols symbols;
  Evaluator(symbols).error(code, message, span);
}
Meta MetadataReader::wrapped(MetaValue value, const Span &span) const {
  return {std::move(value), span, module};
}
const Symbol *MetadataReader::namespace_symbol(const ast::Node &expr,
                                               bool respect_locals) const {
  auto name = ast::expression_path(expr);
  if (name.empty() ||
      (respect_locals && locals.contains(name.substr(0, name.find('.')))))
    return nullptr;
  const auto &symbols = project.modules.at(module).symbols;
  auto found = symbols.find(name);
  return found == symbols.end() ? nullptr : &found->second;
}
std::string MetadataReader::enumeration(const ast::Node &expr,
                                        const std::string &space,
                                        const Json &values) {
  if (auto *member = as<ast::Member>(&expr)) {
    const auto *symbol = namespace_symbol(*member->base);
    if (symbol && symbol->module == "noscheme" && symbol->name == space &&
        contains(values, member->name.text))
      return member->name.text;
  }
  error("metadata.enum", "expected an explicitly imported " + space + " value",
        expr.span);
}
MetaValue MetadataReader::literal(const ast::Node &expr, unsigned depth) {
  if (depth > 128)
    error("native.metadata-limit", "metadata nesting exceeds limit", expr.span);
  if (auto *string = as<ast::String>(&expr))
    return string->value;
  if (boolean_name(expr))
    return as<ast::Name>(expr).text == "true";
  if (auto *number = as<ast::Number>(&expr)) {
    const auto &spelling = number->spelling;
    // Follow the Python provider's decimal conversion limit, including zero
    // (unlimited), rather than imposing the evaluator's separate bit limit.
    auto decimal_limit =
        registry.data.at("literal_integer_digits").get<std::size_t>();
    if (decimal_limit && spelling.size() > decimal_limit &&
        std::ranges::all_of(spelling,
                            [](char c) { return c >= '0' && c <= '9'; }))
      error("metadata.value", "metadata number exceeds supported literal size",
            expr.span);
    Limits limits;
    limits.integer_bits = std::numeric_limits<std::size_t>::max();
    Evaluator evaluator(project.modules.at(module).symbols, limits);
    try {
      return to_meta(evaluator.evaluate(expr));
    } catch (const DiagnosticsFailure &) {
      error("metadata.value", "metadata numbers must be finite and valid",
            expr.span);
    }
  }
  if (negative_number(expr)) {
    const auto &unary = as<ast::Unary>(expr);
    auto value = literal(*unary.operand, depth + 1);
    if (unary.op == "-") {
      if (auto number = value.get_if<Integer>())
        *number = -*number;
      else
        *value.get_if<double>() = -*value.get_if<double>();
    }
    return value;
  }
  if (auto *list = as<ast::ListExpr>(&expr)) {
    MetaList result;
    for (const auto &item : list->items)
      result.push_back(literal(*item, depth + 1));
    return result;
  }
  error("metadata.value", "expected a literal metadata value", expr.span);
}
MetaValue MetadataReader::roles(const std::vector<const ast::Node *> &values,
                                const Span &span) {
  MetaList result;
  for (const auto *item : values)
    result.push_back(
        enumeration(*item, "role", registry.data.at("enums").at("role")));
  if (result.empty())
    error("metadata.value", "role list cannot be empty", span);
  return result;
}
MetaValue MetadataReader::read_value(const Json &schema, const ast::Node &expr,
                                     const std::string &scope) {
  const std::string kind = schema.at("kind");
  if (kind == "enum") {
    if (auto *string = as<ast::String>(&expr);
        string && contains(schema.at("values"), string->value))
      return string->value;
    return enumeration(expr, schema.at("namespace"), schema.at("values"));
  }
  if (kind == "expression")
    return &expr;
  if (kind == "role" || kind == "roles")
    return roles(kind == "roles" ? items(expr)
                                 : std::vector<const ast::Node *>{&expr},
                 expr.span);
  if (kind == "unused")
    return enumeration(expr, "unused", registry.data.at("enums").at("unused"));
  if (kind == "aliases") {
    auto *list = as<ast::ListExpr>(&expr);
    if (!list)
      error("metadata.value", "aliases require a list of pin names", expr.span);
    MetaList result;
    for (const auto &item : list->items) {
      std::string name;
      if (auto *n = as<ast::Name>(item))
        name = n->text;
      else if (auto *string = as<ast::String>(item))
        name = string->value;
      else if (auto *number = as<ast::Number>(item)) {
        name = number->spelling;
        if (!std::ranges::all_of(name,
                                 [](char c) { return c >= '0' && c <= '9'; }))
          name.clear();
      }
      if (name.empty())
        error("metadata.value", "aliases hold names, strings, or integers",
              expr.span);
      result.push_back(std::move(name));
    }
    return result;
  }
  std::string constraint = kind;
  if (registry.data.at("quantities").contains(kind))
    constraint = registry.data.at("quantities").at(kind).at("constraint");
  bool number = constraint == "number" || constraint == "positive" ||
                constraint == "nonnegative";
  bool immediate = expr.kind == Kind::Number || expr.kind == Kind::String ||
                   expr.kind == Kind::ListExpr || negative_number(expr) ||
                   boolean_name(expr);
  if (number && !immediate)
    return Constrained{&expr, constraint};
  MetaValue value;
  if (auto *member = as<ast::Member>(&expr); scope == "attributes" && member) {
    const auto *symbol = namespace_symbol(*member->base, false);
    if (!symbol ||
        !std::set<std::string>{"bias", "role", "nettype", "unused"}.contains(
            symbol->name))
      error("metadata.value", "expected a literal or imported enum value",
            expr.span);
    value = enumeration(expr, symbol->name,
                        registry.data.at("enums").at(symbol->name));
  } else
    value = literal(expr);
  bool valid = kind == "literal" ||
               (kind == "string" && value.is<std::string>()) ||
               (kind == "bool" && value.is<bool>());
  if (number && value.number()) {
    bool positive, nonnegative;
    if (auto integer = value.get_if<Integer>()) {
      positive = *integer > 0;
      nonnegative = *integer >= 0;
    } else {
      auto floating = *value.get_if<double>();
      positive = floating > 0;
      nonnegative = floating >= 0;
    }
    valid = constraint == "number" ||
            (constraint == "positive" ? positive : nonnegative);
  }
  if (!valid)
    error("metadata.value", "invalid " + kind + " metadata", expr.span);
  return value;
}
MetaFields MetadataReader::read_fields(const Fields &fields, const Json &schema,
                                       const std::string &scope,
                                       unsigned depth) {
  MetaFields result;
  for (const auto *field : fields) {
    if (depth > 128)
      error("native.metadata-limit", "metadata nesting exceeds limit",
            field->span);
    if (field->key.last || !field->key.items.empty())
      error("metadata.key", "metadata field names cannot be ranges",
            field->key.span);
    const auto &key = field->key.first.text;
    Json spec;
    if (scope == "custom" || scope == "attributes")
      spec = object("kind", field->fields.empty() ? "literal" : scope);
    else {
      auto found = field_schema(schema, key);
      if (!found)
        error(
            std::set<std::string>{"erc", "differential_pair", "permitted_roles"}
                    .contains(key)
                ? "metadata.unsupported"
                : "metadata.unknown",
            "unsupported metadata field '" + key + "'", field->span);
      spec = *found;
    }
    const std::string kind = spec.at("kind");
    const auto *expr = field->value.get();
    const auto &children = field->fields;
    MetaValue value;
    if (kind == "mapping" || kind == "attributes" || kind == "custom" ||
        kind == "aggregate_load") {
      if (expr || (kind != "custom" && children.empty()))
        error(kind == "aggregate_load" ? "metadata.current_load"
                                       : "metadata.value",
              "metadata requires an indented mapping", field->span);
      Json nested = kind == "mapping" ? spec.at("fields")
                    : kind == "aggregate_load"
                        ? registry.data.at("schemas").at("aggregate_load")
                        : Json::array();
      value = read_fields(ast::field_list(children), nested, kind, depth + 1);
    } else if (kind == "choice_declarations" || kind == "choice_bindings" ||
               kind == "configurations") {
      value = choices(*field, kind);
    } else if (scope == "instance_pin" && (kind == "role" || kind == "roles") &&
               !children.empty()) {
      value = selection(*field, true);
    } else if (kind == "waivers") {
      value = waivers(*field);
    } else if (kind == "checks") {
      value = checks(*field, scope);
    } else if (kind == "current_loads") {
      MetaFields groups;
      for (const auto *group : mapping(*field)) {
        auto entries =
            read_fields(ast::field_list(group->fields),
                        registry.data.at("schemas").at("current_load"),
                        "current_load", depth + 1);
        if (group->value || entries.empty())
          error("metadata.current_load",
                "each current load requires pins and max_load_current",
                group->span);
        if (auto *pins = meta_value(entries, "pins");
            pins && pins->list().empty())
          error("metadata.current_load", "current load pins cannot be empty",
                group->span);
        groups.insert_or_assign(group->key.first.text,
                                wrapped(std::move(entries), group->span));
      }
      value = std::move(groups);
    } else {
      if (!expr || !children.empty())
        error("metadata.value", "metadata requires one value", field->span);
      value = read_value(spec, *expr, scope);
    }
    result.insert_or_assign(key, wrapped(std::move(value), field->span));
  }
  return result;
}
// Keep source order for diagnostics, independently of field name order.
MetadataReader::Fields MetadataReader::mapping(const ast::Field &field) const {
  if (field.value || field.fields.empty())
    error("metadata.erc", "expected a nonempty indented mapping", field.span);
  Fields result;
  std::set<std::string> seen;
  for (const auto &child : field.fields) {
    const auto &key = child.key;
    if (key.last || !key.items.empty() || !seen.insert(key.first.text).second)
      error("metadata.erc", "mapping keys must be distinct names", child.span);
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
const ast::String *string_value(const ast::Node *expr) {
  return as<ast::String>(expr);
}
const std::string &key_name(const ast::Field &field) {
  return field.key.first.text;
}
} // namespace
std::string MetadataReader::choice_string(const ast::Node *expr) const {
  auto *string = string_value(expr);
  if (!string || blank(string->value))
    error("metadata.erc", "expected a nonempty literal string",
          expr ? expr->span : Span{});
  return string->value;
}
MetaFields MetadataReader::selection(const ast::Field &field, bool roles) {
  auto entries = mapping(field);
  const ast::Field *choice = nullptr, *cases = nullptr;
  for (const auto *entry : entries) {
    if (key_name(*entry) == "choice")
      choice = entry;
    if (key_name(*entry) == "cases")
      cases = entry;
  }
  if (entries.size() != 2 || !choice || !cases)
    error("metadata.erc", "a selection requires exactly choice and cases",
          field.span);
  if (!choice->value || !choice->fields.empty())
    error("metadata.erc", "choice requires a local choice name string",
          choice->span);
  auto name = choice_string(choice->value.get());
  if (!is_identifier(name))
    error("metadata.erc", "choice references must be local names",
          choice->span);
  MetaFields values;
  for (const auto *entry : mapping(*cases)) {
    const auto *expr = entry->value.get();
    if (!expr || !entry->fields.empty())
      error("metadata.erc", "each case requires one value", entry->span);
    auto value =
        roles ? enumeration(*expr, "role", registry.data.at("enums").at("role"))
              : choice_string(expr);
    values.insert_or_assign(key_name(*entry),
                            wrapped(std::move(value), entry->span));
  }
  return {{"choice", wrapped(std::move(name), choice->span)},
          {"cases", wrapped(std::move(values), cases->span)}};
}
MetaFields MetadataReader::choices(const ast::Field &field,
                                   const std::string &kind) {
  MetaFields result;
  for (const auto *entry : mapping(field)) {
    const auto &name = key_name(*entry);
    if (!is_identifier(name))
      error("metadata.erc",
            "choice and configuration names must be identifiers", entry->span);
    const auto *expr = entry->value.get();
    MetaValue value;
    if (kind == "configurations")
      value = choices(*entry, "configuration_values");
    else if (kind == "choice_declarations") {
      auto *list = as<ast::ListExpr>(expr);
      if (!list || !entry->fields.empty() || list->items.empty())
        error("metadata.erc",
              "a public choice requires a nonempty list of string alternatives",
              entry->span);
      MetaList alternatives;
      std::set<std::string> unique;
      for (const auto &item : list->items) {
        auto text = choice_string(item.get());
        unique.insert(text);
        alternatives.push_back(std::move(text));
      }
      if (unique.size() != alternatives.size())
        error("metadata.erc", "choice alternatives must be distinct",
              entry->span);
      value = std::move(alternatives);
    } else if (kind == "choice_bindings" && !entry->fields.empty())
      value = selection(*entry);
    else {
      if (!expr || !entry->fields.empty())
        error("metadata.erc", "choice selection requires a string",
              entry->span);
      value = choice_string(expr);
    }
    result.insert_or_assign(name, wrapped(std::move(value), entry->span));
  }
  return result;
}
MetaFields MetadataReader::waivers(const ast::Field &field) {
  if (field.value || field.fields.empty())
    error("metadata.waiver", "waivers requires check names and string reasons",
          field.span);
  MetaFields result;
  for (const auto *entry : mapping(field)) {
    const auto &name = key_name(*entry);
    if (name.find(':') != std::string::npos ||
        (contains(registry.data.at("checks"), name) &&
         !contains(registry.data.at("waivable_checks"), name)))
      error("metadata.waiver", "check is not waivable", entry->span);
    const ast::Node *reason = entry->value.get();
    const ast::Field *reason_field = nullptr, *pins = nullptr;
    MetaList targets;
    if (!entry->fields.empty()) {
      auto fields = mapping(*entry);
      for (const auto *f : fields) {
        if (key_name(*f) == "reason")
          reason_field = f;
        if (key_name(*f) == "pins")
          pins = f;
      }
      if (fields.size() != 2 || !reason_field || !pins)
        error("metadata.waiver",
              "targeted waivers require reason and pins fields", entry->span);
      reason = reason_field->value.get();
      auto *list = as<ast::ListExpr>(pins->value);
      if (!reason_field->fields.empty() || !pins->fields.empty() || !list)
        error("metadata.waiver",
              "pins requires a nonempty list of literal paths", entry->span);
      std::set<std::string> unique;
      for (const auto &item : list->items) {
        auto *string = string_value(item.get());
        if (string && !blank(string->value)) {
          targets.push_back(string->value);
          unique.insert(string->value);
        }
      }
      if (targets.empty() || targets.size() != list->items.size() ||
          unique.size() != targets.size())
        error("metadata.waiver",
              "pins requires distinct nonempty literal paths", pins->span);
    }
    auto *text = string_value(reason);
    if (!text || blank(text->value))
      error("metadata.waiver", "each waiver requires a nonempty literal reason",
            entry->span);
    MetaValue value = text->value;
    if (pins)
      value = MetaFields{{"reason", wrapped(text->value, reason->span)},
                         {"pins", wrapped(std::move(targets), pins->span)}};
    result.insert_or_assign(name, wrapped(std::move(value), entry->span));
  }
  return result;
}
std::string MetadataReader::registered_name(const ast::Node *expr) const {
  if (!expr)
    return {};
  auto symbol = namespace_symbol(*expr);
  if (symbol && symbol->module == "<erc>")
    return symbol->name;
  if (auto *member = as<ast::Member>(expr)) {
    symbol = namespace_symbol(*member->base);
    if (symbol && symbol->module == "noscheme" && symbol->name == "std")
      return "std." + member->name.text;
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
MetaList MetadataReader::selector(const ast::Node &expr) {
  MetaList result;
  for (const auto *item : items(expr)) {
    auto name = registered_name(item);
    auto *member = as<ast::Member>(item);
    auto base = member ? namespace_symbol(*member->base, false) : nullptr;
    auto *field = as<ast::Name>(item);
    if (contains(registry.data.at("predicates"), name))
      result.push_back(MetaList{"predicate", name});
    else if (base && base->module == "noscheme" && base->name == "role")
      result.push_back(
          MetaList{"role", enumeration(*item, "role",
                                       registry.data.at("enums").at("role"))});
    else if (base && base->module == "noscheme" && base->name == "bias")
      result.push_back(MetaList{
          "field", "bias",
          enumeration(*item, "bias", registry.data.at("enums").at("bias"))});
    else if (field && endpoint_schema(field->text) &&
             endpoint_schema(field->text)->at("kind") == "bool")
      result.push_back(MetaList{"field", field->text, true});
    else
      error("erc.application",
            "expected predicate, role, bias, boolean field, or list",
            item->span);
  }
  return result;
}
MetaValue MetadataReader::argument(const Json &schema, const ast::Node &expr) {
  const std::string kind = schema.at("kind");
  if (kind == "field") {
    auto *name = as<ast::Name>(&expr);
    if (!name || !endpoint_schema(name->text))
      error("erc.application", "expected an endpoint field name", expr.span);
    return name->text;
  }
  if (kind == "net_fields") {
    MetaList result;
    for (const auto *item : items(expr)) {
      auto *name = as<ast::Name>(item);
      if (!name || !endpoint_schema(name->text, true))
        error("erc.application", "expected net field names", expr.span);
      result.push_back(name->text);
    }
    return result;
  }
  if (kind == "selector")
    return selector(expr);
  return read_value(schema, expr, "rule");
}
MetaFields MetadataReader::checks(const ast::Field &field,
                                  const std::string &scope) {
  MetaFields result;
  std::string expected =
      scope == "pin" || scope == "instance_pin" ? "pin" : "net";
  for (const auto *entry : mapping(field)) {
    if (!entry->value || !entry->fields.empty() || entry->default_value)
      error("erc.application", "each application is one rule call",
            entry->span);
    const auto *call = as<ast::Call>(entry->value);
    auto name = call ? registered_name(call->callee.get()) : "";
    if (!registry.data.at("rules").contains(name))
      error("erc.application", "expected an imported rule call",
            call ? call->callee->span : entry->value->span);
    const auto &rule = registry.data.at("rules").at(name);
    const auto &params = rule.at("parameters");
    if (call->arguments.size() > params.size())
      error("erc.application", "too many positional arguments", call->span);
    std::vector<std::pair<std::string, const ast::Node *>> args;
    std::set<std::string> seen;
    const ast::Node *missing = nullptr;
    for (std::size_t i = 0; i < call->arguments.size(); ++i) {
      std::string key = params[i].at("name");
      seen.insert(key);
      args.emplace_back(key, call->arguments[i].get());
    }
    for (const auto &kw : call->keywords) {
      const auto &key = kw.name.text;
      if (key != "missing" && !field_schema(params, key))
        error("erc.application", "unknown rule parameter", kw.span);
      if (!seen.insert(key).second)
        error("erc.application", "duplicate rule argument", kw.span);
      if (key == "missing")
        missing = kw.value.get();
      else
        args.emplace_back(key, kw.value.get());
    }
    if (rule.at("scope") != expected)
      error("erc.application", "rule has incorrect pin/net scope",
            call->callee->span);
    MetaFields values{{"checker", wrapped(name, call->callee->span)}};
    if (!args.empty()) {
      MetaFields bound;
      for (const auto &[key, expr] : args)
        bound.insert_or_assign(
            key,
            wrapped(argument(*field_schema(params, key), *expr), expr->span));
      values.insert_or_assign("params", wrapped(std::move(bound), call->span));
    }
    if (missing) {
      auto *policy = string_value(missing);
      if (!policy ||
          !contains(registry.data.at("enums").at("missing"), policy->value))
        error("erc.application", "invalid missing-data policy", missing->span);
      values.insert_or_assign("missing", wrapped(policy->value, missing->span));
    }
    result.insert_or_assign(key_name(*entry),
                            wrapped(std::move(values), entry->span));
  }
  return result;
}
MetaFields MetadataReader::read(const Fields &fields,
                                const std::string &scope) {
  return read_fields(fields,
                     scope == "custom" || scope == "attributes"
                         ? Json::array()
                         : registry.data.at("schemas").at(scope),
                     scope, 0);
}
MetaFields MetadataReader::pin(const ast::Field &field,
                               const std::string &scope) {
  auto result = read(field.fields, scope);
  if (!field.value)
    return result;
  const auto &expr = *field.value;
  std::vector<std::pair<std::string, std::vector<const ast::Node *>>> groups;
  for (const auto *item : items(expr)) {
    auto *member = as<ast::Member>(item);
    auto symbol = member ? namespace_symbol(*member->base) : nullptr;
    if (!symbol || symbol->module != "noscheme" ||
        !std::set<std::string>{"role", "bias", "unused"}.contains(symbol->name))
      error("metadata.shorthand",
            "expected builtin role, bias, or unused value", item->span);
    auto found = std::ranges::find(groups, symbol->name,
                                   [](const auto &g) { return g.first; });
    if (found == groups.end())
      groups.push_back({symbol->name, {item}});
    else
      found->second.push_back(item);
  }
  for (const auto &[key, values] : groups) {
    auto schema = field_schema(registry.data.at("schemas").at(scope), key);
    if (result.contains(key) || !schema)
      error("metadata.shorthand", "field already set or not allowed",
            values.front()->span);
    if (values.size() > 1 && schema->at("kind") != "roles")
      error("metadata.shorthand", "field takes one value", values[1]->span);
    if (values.size() == 1)
      result.insert_or_assign(
          key, wrapped(read_value(*schema, *values.front(), scope),
                       values.front()->span));
    else
      result.insert_or_assign(key,
                              wrapped(roles(values, expr.span), expr.span));
  }
  return result;
}
} // namespace noscheme
