#include "noscheme/resolve.hpp"
#include <algorithm>
#include <cmath>

namespace noscheme {
namespace {
[[noreturn]] void fail(const std::string &code, const std::string &message,
                       const Json &span, const Json &related = Json::array()) {
  const Symbols symbols;
  Evaluator(symbols).error(code, message, span, related);
}
Json related(const std::string &message, const Json &span) {
  return Json::array({object("message", message, "span", span)});
}

std::vector<const Json *> fields(const Json &source, const std::string &section,
                                 bool bare = false) {
  std::vector<const Json *> result;
  for (const auto &node : source.at("body")) {
    if (bare && node.at("node") == "Field")
      result.push_back(&node);
    if (!bare && node.at("node") == "Section" && node.at("name") == section)
      for (const auto &entry : node.at("entries"))
        result.push_back(&entry);
  }
  return result;
}
Json field_array(const std::vector<const Json *> &fields) {
  auto result = Json::array();
  for (const auto *field : fields)
    result.push_back(*field);
  return result;
}
Json comparable(const Json &value) {
  if (value.is_object()) {
    if (value.contains("module") && value.at("module").is_string() &&
        value.contains("span") && value.contains("value"))
      return comparable(value.at("value"));
    auto result = Json::object();
    bool ast = value.contains("node") && value.at("node").is_string();
    for (const auto &[key, item] : value.items())
      if (!(ast && key == "span"))
        result[key] = comparable(item);
    return result;
  }
  if (value.is_array()) {
    auto result = Json::array();
    for (const auto &item : value)
      result.push_back(comparable(item));
    return result;
  }
  return value;
}
bool number(const Json &value) {
  return value.is_object() && value.contains("type") &&
         value.at("type") == "number" && value.contains("tag");
}
bool equal(const Json &left, const Json &right) {
  if ((number(left) || left.is_boolean()) &&
      (number(right) || right.is_boolean())) {
    auto a =
        left.is_boolean() ? Value(Integer(left.get<bool>())) : decoded(left);
    auto b =
        right.is_boolean() ? Value(Integer(right.get<bool>())) : decoded(right);
    if (a.index() == b.index())
      return a == b;
    const auto &integer = std::holds_alternative<Integer>(a)
                              ? std::get<Integer>(a)
                              : std::get<Integer>(b);
    double floating = std::holds_alternative<double>(a) ? std::get<double>(a)
                                                        : std::get<double>(b);
    return floating == std::trunc(floating) && integer == Integer(floating);
  }
  if (left.type() != right.type() || left.size() != right.size())
    return false;
  if (left.is_object()) {
    for (const auto &[key, value] : left.items())
      if (!right.contains(key) || !equal(value, right.at(key)))
        return false;
    return true;
  }
  if (left.is_array()) {
    for (std::size_t i = 0; i < left.size(); ++i)
      if (!equal(left[i], right[i]))
        return false;
    return true;
  }
  return left == right;
}
void remember_order(const std::vector<const Json *> &entries,
                    MetadataOrder &orders, std::vector<std::string> path = {}) {
  auto &order = orders[path];
  for (const auto *entry : entries) {
    std::string key = entry->at("key").at("first").at("text");
    if (std::ranges::find(order, key) == order.end())
      order.push_back(key);
    if (!entry->at("fields").empty()) {
      auto nested = path;
      nested.push_back(key);
      std::vector<const Json *> children;
      for (const auto &child : entry->at("fields"))
        children.push_back(&child);
      remember_order(children, orders, std::move(nested));
    }
  }
}
void current_loads(Definition &definition) {
  if (!definition.metadata.contains("current_loads"))
    return;
  auto &groups = definition.metadata.at("current_loads").at("value");
  std::set<std::string> seen;
  for (const auto &group_name :
       definition.metadata_order.at({"current_loads"})) {
    auto &group = groups.at(group_name);
    auto &entries = group.at("value");
    if (entries.size() != 2 || !entries.contains("pins") ||
        !entries.contains("max_load_current"))
      fail("metadata.current_load",
           "each current load requires pins and max_load_current",
           group.at("span"));
    auto &members = entries.at("pins");
    auto pins = Json::array();
    for (const auto &member : members.at("value")) {
      auto found = definition.aliases.find(member.get<std::string>());
      if (found == definition.aliases.end())
        fail("metadata.current_load", "unknown current load pin",
             members.at("span"));
      const auto &pin = found->second;
      if (!seen.insert(pin).second)
        fail("metadata.current_load",
             "pin belongs to more than one current load entry",
             members.at("span"));
      const auto &metadata = definition.interface.at(pin).metadata;
      if (!metadata.contains("role") ||
          std::ranges::find(metadata.at("role").at("value"), "power_input") ==
              metadata.at("role").at("value").end())
        fail("metadata.current_load",
             "current load pin must permit power_input", members.at("span"));
      if (metadata.contains("max_load_current"))
        fail("metadata.current_load",
             "pin cannot have individual and grouped loads",
             members.at("span"));
      pins.push_back(pin);
    }
    members["value"] = std::move(pins);
  }
}
void interchangeable(Definition &definition) {
  if (!definition.metadata.contains("interchangeable"))
    return;
  auto &group = definition.metadata.at("interchangeable");
  if (group.at("value").empty())
    return;
  auto pins = Json::array();
  std::set<std::string> seen;
  for (const auto &member : group.at("value")) {
    auto found = definition.aliases.find(member.get<std::string>());
    if (found == definition.aliases.end() || !seen.insert(found->second).second)
      fail("metadata.interchangeable", "unknown or repeated pin",
           group.at("span"));
    pins.push_back(found->second);
  }
  if (pins.size() < 2)
    fail("metadata.interchangeable", "an interchangeable group needs two pins",
         group.at("span"));
  auto electrical = [&](const std::string &pin) {
    auto metadata = definition.interface.at(pin).metadata;
    metadata.erase("aliases");
    metadata.erase("custom");
    return comparable(metadata);
  };
  const auto baseline = electrical(pins.front());
  for (std::size_t i = 1; i < pins.size(); ++i)
    if (!equal(baseline, electrical(pins[i])))
      fail("metadata.interchangeable",
           "interchangeable pins differ electrically", group.at("span"),
           related("pin declaration", definition.interface.at(pins[i]).span));
  group["value"] = std::move(pins);
}
} // namespace
std::vector<std::string> physical_keys(const Json &key, std::size_t max_pins) {
  if (!key.at("items").empty()) {
    std::vector<std::string> result;
    std::set<std::string> seen;
    for (const auto &item : key.at("items"))
      for (auto &name : physical_keys(item, max_pins)) {
        if (!seen.insert(name).second)
          fail("resolve.duplicate-pin",
               "pin appears more than once in selection", item.at("span"));
        result.push_back(std::move(name));
        if (result.size() > max_pins)
          fail("resolve.limit", "pin selection exceeds configured pin limit",
               key.at("span"));
      }
    return result;
  }
  std::string first = key.at("first").at("text");
  if (key.at("last").is_null())
    return {first};
  std::string last = key.at("last").at("text");
  if (first.size() > 20 || last.size() > 20)
    fail("resolve.pin-range", "physical range bounds exceed supported size",
         key.at("span"));
  auto ordinary = [](const std::string &text) {
    return !text.empty() && (text.size() == 1 || text[0] != '0') &&
           std::ranges::all_of(text,
                               [](char c) { return c >= '0' && c <= '9'; });
  };
  if (!ordinary(first) || !ordinary(last))
    fail("resolve.pin-range",
         "physical ranges require non-padded integer labels", key.at("span"));
  Integer lo(first), hi(last);
  if (hi < lo || hi - lo + 1 > max_pins)
    fail("resolve.pin-range", "descending or excessively large physical range",
         key.at("span"));
  std::vector<std::string> result;
  for (auto index = lo; index <= hi; ++index)
    result.push_back(index.str());
  return result;
}
Definitions resolve_interfaces(const Project &project,
                               const RegistryDescription &registry,
                               std::size_t max_pins) {
  Definitions definitions;
  walk_definitions(project, [&](const DefinitionKey &key, const Json &source,
                                const std::optional<DefinitionKey> &base) {
    const Definition *parent = base ? &definitions.at(*base) : nullptr;
    Definition result = parent ? *parent : Definition{};
    result.key = key;
    result.source = &source;
    result.kind = source.at("kind");
    result.parameters = definition_parameters(
        key, source, parent ? &parent->parameters : nullptr);
    std::set<std::string> locals;
    for (const auto &parameter : result.parameters.parameters)
      locals.insert(parameter.name);
    MetadataReader reader(project, registry, key.first, locals);
    if (!parent) {
      result.body_module = key.first;
      for (const auto &item : source.at("body"))
        if (item.at("node") == "Net" || item.at("node") == "Instance")
          result.body.push_back(&item);
      if (result.kind == "nettype" && !source.at("base").is_null() &&
          !source.at("base").empty())
        result.electrical = source.at("base").back().at("text");
    }
    std::map<std::string, Json> declared;
    for (const auto *field :
         fields(source, result.kind == "component" ? "pins" : "ports",
                result.kind == "bundle")) {
      auto names = physical_keys(field->at("key"), max_pins);
      if (parent && result.kind == "component")
        for (auto &pin : names)
          if (parent->aliases.contains(pin))
            pin = parent->aliases.at(pin);
      if (result.kind != "component" &&
          (!field->at("key").at("last").is_null() ||
           !field->at("key").at("items").empty()))
        fail("resolve.interface", "only physical pins support ranges",
             field->at("span"));
      for (const auto &pin : names) {
        if (result.kind != "component" && !is_identifier(pin))
          fail("resolve.interface",
               "port and bundle field names must be identifiers",
               field->at("key").at("span"));
        if (declared.contains(pin))
          fail("resolve.duplicate-pin", "overlapping pin declaration",
               field->at("span"),
               related("previous declaration", declared.at(pin)));
        declared[pin] = field->at("span");
        auto previous = result.interface.find(pin);
        if (parent && previous == result.interface.end())
          fail("resolve.specialization",
               "specialization cannot add an interface", field->at("span"));
        if (parent && result.kind != "component" &&
            !field->at("value").is_null())
          fail("resolve.specialization",
               "specialization cannot change interface types",
               field->at("span"));
        if (!parent && result.kind != "component" &&
            field->at("value").is_null())
          fail("resolve.interface", "port or bundle field requires a net type",
               field->at("span"));
        auto metadata = result.kind == "component"
                            ? reader.pin(*field, "pin")
                            : reader.read(field->at("fields"), "port");
        Interface interface {
          pin, nullptr, metadata, field->at("span"), {}
        };
        if (previous != result.interface.end()) {
          interface.type = previous->second.type;
          interface.metadata_order = previous->second.metadata_order;
          interface.metadata =
              merge_metadata(previous->second.metadata, metadata);
        } else {
          result.interface_order.push_back(pin);
          if (result.kind != "component")
            interface.type =
                object("value", field->at("value"), "span",
                       field->at("value").at("span"), "module", key.first);
        }
        std::vector<const Json *> pin_fields;
        for (const auto &entry : field->at("fields"))
          pin_fields.push_back(&entry);
        remember_order(pin_fields, interface.metadata_order);
        result.interface[pin] = std::move(interface);
      }
      if (result.interface.size() > max_pins)
        fail("resolve.limit", "interface exceeds configured pin limit",
             field->at("span"));
    }
    result.aliases.clear();
    std::map<std::string, Json> locations;
    for (const auto &pin : result.interface_order) {
      result.aliases[pin] = pin;
      locations[pin] = result.interface.at(pin).span;
    }
    for (const auto &pin : result.interface_order) {
      const auto &metadata = result.interface.at(pin).metadata;
      if (!metadata.contains("aliases"))
        continue;
      const auto &value = metadata.at("aliases");
      for (const auto &alias : value.at("value")) {
        std::string name = alias;
        if (result.aliases.contains(name) && result.aliases.at(name) != pin)
          fail("resolve.alias", "alias identifies multiple pins",
               value.at("span"),
               related("other pin or alias", locations.at(name)));
        result.aliases[name] = pin;
        locations[name] = value.at("span");
      }
    }
    auto metadata_fields = fields(source, "metadata", result.kind == "nettype");
    auto metadata = reader.read(field_array(metadata_fields),
                                result.kind == "nettype" ? "net" : result.kind);
    remember_order(metadata_fields, result.metadata_order);
    result.metadata = parent ? merge_metadata(parent->metadata, metadata)
                             : std::move(metadata);
    if (result.metadata.contains("value")) {
      const auto &value = result.metadata.at("value");
      result.parameters.part_value =
          ScopedExpression{value.at("value"), value.at("module")};
    }
    if (result.kind == "component") {
      current_loads(result);
      interchangeable(result);
    }
    if (result.metadata.contains("configurations")) {
      auto choices = result.metadata.contains("erc_choices")
                         ? result.metadata.at("erc_choices").at("value")
                         : Json::object();
      for (const auto &configuration_name :
           result.metadata_order.at({"configurations"})) {
        const auto &configuration = result.metadata.at("configurations")
                                        .at("value")
                                        .at(configuration_name);
        for (const auto &name :
             result.metadata_order.at({"configurations", configuration_name})) {
          const auto &selection = configuration.at("value").at(name);
          if (!choices.contains(name))
            fail("erc.unknown_choice", "unknown board ERC choice",
                 selection.at("span"));
          const auto &alternatives = choices.at(name).at("value");
          if (std::ranges::find(alternatives, selection.at("value")) ==
              alternatives.end())
            fail("erc.choice_value", "selection is outside alternatives",
                 selection.at("span"));
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
    for (const auto &[name, pin] : definition.interface)
      interfaces[name] = object("name", name, "type", pin.type, "metadata",
                                pin.metadata, "span", pin.span);
    for (const auto *node : definition.body)
      body.push_back(*node);
    auto parameters =
        parameter_summary({{key, definition.parameters}})[0].at("parameters");
    result.push_back(object(
        "module", key.first, "name", key.second, "kind", definition.kind,
        "parameters", std::move(parameters), "interface", std::move(interfaces),
        "metadata", definition.metadata, "aliases", definition.aliases, "body",
        std::move(body), "body_module", definition.body_module, "electrical",
        definition.electrical));
  }
  return result;
}
} // namespace noscheme
