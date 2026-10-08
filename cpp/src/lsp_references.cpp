#include "noscheme/lsp_references.hpp"
#include "noscheme/lsp_completion.hpp"
namespace noscheme {
namespace {
std::string dotted(const Json &names) {
  std::string result;
  for (const auto &name : names) {
    if (!result.empty())
      result += '.';
    result += name.at("text").get<std::string>();
  }
  return result;
}
std::string expression_name(const Json &expr) {
  if (!expr.is_object())
    return {};
  if (expr.value("node", "") == "Name")
    return expr.at("text");
  if (expr.value("node", "") == "Member") {
    auto base = expression_name(expr.at("base"));
    return base.empty()
               ? ""
               : base + "." + expr.at("name").at("text").get<std::string>();
  }
  return {};
}
Json path_span(const Json &names) {
  auto span = names.front().at("span");
  for (auto key : {"end_pos", "end_line", "end_column"})
    span[key] = names.back().at("span").at(key);
  return span;
}
std::string expression_text(const Json &expr) {
  if (expr.is_null())
    return {};
  if (!expr.is_object())
    return expr.dump();
  auto kind = expr.value("node", "");
  if (kind == "Name")
    return expr.at("text");
  if (kind == "Number" || kind == "String")
    return expr.at("spelling");
  if (kind == "Member")
    return expression_text(expr.at("base")) + "." +
           expr.at("name").at("text").get<std::string>();
  if (kind == "Subscript")
    return expression_text(expr.at("base")) +
           expression_text(expr.at("selection"));
  if (kind == "Index")
    return "[" + expression_text(expr.at("start")) +
           (expr.at("stop").is_null()
                ? ""
                : ":" + expression_text(expr.at("stop"))) +
           "]";
  if (expr.value("type", "") == "number") {
    return expr.at("value").is_string() ? expr.at("value").get<std::string>()
                                        : expr.at("value").dump();
  }
  return {};
}
std::string metadata_description(const Json &metadata) {
  std::string result;
  for (const auto *key :
       {"role", "unused", "aliases", "footprint", "value", "ref_prefix"}) {
    if (!metadata.contains(key))
      continue;
    const auto &wrapped = metadata.at(key);
    const auto &value = wrapped.at("value");
    auto text =
        value.is_string() ? value.get<std::string>() : expression_text(value);
    if (value.is_array() &&
        std::all_of(value.begin(), value.end(),
                    [](const Json &v) { return v.is_string(); })) {
      text.clear();
      for (const auto &v : value) {
        if (!text.empty())
          text += ", ";
        text += v.get<std::string>();
      }
    }
    if (text.empty())
      continue;
    if (text.size() > 256) {
      std::size_t end = 256;
      while (end && (static_cast<unsigned char>(text[end]) & 0xc0) == 0x80)
        --end;
      text = text.substr(0, end) + "…";
    }
    result += "\n\n" + std::string(key) + ": " + text;
    if (wrapped.contains("module"))
      result += " (from " + wrapped.at("module").get<std::string>() + ")";
  }
  return result;
}
std::string interface_description(const Interface &field, std::string label) {
  if (!field.type.is_null())
    label += "\n\nType: " + expression_text(field.type.at("value"));
  return label + metadata_description(field.metadata);
}
std::string parameter_description(const Parameter &param) {
  auto result = "parameter " + param.name + ": " + param.type;
  if (param.default_value) {
    auto value = expression_text(param.default_value->expression);
    if (!value.empty())
      result += "\n\nDefault: " + value;
  } else
    result += "\n\nRequired";
  return result;
}
using Targets = std::map<std::string, std::pair<Json, std::string>>;
} // namespace
std::vector<LspReference> reference_index(const Project &project,
                                          const Definitions &definitions,
                                          const RegistryDescription &registry) {
  std::vector<LspReference> refs;
  auto add = [&](const Json &span, const std::pair<Json, std::string> &target) {
    refs.push_back({span, target.first, target.second});
  };
  auto wrap_type = [](const Json &expr, const std::string &module) {
    return object("value", expr, "span", expr.at("span"), "module", module);
  };
  auto member = [&](const Json &type,
                    const std::string &name) -> const Interface * {
    if (type.is_null())
      return nullptr;
    auto definition = type_definition(project, registry, definitions, type);
    if (!definition || definition->kind != "bundle")
      return nullptr;
    auto found = definition->interface.find(name);
    return found == definition->interface.end() ? nullptr : &found->second;
  };
  Targets globals;
  std::map<std::string, Json> global_types;
  if (project.board.declaration)
    for (const auto &item : project.board.declaration->at("body"))
      if (item.value("node", "") == "Net" && item.value("global_", false)) {
        global_types[item.at("name").at("text")] =
            wrap_type(item.at("type"), project.board.module);
        globals[item.at("name").at("text")] = {
            item.at("name").at("span"),
            "global net " + item.at("name").at("text").get<std::string>()};
      }
  for (const auto &[module_name, module] : project.modules) {
    Targets symbols;
    for (const auto &[name, symbol] : module.symbols) {
      if (!symbol.declaration)
        continue;
      const auto &decl = *symbol.declaration;
      auto description = decl.at("kind").get<std::string>() + " " +
                         symbol.module + "." + symbol.name;
      if (auto found = definitions.find({symbol.module, symbol.name});
          found != definitions.end()) {
        const auto &definition = found->second;
        if (!definition.interface_order.empty()) {
          description += "\n\nInterface: ";
          bool first = true;
          for (const auto &pin : definition.interface_order) {
            if (!first)
              description += ", ";
            description += pin;
            first = false;
          }
        }
        if (!definition.parameters.parameters.empty()) {
          description += "\n\nParameters:";
          for (const auto &param : definition.parameters.parameters)
            description += "\n- " + param.name + ": " + param.type;
        }
      }
      if (auto found = definitions.find({symbol.module, symbol.name});
          found != definitions.end())
        description += metadata_description(found->second.metadata);
      symbols[name] = {decl.at("name").at("span"), description};
    }
    for (const auto &item : module.ast->at("items")) {
      if (item.at("node") == "Import") {
        for (const auto &imported : item.at("names")) {
          auto local = imported.at("alias").is_null() ? imported.at("name")
                                                      : imported.at("alias");
          auto found = symbols.find(local.at("text"));
          if (found != symbols.end()) {
            add(imported.at("name").at("span"), found->second);
            if (!imported.at("alias").is_null())
              add(local.at("span"), found->second);
          }
        }
        continue;
      }
      if (item.at("node") != "Definition")
        continue;
      auto name = item.at("name").at("text").get<std::string>();
      if (symbols.contains(name))
        add(item.at("name").at("span"), symbols.at(name));
      if (!item.at("base").is_null()) {
        auto base = dotted(item.at("base"));
        if (symbols.contains(base))
          add(path_span(item.at("base")), symbols.at(base));
      }
      Targets locals;
      std::map<std::string, Json> local_types;
      for (const auto &node : item.at("body")) {
        auto kind = node.value("node", "");
        if (kind == "Net" || kind == "Instance") {
          auto key = node.at("name").at("text").get<std::string>();
          if (kind == "Net" && !node.value("global_", false))
            local_types[key] = wrap_type(node.at("type"), module_name);
          locals[key] = {node.at("name").at("span"),
                         (kind == "Net" ? "net " : "inst ") + key + ": " +
                             (kind == "Net" ? expression_text(node.at("type"))
                                            : dotted(node.at("component")))};
        }
      }
      if (auto owner = definitions.find({module_name, name});
          owner != definitions.end()) {
        for (const auto &[pin, entry] : owner->second.interface) {
          locals[pin] = {entry.span,
                         interface_description(
                             entry, owner->second.kind == "bundle"
                                        ? "bundle field " + pin
                                        : "interface " + name + "." + pin)};
          local_types[pin] = entry.type;
        }
        for (const auto &[alias, pin] : owner->second.aliases) {
          locals[alias] = locals.at(pin);
          local_types[alias] = local_types.at(pin);
        }
        for (const auto &param : owner->second.parameters.parameters)
          locals[param.name] = {param.span, parameter_description(param)};
      }
      for (const auto &[key, target] : locals) {
        const auto &span = target.first;
        if (span.at("filename") == item.at("span").at("filename") &&
            span.at("start_pos") >= item.at("span").at("start_pos") &&
            span.at("end_pos") <= item.at("span").at("end_pos"))
          add(span, target);
      }
      std::function<Json(const Json &)> reference_type =
          [&](const Json &expr) -> Json {
        auto kind = expr.value("node", "");
        if (kind == "Subscript")
          return reference_type(expr.at("base"));
        if (kind == "Name") {
          auto found = local_types.find(expr.at("text"));
          return found == local_types.end() ? Json() : found->second;
        }
        if (kind == "Member") {
          const auto &base = expr.at("base");
          auto field = expr.at("name").at("text").get<std::string>();
          if (base.value("node", "") == "Name" && base.at("text") == "global") {
            auto found = global_types.find(field);
            return found == global_types.end() ? Json() : found->second;
          }
          if (auto found = member(reference_type(base), field))
            return found->type;
        }
        return nullptr;
      };
      std::function<void(const Json &)> walk = [&](const Json &node) {
        if (node.is_array()) {
          for (const auto &child : node)
            walk(child);
          return;
        }
        if (!node.is_object())
          return;
        auto kind = node.value("node", "");
        if (kind == "Name" || kind == "Member") {
          auto text = expression_name(node);
          if (text.starts_with("global.") && globals.contains(text.substr(7)))
            add(node.at("name").at("span"), globals.at(text.substr(7)));
          else if (locals.contains(text))
            add(node.at("span"), locals.at(text));
          else if (const auto *field =
                       kind == "Member"
                           ? member(reference_type(node.at("base")),
                                    node.at("name").at("text"))
                           : nullptr;
                   field) {
            walk(node.at("base"));
            add(node.at("name").at("span"),
                {field->span,
                 interface_description(*field, "bundle field " + field->name)});
          } else if (text.find('.') != std::string::npos &&
                     locals.contains(text.substr(0, text.find('.'))))
            walk(node.at("base"));
          else if (auto description =
                       builtin_description(module.symbols, registry, text);
                   !description.empty())
            add(node.at("span"), {nullptr, description});
          else if (symbols.contains(text))
            add(node.at("span"), symbols.at(text));
          else if (kind == "Member")
            walk(node.at("base"));
          return;
        }
        if (kind == "Section" &&
            (node.at("name") == "params" || node.at("name") == "defaults"))
          for (const auto &entry : node.at("entries"))
            if (entry.value("node", "") == "Field" &&
                entry.at("key").at("items").empty()) {
              const auto &key = entry.at("key").at("first");
              if (locals.contains(key.at("text")))
                add(key.at("span"), locals.at(key.at("text")));
            }
        if (kind == "FieldKey" || kind == "PinKey" || kind == "Endpoint")
          return;
        if (kind == "Instance") {
          auto type = dotted(node.at("component"));
          if (symbols.contains(type))
            add(path_span(node.at("component")), symbols.at(type));
          // Explicit connection keys name public pins of the instantiated type.
          auto binding = module.symbols.find(type);
          if (binding != module.symbols.end()) {
            auto def = definitions.find(
                {binding->second.module, binding->second.name});
            if (def != definitions.end()) {
              auto pin_reference = [&](const Json &key) -> const Interface * {
                auto name = key.at("text").get<std::string>();
                if (def->second.aliases.contains(name))
                  name = def->second.aliases.at(name);
                auto found = def->second.interface.find(name);
                if (found == def->second.interface.end())
                  return nullptr;
                add(key.at("span"),
                    {found->second.span,
                     interface_description(found->second,
                                           "interface " + type + "." + name)});
                refs.back().instance = node.at("span");
                refs.back().pin = name;
                return &found->second;
              };
              for (const auto &section : node.at("sections")) {
                if (section.at("name") == "params") {
                  for (const auto &entry : section.at("entries")) {
                    const auto &key = entry.value("node", "") == "Argument"
                                          ? entry.at("name")
                                          : entry.at("key").at("first");
                    for (const auto &param : def->second.parameters.parameters)
                      if (param.name == key.at("text").get<std::string>())
                        add(key.at("span"),
                            {param.span, parameter_description(param)});
                  }
                }
                if (section.at("name") == "metadata")
                  for (const auto &field : section.at("entries"))
                    if (field.at("key").at("first").at("text") == "pins")
                      for (const auto &overlay : field.at("fields")) {
                        const auto &key = overlay.at("key");
                        if (key.at("items").empty()) {
                          pin_reference(key.at("first"));
                          if (!key.at("last").is_null())
                            pin_reference(key.at("last"));
                        } else
                          for (const auto &item : key.at("items")) {
                            pin_reference(item.at("first"));
                            if (!item.at("last").is_null())
                              pin_reference(item.at("last"));
                          }
                      }
                if (section.at("name") != "connections")
                  continue;
                for (const auto &entry : section.at("entries")) {
                  if (entry.value("node", "") != "Binding")
                    continue;
                  const auto &endpoint = entry.at("endpoint");
                  const auto &head = endpoint.at("head");
                  if (!head.at("items").empty()) {
                    for (const auto &item : head.at("items")) {
                      pin_reference(item.at("first"));
                      if (!item.at("last").is_null())
                        pin_reference(item.at("last"));
                    }
                    walk(endpoint.at("instance_indices"));
                    continue;
                  }
                  auto selected = pin_reference(head.at("first"));
                  if (!head.at("last").is_null()) {
                    pin_reference(head.at("last"));
                    walk(endpoint.at("instance_indices"));
                    continue;
                  }
                  if (selected) {
                    const auto &pin = *selected;
                    auto value = pin.type;
                    for (const auto &selector : endpoint.at("selectors")) {
                      if (selector.value("node", "") == "Index") {
                        walk(selector);
                        continue;
                      }
                      const auto *field = member(value, selector.at("text"));
                      if (!field)
                        break;
                      add(selector.at("span"),
                          {field->span,
                           interface_description(*field, "bundle field " +
                                                             field->name)});
                      value = field->type;
                    }
                    walk(endpoint.at("instance_indices"));
                  }
                }
              }
            }
          }
        }
        if ((kind == "Net" || kind == "Instance") &&
            locals.contains(node.at("name").at("text")))
          add(node.at("name").at("span"),
              locals.at(node.at("name").at("text")));
        for (const auto &[key, value] : node.items())
          if (key != "span" && key != "name" && key != "component")
            walk(value);
      };
      walk(item.at("body"));
    }
  }
  // Resolver spans include metadata bodies. Editor targets should cover the
  // declared name, so references and source edits never include that body.
  std::map<std::string, Json> names, typed_names;
  std::function<void(const Json &)> declarations = [&](const Json &value) {
    if (value.is_array()) {
      for (const auto &v : value)
        declarations(v);
    } else if (value.is_object()) {
      if (value.value("node", "") == "Field") {
        const auto &key = value.at("key");
        if (key.at("items").empty() && key.at("last").is_null()) {
          names[value.at("span").dump()] = key.at("first").at("span");
          if (!value.at("value").is_null())
            typed_names[value.at("value").at("span").dump()] =
                key.at("first").at("span");
        }
      }
      for (const auto &[k, v] : value.items())
        if (k != "span")
          declarations(v);
    }
  };
  for (const auto &[name, module] : project.modules)
    declarations(*module.ast);
  auto targets = names;
  for (const auto &[key, definition] : definitions)
    if (definition.kind != "component")
      for (const auto &[name, field] : definition.interface)
        if (!field.type.is_null())
          if (auto found = typed_names.find(field.type.at("span").dump());
              found != typed_names.end())
            targets[field.span.dump()] = found->second;
  for (auto &reference : refs) {
    if (auto found = names.find(reference.span.dump()); found != names.end())
      reference.span = found->second;
    if (auto found = targets.find(reference.target.dump());
        found != targets.end())
      reference.target = found->second;
  }
  return refs;
}
void elaborate_hover(std::vector<LspReference> &references,
                     const Project &project, const Json &circuit) {
  using Key = std::pair<std::string, std::size_t>;
  auto key = [](const Json &span) -> Key {
    return {span.at("filename"), span.at("start_pos")};
  };
  std::map<Key, std::vector<const Json *>> records;
  for (auto category : {"parts", "blocks"})
    for (const auto &record : circuit.at(category))
      records[key(record.at("source").at("declaration"))].push_back(&record);
  auto brief = [](const Json &value) {
    auto text = value.is_string() ? value.get<std::string>() : value.dump();
    std::replace(text.begin(), text.end(), '\n', ' ');
    std::replace(text.begin(), text.end(), '\r', ' ');
    if (text.size() > 180) {
      std::size_t end = 180;
      while (end && (static_cast<unsigned char>(text[end]) & 0xc0) == 0x80)
        --end;
      text = text.substr(0, end) + "…";
    }
    return text;
  };
  for (auto &reference : references) {
    if (reference.instance.is_null())
      continue;
    auto found = records.find(key(reference.instance));
    if (found == records.end())
      continue;
    std::set<std::string> seen;
    for (const auto *record : found->second) {
      if (!record->contains("pins") ||
          !record->at("pins").contains(reference.pin))
        continue;
      const auto &pin = record->at("pins").at(reference.pin);
      const auto &metadata = pin.at("metadata");
      auto role = metadata.contains("role")
                      ? brief(metadata.at("role").at("value"))
                      : "unspecified";
      if (metadata.contains("role") &&
          metadata.at("role").at("value").is_array()) {
        role.clear();
        for (const auto &value : metadata.at("role").at("value")) {
          if (!role.empty())
            role += ", ";
          role += brief(value);
        }
      }
      auto net =
          pin.at("net").is_null() ? "(unconnected)" : brief(pin.at("net"));
      auto detail = "Effective role: " + role + "; net: " + net;
      if (seen.insert(detail).second)
        reference.description += "\n\n" + detail;
      if (seen.size() == 4)
        break;
    }
  }
  std::map<Key, std::string> details;
  for (const auto &[name, module] : project.modules)
    for (const auto &definition : module.ast->at("items")) {
      if (definition.value("node", "") != "Definition")
        continue;
      for (const auto &node : definition.at("body")) {
        if (node.value("node", "") != "Instance")
          continue;
        auto found = records.find(key(node.at("span")));
        if (found == records.end())
          continue;
        auto &description = details[key(node.at("name").at("span"))];
        description =
            "\n\nElaborated instances: " + std::to_string(found->second.size());
        for (std::size_t i = 0;
             i < std::min<std::size_t>(4, found->second.size()); ++i) {
          const auto &record = *found->second[i];
          description += "\n\nInstance: " + brief(record.at("identity"));
          if (record.contains("value") && !record.at("value").is_null())
            description +=
                "\n\nValue: " + brief(record.at("value").at("value"));
          if (record.contains("metadata") &&
              record.at("metadata").contains("footprint"))
            description +=
                "\n\nFootprint: " +
                brief(record.at("metadata").at("footprint").at("value"));
          if (record.contains("parameters")) {
            std::size_t count = 0;
            for (const auto &[param, value] : record.at("parameters").items()) {
              if (count++ == 6)
                break;
              description +=
                  "\n\nParameter " + param + ": " + brief(value.at("value"));
            }
          }
        }
        if (found->second.size() > 4)
          description += "\n\n… " + std::to_string(found->second.size() - 4) +
                         " more instances";
      }
    }
  for (auto &reference : references)
    if (!reference.target.is_null())
      if (auto found = details.find(key(reference.target));
          found != details.end())
        reference.description += found->second;
}
} // namespace noscheme
