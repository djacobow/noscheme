#include "noscheme/lsp_completion.hpp"
#include "noscheme/lsp_completion_scan.hpp"
#include "noscheme/lsp_erc_completion.hpp"
#include "noscheme/lsp_instance_completion.hpp"
#include "noscheme/validation.hpp"
#include <algorithm>
#include <iterator>
#include <regex>
#include <set>
namespace noscheme {
namespace {
using completion_scan::name_char;
const std::regex
    instance_type_pattern(R"(^[ \t]+(?:global[ \t]+)?([A-Za-z0-9_#.]*)$)",
                          std::regex::multiline);
const std::regex base_type_pattern(R"(^(extend)[ \t]+([A-Za-z0-9_#.]*)$)",
                                   std::regex::multiline);

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
  // Remove incomplete declaration/type lines while retaining the enclosing
  // scope. A harmless metadata section keeps an otherwise empty body valid.
  if (recover_calls)
    for (const auto *pattern : {&instance_type_pattern, &base_type_pattern}) {
      for (std::sregex_iterator it(mask.begin(), mask.end(), *pattern), end;
           it != end; ++it) {
        auto start = static_cast<std::size_t>(it->position());
        auto stop = start + it->length();
        auto raw = it->str();
        auto indent = raw.find_first_not_of(" \t");
        // Masked comments are not empty declaration lines. Preserve their
        // delimiters and contents so recovery cannot turn prose into code.
        if (indent == std::string::npos &&
            text.substr(start, stop - start).find_first_not_of(" \t\r") !=
                std::string::npos)
          continue;
        if (pattern == &base_type_pattern)
          repairs[start] = {start, stop, ""};
        else {
          if (indent == std::string::npos)
            indent = raw.size();
          bool has_sibling = false;
          auto sibling = [&](std::size_t begin, std::size_t end) {
            auto first = mask.find_first_not_of(" \t\r", begin);
            if (first == std::string::npos || first >= end)
              return true;
            auto depth = first - begin;
            if (depth < indent)
              return false;
            if (depth == indent &&
                !std::regex_match(mask.substr(begin, end - begin),
                                  instance_type_pattern))
              has_sibling = true;
            return true;
          };
          for (auto end = start; end > 0 && !has_sibling;) {
            --end;
            auto previous = end ? mask.rfind('\n', end - 1) : std::string::npos;
            auto begin = previous == std::string::npos ? 0 : previous + 1;
            if (!sibling(begin, end))
              break;
            end = begin;
          }
          for (auto begin = mask.find('\n', stop);
               begin != std::string::npos && !has_sibling;) {
            ++begin;
            auto end = mask.find('\n', begin);
            if (!sibling(begin, end == std::string::npos ? mask.size() : end))
              break;
            begin = end;
          }
          if (has_sibling) {
            repairs[start] = {start, stop, ""};
            continue;
          }
          repairs[start] = {start, stop,
                            std::string(indent, ' ') + "metadata:\n" +
                                std::string(indent + 2, ' ') + "custom:\n" +
                                std::string(indent + 4, ' ') +
                                "completion: true"};
        }
      }
    }

