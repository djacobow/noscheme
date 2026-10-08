#include "noscheme/lsp_completion.hpp"
#include "noscheme/lsp_completion_scan.hpp"
#include "noscheme/lsp_erc_completion.hpp"
#include "noscheme/lsp_instance_completion.hpp"
#include "noscheme/validation.hpp"
#include <algorithm>
#include <set>
namespace noscheme {
namespace {
using completion_scan::name_char;
bool name_start(char c) { return name_char(c) && !(c >= '0' && c <= '9'); }
// Keep array selections in the replacement range but omit them when matching
// the declared element interface. Balanced selections may contain expressions.
std::size_t reference_start(const std::string &text, std::size_t end) {
  auto start = end;
  int depth = 0;
  while (start) {
    auto c = text[start - 1];
    if (c == ']')
      ++depth;
    else if (c == '[' && depth)
      --depth;
    else if (!depth && !name_char(c) && c != '.')
      break;
    if (c == '\n' || c == '\r')
      break;
    --start;
  }
  return depth ? end : start;
}
std::string without_indices(const std::string &text) {
  std::string result;
  int depth = 0;
  for (auto c : text) {
    if (c == '[')
      ++depth;
    else if (c == ']')
      --depth;
    else if (!depth)
      result += c;
  }
  if (result.starts_with('.'))
    result.erase(0, 1);
  return result;
}
} // namespace
std::string code_mask(const std::string &text, bool preserve_strings) {
  auto result = text;
  enum { code, line, block, string } state = code;
  for (std::size_t i = 0; i < text.size(); ++i) {
    char c = text[i], next = i + 1 < text.size() ? text[i + 1] : '\0';
    if (state == code) {
      if (c == '/' && (next == '/' || next == '*')) {
        state = next == '/' ? line : block;
        result[i] = result[i + 1] = ' ';
        ++i;
      } else if (c == '"') {
        state = string;
        if (!preserve_strings)
          result[i] = ' ';
      }
    } else {
      if (c != '\n' && c != '\r' && (state != string || !preserve_strings))
        result[i] = ' ';
      if (state == block && c == '*' && next == '/') {
        result[++i] = ' ';
        state = code;
      } else if (state == line && c == '\n')
        state = code;
      else if (state == string) {
        if (c == '\\' && next && next != '\n' && next != '\r') {
          ++i;
          if (!preserve_strings)
            result[i] = ' ';
        } else if (c == '"' || c == '\n' || c == '\r')
          state = code;
      }
    }
  }
  return result;
}
namespace {
struct FootprintValue {
  std::size_t start, end;
  bool closed;
};
// Lexical detection only: the AST catalog below decides whether this is a
// component/instance metadata field, excluding custom and pin metadata.
std::vector<FootprintValue> footprint_values(const std::string &text) {
  auto mask = code_mask(text);
  std::vector<FootprintValue> values;
  for (std::size_t line = 0; line < text.size();) {
    auto end = text.find_first_of("\r\n", line);
    if (end == std::string::npos)
      end = text.size();
    auto key = line;
    while (key < end && (text[key] == ' ' || text[key] == '\t'))
      ++key;
    if (mask.compare(key, 10, "footprint:") == 0) {
      auto start = key + 10;
      while (start < end && (text[start] == ' ' || text[start] == '\t'))
        ++start;
      if (start == end || text.compare(start, 2, "//") == 0 ||
          text[start] == '"') {
        if (start == end || text.compare(start, 2, "//") == 0) {
          values.push_back({start, start, false});
        } else if (text[start] == '"') {
          auto stop = start + 1;
          while (stop < end) {
            if (text[stop] == '\\') {
              stop += std::min<std::size_t>(2, end - stop);
              continue;
            }
            if (text[stop] == '"')
              break;
            ++stop;
          }
          bool closed = stop < end;
          if (closed)
            ++stop;
          values.push_back({start, stop, closed});
        }
      }
    }
    line = end + 1;
  }
  return values;
}
} // namespace
std::string completion_recovery(const std::string &text,
                                std::vector<std::size_t> *offsets,
                                bool recover_calls) {
  if (offsets) {
    offsets->clear();
    offsets->push_back(0);
  }
  auto mask = code_mask(text);
  std::map<std::size_t, TextRepair> repairs;
  for (const auto &value : footprint_values(text))
    if (!value.closed)
      repairs[value.start] = {value.start, value.end, "\"\""};
  if (recover_calls) {
    for (const auto &repair : erc_call_repairs(text))
      repairs[repair.start] = repair;
    for (const auto &repair : instance_repairs(text))
      repairs[repair.start] = repair;
    for (const auto &repair : section_repairs(text))
      repairs[repair.start] = repair;
  }
  std::string result;
  for (std::size_t i = 0; i <= text.size(); ++i) {
    if (auto repair = repairs.find(i); repair != repairs.end()) {
      result += repair->second.text;
      if (offsets) {
        offsets->insert(offsets->end(), repair->second.text.size(), i);
        if (!repair->second.text.empty())
          offsets->back() = repair->second.end;
      }
      i = repair->second.end;
    }
    if (i == text.size())
      break;
    result += text[i];
    if (offsets)
      offsets->push_back(i + 1);
    if (mask[i] != '.' || !i || (!name_char(mask[i - 1]) && mask[i - 1] != ']'))
      continue;
    if (i + 1 < mask.size() && (name_char(mask[i + 1]) || mask[i + 1] == '.'))
      continue;
    auto begin = reference_start(mask, i);
    if (begin < i && (name_start(mask[begin]) || mask[begin] == '[')) {
      constexpr std::string_view placeholder = "_noscheme_completion";
      result += placeholder;
      if (offsets)
        offsets->insert(offsets->end(), placeholder.size(), i + 1);
    }
  }
  return result;
}
std::string builtin_description(const Symbols &symbols,
                                const RegistryDescription &registry,
                                const std::string &name) {
  std::string qualified;
  if (auto found = symbols.find(name);
      found != symbols.end() && found->second.module == "<erc>")
    qualified = found->second.name;
  auto separator = name.rfind('.');
  if (separator != std::string::npos) {
    auto prefix = symbols.find(name.substr(0, separator));
    if (prefix != symbols.end() && prefix->second.module == "noscheme" &&
        prefix->second.name == "std")
      qualified = "std." + name.substr(separator + 1);
  }
  if (!qualified.empty()) {
    if (registry.data.at("rules").contains(qualified)) {
      const auto &rule = registry.data.at("rules").at(qualified);
      return rule.at("signature").get<std::string>() +
             "\n\nScope: " + rule.at("scope").get<std::string>() +
             "\n\nWaivable: " +
             (rule.at("waivable").get<bool>() ? "yes" : "no");
    }
    if (std::find(registry.data.at("predicates").begin(),
                  registry.data.at("predicates").end(),
                  Json(qualified)) != registry.data.at("predicates").end())
      return "ERC predicate " + qualified;
    return {};
  }
  auto dot = name.rfind('.');
  auto prefix = dot == std::string::npos ? name : name.substr(0, dot);
  auto found = symbols.find(prefix);
  if (found == symbols.end() || found->second.module != "noscheme")
    return {};
  const auto &space = found->second.name;
  if (!registry.data.at("enums").contains(space))
    return {};
  const auto &values = registry.data.at("enums").at(space);
  if (dot == std::string::npos)
    return space + " namespace";
  auto member = name.substr(dot + 1);
  if (std::find(values.begin(), values.end(), Json(member)) == values.end())
    return {};
  auto result = space + "." + member;
  if (registry.data.contains("help") &&
      registry.data.at("help").contains(space) &&
      registry.data.at("help").at(space).contains(member))
    result += "\n\n" +
              registry.data.at("help").at(space).at(member).get<std::string>();
  return result;
}
CompletionCatalog completion_catalog(const Project &project,
                                     const RegistryDescription &registry) {
  CompletionCatalog catalog;
  for (const auto &[module_name, module] : project.modules) {
    auto &entries = catalog[module.path];
    auto add_rule = [&](const std::string &name, const std::string &canonical) {
      CompletionEntry entry{
          name, builtin_description(module.symbols, registry, name), 3};
      if (registry.data.at("rules").contains(canonical))
        entry.rule = registry.data.at("rules").at(canonical);
      entries.push_back(std::move(entry));
    };
    for (const auto &[name, symbol] : module.symbols) {
      if (symbol.declaration)
        entries.push_back({name,
                           symbol.declaration->at("kind").get<std::string>() +
                               " " + symbol.module + "." + symbol.name,
                           7});
      else if (symbol.module == "<erc>")
        add_rule(name, symbol.name);
      else if (symbol.module == "noscheme" && symbol.name == "std") {
        for (const auto &[key, rule] : registry.data.at("rules").items()) {
          if (!key.starts_with("std."))
            continue;
          auto qualified = name + "." + key.substr(4);
          add_rule(qualified, key);
        }
        for (const auto &predicate : registry.data.at("predicates")) {
          auto key = predicate.get<std::string>();
          if (!key.starts_with("std."))
            continue;
          auto qualified = name + "." + key.substr(4);
          add_rule(qualified, key);
        }
      } else if (symbol.module == "noscheme" &&
                 registry.data.at("enums").contains(symbol.name))
        for (const auto &value : registry.data.at("enums").at(symbol.name)) {
          auto qualified = name + "." + value.get<std::string>();
          entries.push_back(
              {qualified,
               builtin_description(module.symbols, registry, qualified), 20});
        }
    }
    Json values = object("selector", Json::array(), "field", Json::array(),
                         "net_fields", Json::array(), "missing", Json::array());
    for (const auto &entry : entries)
      if (entry.description.starts_with("ERC predicate ") ||
          (entry.kind == 20 && (entry.description.starts_with("role.") ||
                                entry.description.starts_with("bias."))))
        values["selector"].push_back(entry.name);
    for (const auto *scope : {"pin", "instance_pin", "port", "net"})
      for (const auto &field : registry.data.at("schemas").at(scope)) {
        auto kind = field.at("schema").at("kind");
        if (kind == "mapping" || kind == "enum")
          continue;
        auto name = field.at("name");
        values[std::string(scope) == "net" ? "net_fields" : "field"].push_back(
            name);
        if (std::string(scope) != "net" && kind == "bool")
          values["selector"].push_back(name);
      }
    for (const auto &policy : registry.data.at("enums").at("missing"))
      values["missing"].push_back(policy.dump());
    for (auto &entry : entries)
      if (!entry.rule.is_null())
        entry.rule["values"] = values;
    for (const auto &item : module.ast->at("items")) {
      if (item.value("node", "") != "Definition")
        continue;
      std::set<std::string> locals;
      for (const auto &node : item.at("body")) {
        auto kind = node.value("node", "");
        if (kind == "Net" || kind == "Instance")
          locals.insert(node.at("name").at("text").get<std::string>());
        if (kind == "Section" &&
            (node.at("name") == "params" || node.at("name") == "ports" ||
             node.at("name") == "pins"))
          for (const auto &entry : node.at("entries"))
            locals.insert(
                entry.at("key").at("first").at("text").get<std::string>());
      }
      for (auto &entry : entries)
        if (locals.contains(entry.name.substr(0, entry.name.find('.'))))
          entry.shadowed_in.push_back(item.at("span"));
    }
  }
  return catalog;
}
void interface_completions(CompletionCatalog &catalog, const Project &project,
                           const RegistryDescription &registry,
                           const Definitions &definitions) {
  auto bundle_type = [&](const Json &type) -> const Definition * {
    if (type.is_null())
      return nullptr;
    try {
      auto result = type_definition(project, registry, definitions, type);
      return result && result->kind == "bundle" ? result : nullptr;
    } catch (const DiagnosticsFailure &) {
      // Body validation reports invalid types; suggestions must not stop it.
      return nullptr;
    }
  };
  auto named_type = [&](const LoadedModule &module,
                        const Json &path) -> const Definition * {
    std::string name;
    for (const auto &part : path) {
      if (!name.empty())
        name += '.';
      name += part.at("text").get<std::string>();
    }
    auto symbol = module.symbols.find(name);
    if (symbol == module.symbols.end())
      return nullptr;
    auto found = definitions.find({symbol->second.module, symbol->second.name});
    return found == definitions.end() ? nullptr : &found->second;
  };
  auto footprints = [&](std::vector<CompletionEntry> &entries,
                        const Definition &def, const Definition *default_def,
                        const Json &sections) {
    if (def.kind != "component")
      return;
    std::string default_name;
    if (default_def && default_def->metadata.contains("footprint")) {
      const auto &value = default_def->metadata.at("footprint").at("value");
      if (value.is_string())
        default_name = value.get<std::string>();
    }
    std::set<std::string> candidates;
    if (!default_name.empty())
      candidates.insert(default_name);
    if (def.metadata.contains("custom")) {
      const auto &custom = def.metadata.at("custom").at("value");
      if (custom.contains("kicad_footprint_candidates")) {
        const auto &names = custom.at("kicad_footprint_candidates").at("value");
        if (names.is_array())
          for (const auto &name : names)
            if (name.is_string())
              candidates.insert(name.get<std::string>());
      }
    }
    for (const auto &section : sections) {
      if (section.value("node", "") != "Section" ||
          section.at("name") != "metadata")
        continue;
      for (const auto &field : section.at("entries")) {
        if (field.at("key").at("first").at("text") != "footprint" ||
            field.at("value").is_null() ||
            field.at("value").value("node", "") != "String")
          continue;
        std::vector<std::string> ordered;
        if (!default_name.empty())
          ordered.push_back(default_name);
        for (const auto &name : candidates)
          if (name != default_name)
            ordered.push_back(name);
        for (const auto &name : ordered)
          entries.push_back({name,
                             name == default_name
                                 ? "default footprint"
                                 : "footprint hint (verify package dimensions)",
                             17,
                             {},
                             field.at("value").at("span"),
                             Json(name).dump()});
      }
    }
  };
  auto wrapped = [](const Json &value, const std::string &module) {
    return object("value", value, "module", module, "span", value.at("span"));
  };
  std::map<std::string, Json> globals;
  if (project.board.declaration)
    for (const auto &node : project.board.declaration->at("body"))
      if (node.value("node", "") == "Net" && node.value("global_", false))
        globals["global." + node.at("name").at("text").get<std::string>()] =
            wrapped(node.at("type"), project.board.module);

  for (const auto &[module_name, module] : project.modules) {
    auto &entries = catalog[module.path];
    // Bound recursive expansion even for invalid cyclic bundle definitions.
    auto add = [&](std::vector<CompletionEntry> &destination,
                   const Definition &definition, const std::string &prefix,
                   const Json &span) {
      std::set<DefinitionKey> active;
      std::size_t count = 0;
      std::function<void(const Definition &, const std::string &)> visit =
          [&](const Definition &def, const std::string &base) {
            if (active.size() >= 32 || !active.insert(def.key).second)
              return;
            auto field = [&](const std::string &name, const Interface &pin) {
              if (++count > 10000)
                return;
              auto path = base + name;
              destination.push_back(
                  {path, "public interface " + path, 5, {}, span});
              if (!pin.type.is_null()) {
                auto nested = bundle_type(pin.type);
                if (nested)
                  visit(*nested, path + ".");
              }
            };
            for (const auto &[name, pin] : def.interface)
              field(name, pin);
            for (const auto &[alias, pin] : def.aliases)
              field(alias, def.interface.at(pin));
            active.erase(def.key);
          };
      visit(definition, prefix);
    };
    for (const auto &item : module.ast->at("items")) {
      if (item.value("node", "") != "Definition")
        continue;
      auto owner = definitions.find({module_name, item.at("name").at("text")});
      if (owner == definitions.end())
        continue;
      const Definition *default_def = &owner->second;
      if (!item.at("base").is_null())
        default_def = named_type(module, item.at("base"));
      footprints(entries, owner->second, default_def, item.at("body"));
      auto locals = globals;
      for (const auto &[name, pin] : owner->second.interface)
        locals[name] = pin.type;
      for (const auto &[alias, name] : owner->second.aliases)
        locals[alias] = owner->second.interface.at(name).type;
      for (const auto &node : item.at("body"))
        if (node.value("node", "") == "Net" && !node.value("global_", false))
          locals[node.at("name").at("text")] =
              wrapped(node.at("type"), module_name);
      std::map<std::string, const Definition *> bundles;
      for (const auto &[name, type] : locals) {
        if (type.is_null())
          continue;
        auto bundle = bundle_type(type);
        if (bundle)
          bundles[name] = bundle;
      }
      // Every binding in this lexical scope shares the same target choices.
      // Storing the full list per binding is quadratic on large boards.
      auto net_choices = std::make_shared<std::vector<CompletionEntry>>();
      for (const auto &[name, type] : locals)
        net_choices->push_back({name, "connection target " + name, 6});
      for (const auto &[name, bundle] : bundles)
        add(*net_choices, *bundle, name + ".", nullptr);
      auto scope_choices =
          [&](const Json &span,
              std::shared_ptr<const std::vector<CompletionEntry>> choices) {
            CompletionEntry entry{"", "", 0, {}, span};
            entry.candidates = std::move(choices);
            entries.push_back(std::move(entry));
          };
      for (const auto &node : item.at("body")) {
        if (node.value("node", "") != "Instance")
          continue;
        auto target = named_type(module, node.at("component"));
        if (!target)
          continue;
        footprints(entries, *target, target, node.at("sections"));
        CompletionEntry context{"", "", 0, {}, node.at("span")};
        Json pins = Json::object(), params = Json::array();
        for (const auto &[name, pin] : target->interface)
          pins[name] = name;
        for (const auto &[alias, pin] : target->aliases)
          pins[alias] = pin;
        for (const auto &param : target->parameters.parameters)
          params.push_back(param.name);
        std::vector<CompletionEntry> fields;
        add(fields, *target, "", nullptr);
        Json pin_fields = Json::array();
        for (const auto &field : fields)
          pin_fields.push_back(field.name);
        context.instance = object(
            "pins", pins, "fields", pin_fields, "params", params, "positional",
            target->metadata.contains("interchangeable")
                ? target->metadata.at("interchangeable").at("value").size()
                : 0);
        context.targets = net_choices;
        entries.push_back(std::move(context));
        auto pin_choices = std::make_shared<std::vector<CompletionEntry>>();
        add(*pin_choices, *target, "", nullptr);
        for (const auto &position : node.at("positional"))
          scope_choices(position.at("span"), net_choices);
        for (const auto &section : node.at("sections")) {
          if (section.at("name") != "connections")
            continue;
          for (const auto &binding : section.at("entries")) {
            if (binding.value("node", "") != "Binding")
              continue;
            scope_choices(binding.at("endpoint").at("span"), pin_choices);
            scope_choices(binding.at("target").at("span"), net_choices);
          }
        }
      }
    }
  }
}
CompletionResult complete(const std::string &text, std::size_t offset,
                          const std::vector<CompletionEntry> &catalog) {
  CompletionResult result{offset, offset, {}};
  if (offset > text.size())
    return result;
  if (auto keywords = keyword_completion(text, offset);
      !keywords.entries.empty())
    return keywords;
  if (auto section = section_completion(text, offset, catalog))
    return *section;
  if (auto instance = instance_completion(text, offset, catalog))
    return *instance;
  if (auto erc = erc_completion(text, offset, catalog))
    return *erc;
  for (const auto &value : footprint_values(text)) {
    if (offset < value.start || offset > value.end ||
        (value.closed && offset == value.end))
      continue;
    auto prefix = text.substr(value.start, offset - value.start);
    if (prefix.starts_with('"'))
      prefix.erase(0, 1);
    auto decoded = Json::parse("\"" + prefix + "\"", nullptr, false);
    if (!decoded.is_string())
      return result;
    result.start = value.start;
    result.end = value.end;
    std::set<std::string> offered;
    for (const auto &entry : catalog) {
      if (entry.kind != 17 || entry.visible_in.is_null())
        continue;
      if (value.start != entry.visible_in.at("start_pos").get<std::size_t>())
        continue;
      if (entry.name.starts_with(decoded.get<std::string>()) &&
          offered.insert(entry.name).second)
        result.entries.push_back(entry);
    }
    return result;
  }
  auto mask = code_mask(text);
  auto start = reference_start(mask, offset);
  if (start == offset)
    return result;
  auto end = offset;
  while (end < text.size() && name_char(mask[end]))
    ++end;
  auto raw_prefix = text.substr(start, offset - start);
  auto prefix = without_indices(raw_prefix);
  auto dot = prefix.rfind('.');
  auto qualifier =
      dot == std::string::npos ? std::string() : prefix.substr(0, dot + 1);
  auto raw_dot = raw_prefix.rfind('.');
  result.start = raw_dot == std::string::npos ? start : start + raw_dot + 1;
  result.end = end;
  // Bare names are offered in type positions, not arbitrary metadata
  // expressions.
  bool type_position = true;
  if (qualifier.empty()) {
    auto line = mask.rfind('\n', start ? start - 1 : 0);
    auto before =
        mask.substr(line == std::string::npos ? 0 : line + 1,
                    start - (line == std::string::npos ? 0 : line + 1));
    type_position = before.find("extends ") != std::string::npos ||
                    before.find(':') != std::string::npos;
  }
  bool interface_position =
      std::any_of(catalog.begin(), catalog.end(), [&](const auto &entry) {
        return (entry.kind != 0 || entry.candidates) &&
               !entry.visible_in.is_null() &&
               offset >= entry.visible_in.at("start_pos")
                             .template get<std::size_t>() &&
               offset <=
                   entry.visible_in.at("end_pos").template get<std::size_t>();
      });
  std::vector<const CompletionEntry *> choices;
  for (const auto &entry : catalog) {
    if (!entry.candidates) {
      choices.push_back(&entry);
    } else if (offset >= entry.visible_in.at("start_pos").get<std::size_t>() &&
               offset <= entry.visible_in.at("end_pos").get<std::size_t>()) {
      for (const auto &choice : *entry.candidates)
        choices.push_back(&choice);
    }
  }
  std::set<std::string> offered;
  for (const auto *candidate : choices) {
    const auto &entry = *candidate;
    if (entry.kind == 17 || entry.kind == 0)
      continue;
    if (!entry.visible_in.is_null()) {
      if (offset < entry.visible_in.at("start_pos").get<std::size_t>() ||
          offset > entry.visible_in.at("end_pos").get<std::size_t>())
        continue;
    } else if (interface_position && entry.kind != 5 && entry.kind != 6)
      continue;
    if (std::any_of(entry.shadowed_in.begin(), entry.shadowed_in.end(),
                    [&](const Json &span) {
                      return offset >=
                                 span.at("start_pos").get<std::size_t>() &&
                             offset < span.at("end_pos").get<std::size_t>();
                    }))
      continue;
    if (!entry.name.starts_with(prefix))
      continue;
    auto tail = entry.name.substr(qualifier.size());
    if (tail.find('.') != std::string::npos)
      continue;
    if (qualifier.empty() && entry.kind != 5 && entry.kind != 6 &&
        (!type_position || (entry.kind != 7 && entry.kind != 3)))
      continue;
    if (!offered.insert(tail).second)
      continue;
    result.entries.push_back({tail, entry.description, entry.kind});
  }
  return result;
}
} // namespace noscheme
