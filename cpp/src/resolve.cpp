#include "noscheme/resolve.hpp"
#include <algorithm>
#include <cmath>

namespace noscheme {
namespace {
using ast::as;
using Fields = std::vector<const ast::Field *>;
[[noreturn]] void fail(const std::string &code, const std::string &message,
                       const Json &span, const Json &related = Json::array()) {
  const Symbols symbols;
  Evaluator(symbols).error(code, message, span, related);
}
[[noreturn]] void fail(const std::string &code, const std::string &message,
                       const Span &span, const Json &related = Json::array()) {
  fail(code, message, span.json(), related);
}
Json related(const std::string &message, const Span &span) {
  return Json::array({object("message", message, "span", span.json())});
}

Fields fields(const ast::Definition &source, const std::string &section,
              bool bare = false) {
  Fields result;
  for (const auto &node : source.body) {
    if (bare)
      if (auto *field = as<ast::Field>(node))
        result.push_back(field);
    if (auto *s = as<ast::Section>(node); !bare && s && s->name == section)
      for (const auto *field : ast::field_list(s->entries))
        result.push_back(field);
  }
  return result;
}
void remember_order(const Fields &entries, MetadataOrder &orders,
                    std::vector<std::string> path = {}) {
  auto &order = orders[path];
  for (const auto *entry : entries) {
    const auto &key = entry->key.first.text;
    if (std::ranges::find(order, key) == order.end())
      order.push_back(key);
    if (!entry->fields.empty()) {
      auto nested = path;
      nested.push_back(key);
      remember_order(ast::field_list(entry->fields), orders, std::move(nested));
    }
  }
}
void current_loads(Definition &definition) {
  auto found = definition.metadata.find("current_loads");
  if (found == definition.metadata.end())
    return;
  auto &groups = found->second.value.fields();
  std::set<std::string> seen;
  for (const auto &group_name :
       definition.metadata_order.at({"current_loads"})) {
    auto &group = groups.at(group_name);
    auto &entries = group.value.fields();
    if (entries.size() != 2 || !entries.contains("pins") ||
        !entries.contains("max_load_current"))
      fail("metadata.current_load",
           "each current load requires pins and max_load_current", group.span);
    auto &members = entries.at("pins");
    MetaList pins;
    for (const auto &member : members.value.list()) {
      auto alias = definition.aliases.find(member.string());
      if (alias == definition.aliases.end())
        fail("metadata.current_load", "unknown current load pin", members.span);
      const auto &pin = alias->second;
      if (!seen.insert(pin).second)
        fail("metadata.current_load",
             "pin belongs to more than one current load entry", members.span);
      const auto &metadata = definition.interface.at(pin).metadata;
      auto *role = meta_value(metadata, "role");
      if (!role || !std::ranges::contains(meta_strings(*role), "power_input"))
        fail("metadata.current_load",
             "current load pin must permit power_input", members.span);
      if (metadata.contains("max_load_current"))
        fail("metadata.current_load",
             "pin cannot have individual and grouped loads", members.span);
      pins.push_back(pin);
    }
    members.value = std::move(pins);
  }
}
void interchangeable(Definition &definition) {
  auto found = definition.metadata.find("interchangeable");
  if (found == definition.metadata.end() || found->second.value.list().empty())
    return;
  auto &group = found->second;
  MetaList pins;
  std::set<std::string> seen;
  for (const auto &member : group.value.list()) {
    auto alias = definition.aliases.find(member.string());
    if (alias == definition.aliases.end() || !seen.insert(alias->second).second)
      fail("metadata.interchangeable", "unknown or repeated pin", group.span);
    pins.push_back(alias->second);
  }
  if (pins.size() < 2)
    fail("metadata.interchangeable", "an interchangeable group needs two pins",
         group.span);
  auto electrical = [&](const MetaValue &pin) {
    auto metadata = definition.interface.at(pin.string()).metadata;
    metadata.erase("aliases");
    metadata.erase("custom");
    return MetaValue(std::move(metadata));
  };
  const auto baseline = electrical(pins.front());
  for (std::size_t i = 1; i < pins.size(); ++i)
    if (!equivalent(baseline, electrical(pins[i])))
      fail("metadata.interchangeable",
           "interchangeable pins differ electrically", group.span,
           related("pin declaration",
                   definition.interface.at(pins[i].string()).span));
  group.value = std::move(pins);
}
} // namespace
std::vector<std::string> physical_keys(const ast::FieldKey &key,
                                       std::size_t max_pins) {
  if (!key.items.empty()) {
    std::vector<std::string> result;
    std::set<std::string> seen;
    for (const auto &item : key.items)
      for (auto &name : physical_keys(item, max_pins)) {
        if (!seen.insert(name).second)
          fail("resolve.duplicate-pin",
               "pin appears more than once in selection", item.span);
        result.push_back(std::move(name));
        if (result.size() > max_pins)
          fail("resolve.limit", "pin selection exceeds configured pin limit",
               key.span);
      }
    return result;
  }
  const auto &first = key.first.text;
  if (!key.last)
    return {first};
  const auto &last = key.last->text;
  if (first.size() > 20 || last.size() > 20)
    fail("resolve.pin-range", "physical range bounds exceed supported size",
         key.span);
  auto ordinary = [](const std::string &text) {
    return !text.empty() && (text.size() == 1 || text[0] != '0') &&
           std::ranges::all_of(text,
                               [](char c) { return c >= '0' && c <= '9'; });
  };
  if (!ordinary(first) || !ordinary(last))
    fail("resolve.pin-range",
         "physical ranges require non-padded integer labels", key.span);
  Integer lo(first), hi(last);
  if (hi < lo || hi - lo + 1 > max_pins)
    fail("resolve.pin-range", "descending or excessively large physical range",
         key.span);
  std::vector<std::string> result;
  for (auto index = lo; index <= hi; ++index)
    result.push_back(index.str());
  return result;
}
Definitions resolve_interfaces(const Project &project,
                               const RegistryDescription &registry,
                               std::size_t max_pins) {
  Definitions definitions;
  walk_definitions(project, [&](const DefinitionKey &key,
                                const ast::Definition &source,
                                const std::optional<DefinitionKey> &base) {
    const Definition *parent = base ? &definitions.at(*base) : nullptr;
    Definition result = parent ? *parent : Definition{};
    result.key = key;
    result.source = &source;
    result.kind = source.keyword;
    result.parameters = definition_parameters(
        key, source, parent ? &parent->parameters : nullptr);
    std::set<std::string> locals;
    for (const auto &parameter : result.parameters.parameters)
      locals.insert(parameter.name);
    MetadataReader reader(project, registry, key.first, locals);
    if (!parent) {
      result.body_module = key.first;
      for (const auto &item : source.body)
        if (item->kind == ast::Kind::Net || item->kind == ast::Kind::Instance)
          result.body.push_back(item.get());
      if (result.kind == "nettype" && !source.base.empty())
        result.electrical = source.base.back().text;
    }
    std::map<std::string, Span> declared;
    for (const auto *field :
         fields(source, result.kind == "component" ? "pins" : "ports",
                result.kind == "bundle")) {
      auto names = physical_keys(field->key, max_pins);
      if (parent && result.kind == "component")
        for (auto &pin : names)
          if (parent->aliases.contains(pin))
            pin = parent->aliases.at(pin);
      if (result.kind != "component" &&
          (field->key.last || !field->key.items.empty()))
        fail("resolve.interface", "only physical pins support ranges",
             field->span);
      for (const auto &pin : names) {
        if (result.kind != "component" && !is_identifier(pin))
          fail("resolve.interface",
               "port and bundle field names must be identifiers",
               field->key.span);
        if (declared.contains(pin))
          fail("resolve.duplicate-pin", "overlapping pin declaration",
               field->span, related("previous declaration", declared.at(pin)));
        declared[pin] = field->span;
        auto previous = result.interface.find(pin);
        if (parent && previous == result.interface.end())
          fail("resolve.specialization",
               "specialization cannot add an interface", field->span);
        if (parent && result.kind != "component" && field->value)
          fail("resolve.specialization",
               "specialization cannot change interface types", field->span);
        if (!parent && result.kind != "component" && !field->value)
          fail("resolve.interface", "port or bundle field requires a net type",
               field->span);
        auto metadata = result.kind == "component"
                            ? reader.pin(*field, "pin")
                            : reader.read(field->fields, "port");
        Interface interface;
        interface.name = pin;
        interface.metadata = metadata;
        interface.span = field->span;
        if (previous != result.interface.end()) {
          interface.type = previous->second.type;
          interface.metadata_order = previous->second.metadata_order;
          interface.metadata =
              merge_metadata(previous->second.metadata, metadata);
        } else {
          result.interface_order.push_back(pin);
          if (result.kind != "component")
            interface.type = ScopedExpression{field->value.get(), key.first};
        }
        remember_order(ast::field_list(field->fields),
                       interface.metadata_order);
        result.interface[pin] = std::move(interface);
      }
      if (result.interface.size() > max_pins)
        fail("resolve.limit", "interface exceeds configured pin limit",
             field->span);
    }
    result.aliases.clear();
    // Pin declarations, then alias metadata, locate earlier names.
    std::map<std::string, Span> locations;
    for (const auto &pin : result.interface_order) {
      result.aliases[pin] = pin;
      locations[pin] = result.interface.at(pin).span;
    }
    for (const auto &pin : result.interface_order) {
      const auto &metadata = result.interface.at(pin).metadata;
      auto found = metadata.find("aliases");
      if (found == metadata.end())
        continue;
      const auto &aliases = found->second;
      for (const auto &alias : aliases.value.list()) {
        const auto &name = alias.string();
        if (result.aliases.contains(name) && result.aliases.at(name) != pin)
          fail("resolve.alias", "alias identifies multiple pins", aliases.span,
               related("other pin or alias", locations.at(name)));
        result.aliases[name] = pin;
        locations[name] = aliases.span;
      }
    }
    auto metadata_fields = fields(source, "metadata", result.kind == "nettype");
    auto metadata = reader.read(metadata_fields,
                                result.kind == "nettype" ? "net" : result.kind);
    remember_order(metadata_fields, result.metadata_order);
    result.metadata = parent ? merge_metadata(parent->metadata, metadata)
                             : std::move(metadata);
    if (auto found = result.metadata.find("value");
        found != result.metadata.end())
      result.parameters.part_value = ScopedExpression{
          found->second.value.expression(), found->second.module};
    if (result.kind == "component") {
      current_loads(result);
      interchangeable(result);
    }
    if (auto configurations = meta_value(result.metadata, "configurations")) {
      static const MetaFields none;
      auto *declared = meta_value(result.metadata, "erc_choices");
      const auto &choices = declared ? declared->fields() : none;
      for (const auto &configuration_name :
           result.metadata_order.at({"configurations"})) {
        const auto &configuration =
            configurations->fields().at(configuration_name).value.fields();
        for (const auto &name :
             result.metadata_order.at({"configurations", configuration_name})) {
          const auto &selection = configuration.at(name);
          if (!choices.contains(name))
            fail("erc.unknown_choice", "unknown board ERC choice",
                 selection.span);
          if (!std::ranges::contains(meta_strings(choices.at(name).value),
                                     selection.value.string()))
            fail("erc.choice_value", "selection is outside alternatives",
                 selection.span);
        }
      }
    }
    result.resolution_order = definitions.size();
    definitions.emplace(key, std::move(result));
  });
  return definitions;
}
Json interface_summary(const Definitions &definitions) {
  auto result = Json::array();
  for (const auto &[key, definition] : definitions) {
    auto interfaces = Json::object(), body = Json::array();
    for (const auto &[name, pin] : definition.interface) {
      Json type = nullptr;
      if (pin.type.expression)
        type =
            object("value", ast::to_json(*pin.type.expression), "span",
                   pin.type.expression->span.json(), "module", pin.type.module);
      interfaces[name] =
          object("name", name, "type", std::move(type), "metadata",
                 to_json(pin.metadata), "span", pin.span.json());
    }
    for (const auto *node : definition.body)
      body.push_back(ast::to_json(*node));
    auto parameters =
        parameter_summary({{key, definition.parameters}})[0].at("parameters");
    result.push_back(object(
        "module", key.first, "name", key.second, "kind", definition.kind,
        "parameters", std::move(parameters), "interface", std::move(interfaces),
        "metadata", to_json(definition.metadata), "aliases", definition.aliases,
        "body", std::move(body), "body_module", definition.body_module,
        "electrical", definition.electrical));
  }
  return result;
}
} // namespace noscheme