  if (recover_calls)
    for (const auto &list : completion_scan::parameter_lists(mask)) {
      if (list.closed)
        repairs[list.opening] = {list.opening, list.end + 1, ""};
      else
        repairs[list.opening] = {list.opening, list.end,
                                 " _noscheme_completion"};
    }
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
    CompletionEntry schema_entry{"", "", 0};
    schema_entry.schemas =
        std::make_shared<const Json>(registry.data.at("schemas"));
    entries.push_back(std::move(schema_entry));
    auto add_rule = [&](const std::string &name, const std::string &canonical) {
      CompletionEntry entry{
          name, builtin_description(module.symbols, registry, name), 3};
      if (registry.data.at("rules").contains(canonical))
        entry.rule = registry.data.at("rules").at(canonical);
      entries.push_back(std::move(entry));
    };
    for (const auto &[name, symbol] : module.symbols) {
      if (symbol.declaration) {
        CompletionEntry entry{name,
                              symbol.declaration->keyword + " " +
                                  symbol.module + "." + symbol.name,
                              7};
        entry.type_kind = symbol.declaration->keyword;
        // Parameter names survive incomplete default expressions. Inheritance
        // changes defaults, not the parameter interface.
        const Symbol *current = &symbol;
        std::set<const ast::Definition *> seen;
        while (current && current->declaration &&
               seen.insert(current->declaration).second) {
          for (const auto &node : current->declaration->body)
            if (auto *section = ast::as<ast::Section>(node);
                section && section->name == "params")
              for (const auto *field : ast::field_list(section->entries))
                entry.parameter_names.push_back(field->key.first.text);
          auto owner_module = project.modules.find(current->module);
          auto base = ast::dotted(current->declaration->base);
          if (owner_module == project.modules.end() || base.empty())
            break;
          auto parent = owner_module->second.symbols.find(base);
          current = parent == owner_module->second.symbols.end()
                        ? nullptr
                        : &parent->second;
        }
        entries.push_back(std::move(entry));
      } else if (symbol.module == "noscheme" && symbol.name == "num") {
        for (const auto *helper : {"int", "ceil", "floor", "round"}) {
          CompletionEntry entry{name + "." + helper, "numeric helper", 3};
          entry.type_kind = "num";
          entries.push_back(std::move(entry));
        }
      } else if (symbol.module == "<erc>")
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
          CompletionEntry entry{
              qualified,
              builtin_description(module.symbols, registry, qualified), 20};
          entry.type_kind = symbol.name;
          entries.push_back(std::move(entry));
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
    for (const auto &entry : module.ast->items) {
      auto *item = ast::as<ast::Definition>(entry);
      if (!item)
        continue;
      std::set<std::string> locals;
      for (const auto &node : item->body) {
        if (auto *net = ast::as<ast::Net>(node))
          locals.insert(net->name.text);
        else if (auto *instance = ast::as<ast::Instance>(node))
          locals.insert(instance->name.text);
        else if (auto *section = ast::as<ast::Section>(node);
                 section &&
                 (section->name == "params" || section->name == "ports" ||
                  section->name == "pins"))
          for (const auto *field : ast::field_list(section->entries))
            locals.insert(field->key.first.text);
      }
      for (auto &entry : entries)
        if (locals.contains(entry.name.substr(0, entry.name.find('.'))))
          entry.shadowed_in.push_back(item->span);
    }
  }
  return catalog;
}
void interface_completions(CompletionCatalog &catalog, const Project &project,
                           const RegistryDescription &registry,
                           const Definitions &definitions) {
  auto bundle_type = [&](const ScopedExpression &type) -> const Definition * {
    if (!type.expression)
      return nullptr;
    try {
      auto result = type_definition(project, registry, definitions, type);
      return result && result->kind == "bundle" ? result : nullptr;
    } catch (const DiagnosticsFailure &) {
      // Body validation reports invalid types; suggestions must not stop it.
      return nullptr;
    }
  };
  auto named_type =
      [&](const LoadedModule &module,
          const std::vector<ast::Name> &path) -> const Definition * {
    auto symbol = module.symbols.find(ast::dotted(path));
    if (symbol == module.symbols.end())
      return nullptr;
    auto found = definitions.find({symbol->second.module, symbol->second.name});
    return found == definitions.end() ? nullptr : &found->second;
  };
  auto footprints = [&](std::vector<CompletionEntry> &entries,
                        const Definition &def, const Definition *default_def,
                        const std::vector<const ast::Section *> &sections) {
    if (def.kind != "component")
      return;
    std::string default_name;
    if (auto *footprint = default_def
                              ? meta_value(default_def->metadata, "footprint")
                              : nullptr;
        footprint && footprint->is<std::string>())
      default_name = footprint->string();
    std::set<std::string> candidates;
    if (!default_name.empty())
      candidates.insert(default_name);
    if (auto *custom = meta_value(def.metadata, "custom");
        custom && custom->is<MetaFields>())
      if (auto *names =
              meta_value(custom->fields(), "kicad_footprint_candidates");
          names && names->is<MetaList>())
        for (const auto &name : meta_strings(*names))
          candidates.insert(name);
    for (const auto *section : sections) {
      if (section->name != "metadata")
        continue;
      for (const auto *field : ast::field_list(section->entries)) {
        if (field->key.first.text != "footprint" ||
            !ast::is(field->value, ast::Kind::String))
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
                             field->value->span,
                             Json(name).dump()});
      }
    }
  };
  std::map<std::string, ScopedExpression> globals;
  if (project.board.declaration)
    for (const auto &node : project.board.declaration->body)
      if (auto *net = ast::as<ast::Net>(node); net && net->global)
        globals["global." + net->name.text] = {net->type.get(),
                                               project.board.module};

  for (const auto &[module_name, module] : project.modules) {
    auto &entries = catalog[module.path];
    // Bound recursive expansion even for invalid cyclic bundle definitions.
    auto add = [&](std::vector<CompletionEntry> &destination,
                   const Definition &definition, const std::string &prefix,
                   const Span &span) {
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
              if (pin.type.expression) {
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
    for (const auto &entry : module.ast->items) {
      auto *item = ast::as<ast::Definition>(entry);
      if (!item)
        continue;
      auto owner = definitions.find({module_name, item->name.text});
      if (owner == definitions.end())
        continue;
      const Definition *default_def = &owner->second;
      if (!item->base.empty())
        default_def = named_type(module, item->base);
      std::vector<const ast::Section *> sections;
      for (const auto &node : item->body)
        if (auto *section = ast::as<ast::Section>(node))
          sections.push_back(section);
      footprints(entries, owner->second, default_def, sections);
      auto locals = globals;
      for (const auto &[name, pin] : owner->second.interface)
        locals[name] = pin.type;
      for (const auto &[alias, name] : owner->second.aliases)
        locals[alias] = owner->second.interface.at(name).type;
      for (const auto &node : item->body)
        if (auto *net = ast::as<ast::Net>(node); net && !net->global)
          locals[net->name.text] = {net->type.get(), module_name};
      std::map<std::string, const Definition *> bundles;
      for (const auto &[name, type] : locals) {
        if (!type.expression)
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
        add(*net_choices, *bundle, name + ".", {});
      auto scope_choices =
          [&](const Span &span,
              std::shared_ptr<const std::vector<CompletionEntry>> choices) {
            CompletionEntry entry{"", "", 0, {}, span};
            entry.candidates = std::move(choices);
            entries.push_back(std::move(entry));
          };
      for (const auto &body_node : item->body) {
        auto *node = ast::as<ast::Instance>(body_node);
        if (!node)
          continue;
        auto target = named_type(module, node->component);
        if (!target)
          continue;
        std::vector<const ast::Section *> instance_sections;
        for (const auto &section : node->sections)
          instance_sections.push_back(&section);
        footprints(entries, *target, target, instance_sections);
        CompletionEntry context{"", "", 0, {}, node->span};
        Json pins = Json::object(), params = Json::array();
        for (const auto &[name, pin] : target->interface)
          pins[name] = name;
        for (const auto &[alias, pin] : target->aliases)
          pins[alias] = pin;
        for (const auto &param : target->parameters.parameters)
          params.push_back(param.name);
        std::vector<CompletionEntry> fields;
        add(fields, *target, "", {});
        Json pin_fields = Json::array();
        for (const auto &field : fields)
          pin_fields.push_back(field.name);
        context.instance = object(
            "pins", pins, "fields", pin_fields, "params", params, "positional",
            target->metadata.contains("interchangeable")
                ? meta_value(target->metadata, "interchangeable")->list().size()
                : 0);
        Json action_pins = Json::array(), action_params = Json::array();
        std::set<std::string> bound, supplied;
        bool safe_actions = true, has_metadata = false;
        if (node->wildcard)
          for (const auto &[name, pin] : target->interface)
            bound.insert(name);
        if (auto *group = meta_value(target->metadata, "interchangeable")) {
          auto names = meta_strings(*group);
          for (std::size_t i = 0;
               i < std::min(names.size(), node->positional.size()); ++i)
            bound.insert(names[i]);
        }
        for (const auto &section : node->sections) {
          if (section.name == "elements")
            safe_actions = false;
          if (section.name == "metadata")
            has_metadata = true;
          for (const auto &entry : section.entries) {
            if (auto *binding = ast::as<ast::Binding>(entry)) {
              try {
                for (const auto &name : physical_keys(binding->endpoint.head)) {
                  auto alias = target->aliases.find(name);
                  bound.insert(alias == target->aliases.end() ? name
                                                              : alias->second);
                }
              } catch (const std::exception &) {
                // Invalid or unfinished ranges must not disable completion.
                safe_actions = false;
              }
            }
            if (auto *argument = ast::as<ast::Argument>(entry))
              supplied.insert(argument->name.text);
          }
        }
        for (const auto &[name, pin] : target->interface)
          if (!bound.contains(name)) {
            if (auto *role = meta_value(pin.metadata, "role");
                role && meta_strings(*role) ==
                            std::vector<std::string>{"do_not_connect"})
              continue;
            auto *unused = meta_value(pin.metadata, "unused");
            action_pins.push_back(object("name", name, "required",
                                         !unused ||
                                             !unused->is<std::string>() ||
                                             unused->string() != "always"));
          }
        if (node->parameter && target->parameters.unnamed)
          supplied.insert(*target->parameters.unnamed);
        for (const auto &param : target->parameters.parameters)
          if (!supplied.contains(param.name))
            action_params.push_back(
                object("name", param.name, "required", !param.default_value));
        context.instance["actions"] =
            object("safe", safe_actions, "metadata", has_metadata, "pins",
                   action_pins, "params", action_params);
        context.targets = net_choices;
        entries.push_back(std::move(context));
        auto pin_choices = std::make_shared<std::vector<CompletionEntry>>();
        add(*pin_choices, *target, "", {});
        for (const auto &position : node->positional)
          scope_choices(position->span, net_choices);
        for (const auto &entry :
             ast::section_entries(node->sections, "connections")) {
          auto *binding = ast::as<ast::Binding>(entry);
          if (!binding)
            continue;
          scope_choices(binding->endpoint.span, pin_choices);
          scope_choices(binding->target->span, net_choices);
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
  auto scoped = scope_completion(text, offset, catalog);
  if (!scoped.entries.empty())
    return scoped;
  for (const auto &list : completion_scan::parameter_lists(code_mask(text))) {
    if (offset <= list.opening || offset > list.end)
      continue;
    auto args =
        code_mask(text).substr(list.opening + 1, offset - list.opening - 1);
    auto comma = args.rfind(',');
    auto fragment = args.substr(comma == std::string::npos ? 0 : comma + 1);
    auto word = completion_scan::trim(fragment);
    if (!std::all_of(word.begin(), word.end(), name_char))
      return result;
    result.start = offset - word.size();
    while (result.end < text.size() && name_char(text[result.end]))
      ++result.end;
    std::set<std::string> bound;
    static const std::regex named(R"(([A-Za-z_#][A-Za-z0-9_#]*)[ \t]*:)");
    auto previous =
        comma == std::string::npos ? std::string() : args.substr(0, comma);
    for (std::sregex_iterator it(previous.begin(), previous.end(), named), end;
         it != end; ++it)
      bound.insert((*it)[1]);
    for (const auto &entry : catalog)
      if (entry.name == list.type)
        for (const auto &name : entry.parameter_names)
          if (name.starts_with(word) && !bound.contains(name))
            result.entries.push_back(
                {name,
                 "instance parameter",
                 10,
                 {},
                 {},
                 name + (completion_scan::trim(text.substr(result.end))
                                 .starts_with(':')
                             ? ""
                             : ": ")});
    return result;
  }
  auto keywords = keyword_completion(text, offset);
  auto section = section_completion(text, offset, catalog);
  // An instance body's first lines take connections or section keywords.
  if (section && !keywords.entries.empty() &&
      section->start == keywords.start && section->end == keywords.end) {
    std::ranges::move(keywords.entries, std::back_inserter(section->entries));
    return *section;
  }
  // Type completion adds to keywords in board/block bodies. Elsewhere the
  // lexical keywords stand alone, including files without a project.
  if (section)
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
      if (entry.kind != 17 || !entry.visible_in)
        continue;
      if (value.start != entry.visible_in.start)
        continue;
      if (entry.name.starts_with(decoded.get<std::string>()) &&
          offered.insert(entry.name).second)
        result.entries.push_back(entry);
    }
    return result;
  }
  auto mask = code_mask(text);
  auto line = offset ? text.rfind('\n', offset - 1) : std::string::npos;
  auto begin = line == std::string::npos ? 0 : line + 1;
  auto before = text.substr(begin, offset - begin);
  std::smatch type_match, base_match;
  const bool instance_type =
      std::regex_match(before, type_match, instance_type_pattern);
  const bool base_type =
      std::regex_match(before, base_match, base_type_pattern);
  if (code_mask(text.substr(0, offset) + "x").back() == 'x' &&
      (instance_type || base_type)) {
    const auto prefix = base_type ? base_match[2].str() : type_match[1].str();
    bool nets_only = before.find("global ") != std::string::npos;
    // A bundle body or ports section accepts only net and bundle types.
    std::vector<std::pair<std::size_t, std::string>> parents;
    auto indent = before.find_first_not_of(" \t");
    if (indent == std::string::npos)
      indent = before.size();
    for (std::size_t pos = 0; pos < begin;) {
      auto end = mask.find('\n', pos);
      if (end == std::string::npos)
        end = mask.size();
      auto row = mask.substr(pos, end - pos);
      auto first = row.find_first_not_of(" \t\r");
      if (first != std::string::npos) {
        while (!parents.empty() && parents.back().first >= first)
          parents.pop_back();
        parents.emplace_back(first, completion_scan::trim(row));
      }
      pos = end + 1;
    }
    while (!parents.empty() && parents.back().first >= indent)
      parents.pop_back();
    if (!base_type) {
      if (parents.empty())
        return keywords;
      const auto &parent = parents.back().second;
      nets_only |= parent == "ports:" || parent.starts_with("bundle ");
      if (!(parent.starts_with("board ") || parent.starts_with("block ") ||
            nets_only))
        return keywords;
    }
    auto dot = prefix.rfind('.');
    auto qualifier =
        dot == std::string::npos ? std::string() : prefix.substr(0, dot + 1);
    result.start = offset - prefix.size() + qualifier.size();
    while (result.end < text.size() &&
           (name_char(text[result.end]) || text[result.end] == '.'))
      ++result.end;
    std::string owner_name;
    if (!base_type && !parents.empty()) {
      static const std::regex owner(
          R"(^(?:component|block|board|bundle)\s+([A-Za-z_#][A-Za-z0-9_#]*))");
      std::smatch match;
      if (std::regex_search(parents.front().second, match, owner))
        owner_name = match[1];
    }
    result.entries = keywords.entries;
    for (const auto &entry : catalog) {
      const bool valid_kind = entry.type_kind == "nettype" ||
                              (!base_type && entry.type_kind == "bundle") ||
                              (!nets_only && (entry.type_kind == "component" ||
                                              entry.type_kind == "block")) ||
                              entry.type_kind == "net";
      if ((entry.kind == 7 || entry.kind == 20) && valid_kind &&
          entry.name.starts_with(prefix) && entry.name != owner_name)
        result.entries.push_back({entry.name.substr(qualifier.size()),
                                  entry.description, entry.kind});
    }
    return result;
  }
  if (!keywords.entries.empty())
    return keywords;
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
    type_position = before.find("extend ") != std::string::npos ||
                    before.find(':') != std::string::npos;
  }
  bool interface_position =
      std::any_of(catalog.begin(), catalog.end(), [&](const auto &entry) {
        return (entry.kind != 0 || entry.candidates) && entry.visible_in &&
               offset >= entry.visible_in.start &&
               offset <= entry.visible_in.end;
      });
  std::vector<const CompletionEntry *> choices;
  for (const auto &entry : catalog) {
    if (!entry.candidates) {
      choices.push_back(&entry);
    } else if (offset >= entry.visible_in.start &&
               offset <= entry.visible_in.end) {
      for (const auto &choice : *entry.candidates)
        choices.push_back(&choice);
    }
  }
  std::set<std::string> offered;
  for (const auto *candidate : choices) {
    const auto &entry = *candidate;
    if (entry.kind == 17 || entry.kind == 0)
      continue;
    if (entry.visible_in) {
      if (offset < entry.visible_in.start || offset > entry.visible_in.end)
        continue;
    } else if (interface_position && entry.kind != 5 && entry.kind != 6)
      continue;
    if (entry.shadowed(offset))
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
