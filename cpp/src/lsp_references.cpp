#include "noscheme/lsp_references.hpp"
#include "noscheme/lsp_completion.hpp"
#include <tuple>
namespace noscheme {
namespace {
using ast::as;
using ast::Kind;
std::string expression_text(const ast::Node &expr) {
  switch (expr.kind) {
  case Kind::Name:
    return as<ast::Name>(expr).text;
  case Kind::Number:
    return as<ast::Number>(expr).spelling;
  case Kind::String:
    return as<ast::String>(expr).spelling;
  case Kind::Member: {
    const auto &member = as<ast::Member>(expr);
    return expression_text(*member.base) + "." + member.name.text;
  }
  case Kind::Subscript: {
    const auto &subscript = as<ast::Subscript>(expr);
    return expression_text(*subscript.base) +
           expression_text(subscript.selection);
  }
  case Kind::Index: {
    const auto &index = as<ast::Index>(expr);
    return "[" + expression_text(*index.start) +
           (index.stop ? ":" + expression_text(*index.stop) : "") + "]";
  }
  default:
    return {};
  }
}
// Strings as written, numbers in decimal, and expressions as source text.
std::string value_text(const MetaValue &value) {
  if (auto *expression = value.expression())
    return expression_text(*expression);
  if (auto *integer = value.get_if<Integer>())
    return integer->str();
  if (auto *floating = value.get_if<double>())
    return Json(*floating).dump();
  if (value.is<bool>() || value.is<MetaList>())
    return to_json(value).dump();
  return {};
}
Span path_span(const std::vector<ast::Name> &names) {
  return {names.front().span.source, names.front().span.start,
          names.back().span.end};
}
std::string metadata_description(const MetaFields &metadata) {
  std::string result;
  for (const auto *key :
       {"role", "unused", "aliases", "footprint", "value", "ref_prefix"}) {
    auto found = metadata.find(key);
    if (found == metadata.end())
      continue;
    const auto &value = found->second.value;
    auto text = value.is<std::string>() ? value.string() : value_text(value);
    if (auto *list = value.get_if<MetaList>();
        list && std::ranges::all_of(*list, [](const MetaValue &v) {
          return v.is<std::string>();
        })) {
      text.clear();
      for (const auto &v : *list) {
        if (!text.empty())
          text += ", ";
        text += v.string();
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
    result += "\n\n" + std::string(key) + ": " + text + " (from " +
              found->second.module + ")";
  }
  return result;
}
std::string interface_description(const Interface &field, std::string label) {
  if (field.type.expression)
    label += "\n\nType: " + expression_text(*field.type.expression);
  return label + metadata_description(field.metadata);
}
std::string parameter_description(const Parameter &param) {
  auto result = "parameter " + param.name + ": " + param.type;
  if (param.default_value) {
    auto value = expression_text(*param.default_value->expression);
    if (!value.empty())
      result += "\n\nDefault: " + value;
  } else
    result += "\n\nRequired";
  return result;
}
using Target = std::pair<Span, std::string>;
using Targets = std::map<std::string, Target>;
// Spans within one project share their module's Source.
using SpanKey = std::tuple<const Source *, std::size_t, std::size_t>;
SpanKey span_key(const Span &span) {
  return {span.source, span.start, span.end};
}
void visit_all(const ast::Node &node,
               const std::function<void(const ast::Node &)> &visit) {
  visit(node);
  ast::for_each_child(node, [&](std::string_view, const ast::Node &child) {
    visit_all(child, visit);
  });
}
} // namespace
std::vector<LspReference> reference_index(const Project &project,
                                          const Definitions &definitions,
                                          const RegistryDescription &registry) {
  // Grouped fields share declaration spans, but navigation selects one name.
  std::map<std::pair<SpanKey, std::string>, Span> declared_names,
      typed_declared_names;
  for (const auto &[name, module] : project.modules)
    visit_all(*module.ast, [&](const ast::Node &node) {
      if (auto *field = ast::as<ast::Field>(&node);
          field && field->key.items.empty() && !field->key.last) {
        if (field->value)
          typed_declared_names[{span_key(field->value->span),
                                field->key.first.text}] = field->key.first.span;
        declared_names[{span_key(field->span), field->key.first.text}] =
            field->key.first.span;
      }
    });
  auto declared_name = [&](const Span &span, const std::string &name) {
    auto found = declared_names.find({span_key(span), name});
    return found == declared_names.end() ? span : found->second;
  };
  auto interface_name = [&](const Interface &field) {
    // An inherited interface override keeps the original type declaration.
    if (field.type.expression) {
      auto found = typed_declared_names.find(
          {span_key(field.type.expression->span), field.name});
      if (found != typed_declared_names.end())
        return found->second;
    }
    return declared_name(field.span, field.name);
  };
  std::vector<LspReference> refs;
  auto add = [&](const Span &span, const Target &target) {
    refs.push_back({span, target.first, target.second});
  };
  auto member = [&](const ScopedExpression &type,
                    const std::string &name) -> const Interface * {
    if (!type.expression)
      return nullptr;
    auto definition = type_definition(project, registry, definitions, type);
    if (!definition || definition->kind != "bundle")
      return nullptr;
    auto found = definition->interface.find(name);
    return found == definition->interface.end() ? nullptr : &found->second;
  };
  Targets globals;
  std::map<std::string, ScopedExpression> global_types;
  const auto root =
      definitions.find({project.board.module, project.board.name});
  if (root != definitions.end())
    for (const auto *item : root->second.body)
      if (auto *net = as<ast::Net>(item); net && net->global) {
        global_types[net->name.text] = {net->type.get(),
                                        root->second.body_module};
        globals[net->name.text] = {net->name.span,
                                   "global net " + net->name.text};
      }
  for (const auto &[module_name, module] : project.modules) {
    Targets symbols;
    for (const auto &[name, symbol] : module.symbols) {
      if (!symbol.declaration)
        continue;
      const auto &decl = *symbol.declaration;
      auto description = decl.keyword + " " + symbol.module + "." + symbol.name;
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
        description += metadata_description(definition.metadata);
      }
      symbols[name] = {decl.name.span, description};
    }
    for (const auto &entry : module.ast->items) {
      if (auto *import = as<ast::Import>(entry)) {
        for (const auto &imported : import->names) {
          const auto &local = imported.local();
          auto found = symbols.find(local.text);
          if (found != symbols.end()) {
            add(imported.name.span, found->second);
            if (imported.alias)
              add(local.span, found->second);
          }
        }
        continue;
      }
      const auto *item = as<ast::Definition>(entry);
      if (!item)
        continue;
      const auto &name = item->name.text;
      if (symbols.contains(name))
        add(item->name.span, symbols.at(name));
      if (!item->base.empty()) {
        auto base = ast::dotted(item->base);
        if (symbols.contains(base))
          add(path_span(item->base), symbols.at(base));
      }
      Targets locals;
      std::map<std::string, ScopedExpression> local_types;
      for (const auto &node : item->body) {
        if (auto *net = as<ast::Net>(node)) {
          const auto &key = net->name.text;
          if (!net->global)
            local_types[key] = {net->type.get(), module_name};
          locals[key] = {net->name.span,
                         "net " + key + ": " + expression_text(*net->type)};
        } else if (auto *instance = as<ast::Instance>(node)) {
          const auto &key = instance->name.text;
          locals[key] = {instance->name.span,
                         "inst " + key + ": " +
                             ast::dotted(instance->component)};
        }
      }
      if (auto owner = definitions.find({module_name, name});
          owner != definitions.end()) {
        for (const auto &[pin, entry] : owner->second.interface) {
          locals[pin] = {declared_name(entry.span, pin),
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
          locals[param.name] = {declared_name(param.span, param.name),
                                parameter_description(param)};
      }
      for (const auto &[key, target] : locals) {
        const auto &span = target.first;
        if (span.filename() == item->span.filename() &&
            span.start >= item->span.start && span.end <= item->span.end)
          add(span, target);
      }
      std::function<ScopedExpression(const ast::Node &)> reference_type =
          [&](const ast::Node &expr) -> ScopedExpression {
        if (auto *subscript = as<ast::Subscript>(&expr))
          return reference_type(*subscript->base);
        if (auto *n = as<ast::Name>(&expr)) {
          auto found = local_types.find(n->text);
          return found == local_types.end() ? ScopedExpression{}
                                            : found->second;
        }
        if (auto *m = as<ast::Member>(&expr)) {
          const auto &field = m->name.text;
          if (auto *base = as<ast::Name>(m->base);
              base && base->text == "global") {
            auto found = global_types.find(field);
            return found == global_types.end() ? ScopedExpression{}
                                               : found->second;
          }
          if (auto found = member(reference_type(*m->base), field))
            return found->type;
        }
        return {};
      };
      std::function<void(const ast::Node &)> walk = [&](const ast::Node &node) {
        auto kind = node.kind;
        if (kind == Kind::Name || kind == Kind::Member) {
          auto *m = as<ast::Member>(&node);
          auto text = ast::expression_path(node);
          if (text.starts_with("global.") && globals.contains(text.substr(7)))
            add(m->name.span, globals.at(text.substr(7)));
          else if (locals.contains(text))
            add(node.span, locals.at(text));
          else if (const auto *field =
                       m ? member(reference_type(*m->base), m->name.text)
                         : nullptr;
                   field) {
            walk(*m->base);
            add(m->name.span,
                {interface_name(*field),
                 interface_description(*field, "bundle field " + field->name)});
          } else if (text.find('.') != std::string::npos &&
                     locals.contains(text.substr(0, text.find('.'))))
            walk(*m->base);
          else if (auto description =
                       builtin_description(module.symbols, registry, text);
                   !description.empty())
            add(node.span, {Span{}, description});
          else if (symbols.contains(text))
            add(node.span, symbols.at(text));
          else if (m)
            walk(*m->base);
          return;
        }
        if (auto *section = as<ast::Section>(&node);
            section &&
            (section->name == "params" || section->name == "defaults"))
          for (const auto *entry : ast::field_list(section->entries))
            if (entry->key.items.empty()) {
              const auto &key = entry->key.first;
              if (locals.contains(key.text))
                add(key.span, locals.at(key.text));
            }
        // A pin's aliases, `1 (signal):`, also name that pin.
        if (auto *key = as<ast::FieldKey>(&node))
          for (const auto &alias : key->aliases)
            if (locals.contains(alias.text))
              add(alias.span, locals.at(alias.text));
        if (kind == Kind::FieldKey || kind == Kind::PinKey ||
            kind == Kind::Endpoint)
          return;
        if (auto *instance = as<ast::Instance>(&node)) {
          auto type = ast::dotted(instance->component);
          if (symbols.contains(type))
            add(path_span(instance->component), symbols.at(type));
          // Explicit connection keys name public pins of the instantiated type.
          auto binding = module.symbols.find(type);
          if (binding != module.symbols.end()) {
            auto def = definitions.find(
                {binding->second.module, binding->second.name});
            if (def != definitions.end()) {
              auto pin_reference =
                  [&](const ast::PinKey &key) -> const Interface * {
                auto name = key.text;
                if (def->second.aliases.contains(name))
                  name = def->second.aliases.at(name);
                auto found = def->second.interface.find(name);
                if (found == def->second.interface.end())
                  return nullptr;
                add(key.span,
                    {interface_name(found->second),
                     interface_description(found->second,
                                           "interface " + type + "." + name)});
                refs.back().instance = instance->span;
                refs.back().pin = name;
                return &found->second;
              };
              auto key_references = [&](const ast::FieldKey &key) {
                auto range = [&](const ast::FieldKey &item) {
                  pin_reference(item.first);
                  if (item.last)
                    pin_reference(*item.last);
                };
                if (key.items.empty())
                  range(key);
                else
                  for (const auto &item : key.items)
                    range(item);
              };
              auto walk_indices = [&](const ast::Endpoint &endpoint) {
                for (const auto &index : endpoint.instance_indices)
                  walk(index);
              };
              for (const auto &section : instance->sections) {
                if (section.name == "params") {
                  for (const auto &entry : section.entries) {
                    auto *argument = as<ast::Argument>(entry);
                    const auto &text =
                        argument ? argument->name.text
                                 : as<ast::Field>(*entry).key.first.text;
                    const auto &span =
                        argument ? argument->name.span
                                 : as<ast::Field>(*entry).key.first.span;
                    for (const auto &param : def->second.parameters.parameters)
                      if (param.name == text)
                        add(span, {declared_name(param.span, param.name),
                                   parameter_description(param)});
                  }
                }
                if (section.name == "metadata")
                  for (const auto *field : ast::field_list(section.entries))
                    if (field->key.first.text == "pins")
                      for (const auto &overlay : field->fields)
                        key_references(overlay.key);
                if (section.name != "connections")
                  continue;
                for (const auto &entry : section.entries) {
                  auto *connection = as<ast::Binding>(entry);
                  if (!connection)
                    continue;
                  const auto &endpoint = connection->endpoint;
                  const auto &head = endpoint.head;
                  if (!head.items.empty()) {
                    key_references(head);
                    walk_indices(endpoint);
                    continue;
                  }
                  auto selected = pin_reference(head.first);
                  if (head.last) {
                    pin_reference(*head.last);
                    walk_indices(endpoint);
                    continue;
                  }
                  if (selected) {
                    auto value = selected->type;
                    for (const auto &selector : endpoint.selectors) {
                      if (selector->kind == Kind::Index) {
                        walk(*selector);
                        continue;
                      }
                      const auto &field_name = as<ast::Name>(*selector);
                      const auto *field = member(value, field_name.text);
                      if (!field)
                        break;
                      add(field_name.span,
                          {interface_name(*field),
                           interface_description(*field, "bundle field " +
                                                             field->name)});
                      value = field->type;
                    }
                    walk_indices(endpoint);
                  }
                }
              }
            }
          }
        }
        const ast::Name *declared = nullptr;
        if (auto *net = as<ast::Net>(&node))
          declared = &net->name;
        else if (auto *instance = as<ast::Instance>(&node))
          declared = &instance->name;
        if (declared && locals.contains(declared->text))
          add(declared->span, locals.at(declared->text));
        ast::for_each_child(node,
                            [&](std::string_view key, const ast::Node &child) {
                              if (key != "name" && key != "component")
                                walk(child);
                            });
      };
      for (const auto &node : item->body)
        walk(*node);
    }
  }
  // Resolver spans include metadata bodies. Editor targets should cover the
  // declared name, so references and source edits never include that body.
  std::map<SpanKey, Span> names, typed_names;
  for (const auto &[name, module] : project.modules)
    visit_all(*module.ast, [&](const ast::Node &node) {
      auto *field = as<ast::Field>(&node);
      if (!field || !field->key.items.empty() || field->key.last)
        return;
      names[span_key(field->span)] = field->key.first.span;
      if (field->value)
        typed_names[span_key(field->value->span)] = field->key.first.span;
    });
  auto targets = names;
  for (const auto &[key, definition] : definitions)
    if (definition.kind != "component")
      for (const auto &[name, field] : definition.interface)
        if (field.type.expression)
          if (auto found =
                  typed_names.find(span_key(field.type.expression->span));
              found != typed_names.end())
            targets[span_key(field.span)] = found->second;
  for (const auto &[key, definition] : definitions)
    if (definition.kind != "component")
      for (const auto &[name, field] : definition.interface)
        targets[span_key(declared_name(field.span, name))] =
            interface_name(field);
  for (auto &reference : refs) {
    if (auto found = names.find(span_key(reference.span)); found != names.end())
      reference.span = found->second;
    if (auto found = targets.find(span_key(reference.target));
        found != targets.end())
      reference.target = found->second;
  }
  return refs;
}
void elaborate_hover(std::vector<LspReference> &references,
                     const Project &project, const Circuit &circuit) {
  using Key = std::pair<std::string, std::size_t>;
  auto key = [](const Span &span) -> Key {
    return {span.filename(), span.start};
  };
  // Parts and blocks by declaration; pin-bearing parts carry their pins.
  struct Record {
    const Part *part = nullptr;
    const Block *block = nullptr;
  };
  std::map<Key, std::vector<Record>> records;
  for (const auto &part : circuit.parts)
    records[key(part.declaration)].push_back({&part, nullptr});
  for (const auto &block : circuit.blocks)
    records[key(block.declaration)].push_back({nullptr, &block});
  auto brief = [](std::string text) {
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
  // Hover shows a string as written and other values in their JSON form.
  auto shown = [&](const Json &value) {
    return brief(value.is_string() ? value.get<std::string>() : value.dump());
  };
  auto number = [&](const Value &value) {
    return shown(encoded(value).at("value"));
  };
  for (auto &reference : references) {
    if (!reference.instance)
      continue;
    auto found = records.find(key(reference.instance));
    if (found == records.end())
      continue;
    std::set<std::string> seen;
    for (const auto &record : found->second) {
      if (!record.part || !record.part->pins.contains(reference.pin))
        continue;
      const auto &pin = circuit.leaves[record.part->pins.at(reference.pin)];
      auto *roles = meta_value(pin.metadata, "role");
      auto role = roles ? shown(to_json(*roles)) : "unspecified";
      if (auto *list = roles ? roles->get_if<MetaList>() : nullptr) {
        role.clear();
        for (const auto &value : *list) {
          if (!role.empty())
            role += ", ";
          role += shown(to_json(value));
        }
      }
      auto net = pin.net ? brief(circuit.nets[*pin.net].name) : "(unconnected)";
      auto detail = "Effective role: " + role + "; net: " + net;
      if (seen.insert(detail).second)
        reference.description += "\n\n" + detail;
      if (seen.size() == 4)
        break;
    }
  }
  std::map<Key, std::string> details;
  for (const auto &[name, module] : project.modules)
    for (const auto &entry : module.ast->items) {
      auto *definition = as<ast::Definition>(entry);
      if (!definition)
        continue;
      for (const auto &node : definition->body) {
        auto *instance = as<ast::Instance>(node);
        if (!instance)
          continue;
        auto found = records.find(key(instance->span));
        if (found == records.end())
          continue;
        auto &description = details[key(instance->name.span)];
        description =
            "\n\nElaborated instances: " + std::to_string(found->second.size());
        for (std::size_t i = 0;
             i < std::min<std::size_t>(4, found->second.size()); ++i) {
          const auto &record = found->second[i];
          description +=
              "\n\nInstance: " + brief(record.part ? record.part->identity
                                                   : record.block->identity);
          if (!record.part)
            continue;
          const auto &part = *record.part;
          if (part.value)
            description += "\n\nValue: " + number(*part.value);
          if (auto *footprint = meta_value(part.metadata, "footprint"))
            description += "\n\nFootprint: " + shown(to_json(*footprint));
          std::size_t count = 0;
          for (const auto &[param, value] : part.parameters) {
            if (count++ == 6)
              break;
            description += "\n\nParameter " + param + ": " + number(value);
          }
        }
        if (found->second.size() > 4)
          description += "\n\n… " + std::to_string(found->second.size() - 4) +
                         " more instances";
      }
    }
  for (auto &reference : references)
    if (reference.target)
      if (auto found = details.find(key(reference.target));
          found != details.end())
        reference.description += found->second;
}
} // namespace noscheme
