#include "noscheme/project.hpp"
#include "noscheme/classify.hpp"
#include "noscheme/validation.hpp"
#include <algorithm>
#include <fstream>
#include <regex>
#include <set>
#include <sstream>
#include <toml++/toml.hpp>

namespace noscheme {
bool builtin_net(const Symbols &symbols, const std::string &spelling) {
  auto dot = spelling.rfind('.');
  if (dot == std::string::npos)
    return false;
  auto prefix = symbols.find(spelling.substr(0, dot));
  return prefix != symbols.end() && !prefix->second.declaration &&
         prefix->second.name == "net";
}
bool net_type(const Symbols &symbols, const std::string &spelling) {
  if (auto symbol = symbols.find(spelling); symbol != symbols.end())
    return symbol->second.declaration &&
           (symbol->second.declaration->keyword == "bundle" ||
            symbol->second.declaration->keyword == "nettype");
  return builtin_net(symbols, spelling);
}
bool is_identifier(std::string_view text) {
  auto first = [](char c) {
    return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || c == '_' ||
           c == '#';
  };
  return !text.empty() && first(text.front()) &&
         std::ranges::all_of(
             text, [&](char c) { return first(c) || (c >= '0' && c <= '9'); });
}
namespace {
const std::regex
    qualified(R"([A-Za-z_#][A-Za-z0-9_#]*(\.[A-Za-z_#][A-Za-z0-9_#]*)*)");
const std::set<std::string> builtins{"role",  "net",  "use", "num",
                                     "drive", "pull", "std"};
fs::path resolved(const fs::path &path) {
  return fs::weakly_canonical(fs::absolute(path));
}
[[noreturn]] void fail(std::string code, std::string message, const Json &span,
                       bool config = false, const Json &previous = nullptr);
[[noreturn]] void fail(std::string code, std::string message, const Span &span,
                       bool config = false, const Span &previous = {}) {
  fail(std::move(code), std::move(message), span.json(), config,
       previous.json());
}
[[noreturn]] void fail(std::string code, std::string message, const Json &span,
                       bool config, const Json &previous) {
  auto related = Json::array();
  if (!previous.is_null())
    related.push_back(
        object("message", "previous declaration", "span", previous));
  throw DiagnosticsFailure(
      Json::array(
          {object("code", std::move(code), "severity", "error", "message",
                  std::move(message), "span", span, "related",
                  std::move(related), "instance_path", nullptr, "configuration",
                  nullptr, "application", nullptr)}),
      config ? 2 : 1);
}
// Gives each `extend` the kind of its base: a component, block, or net type.
// An extension of an unknown base stays an "extend", whose resolution reports
// it.
std::shared_ptr<const ast::Module> extension_kinds(const std::string &module,
                                                   const ast::Module &tree,
                                                   const Symbols &symbols,
                                                   bool lenient) {
  std::map<std::string, std::string> kinds;
  std::function<std::string(const ast::Definition &, std::vector<std::string>)>
      kind = [&](const ast::Definition &definition,
                 std::vector<std::string> chain) -> std::string {
    const auto &name = definition.name.text;
    if (auto found = kinds.find(name); found != kinds.end())
      return found->second;
    if (auto at = std::ranges::find(chain, name); at != chain.end()) {
      std::string message = "inheritance cycle: ";
      for (auto i = at; i != chain.end(); ++i)
        message += module + "." + *i + " -> ";
      fail("resolve.inheritance-cycle", message + module + "." + name,
           definition.span);
    }
    auto spelling = ast::dotted(definition.base);
    auto symbol = symbols.find(spelling);
    std::string result;
    if (symbol == symbols.end() && builtin_net(symbols, spelling))
      result = "nettype";
    else if (symbol == symbols.end() || !symbol->second.declaration)
      result = "extend";
    else if (symbol->second.declaration->keyword == "extend") {
      chain.push_back(name);
      result = kind(*symbol->second.declaration, chain);
    } else
      result = symbol->second.declaration->keyword;
    return kinds[name] = result;
  };
  auto result = std::make_shared<ast::Module>(tree);
  for (auto &item : result->items) {
    auto *definition = ast::as<ast::Definition>(item);
    if (!definition || definition->keyword != "extend")
      continue;
    auto resolved = std::make_shared<ast::Definition>(*definition);
    try {
      resolved->keyword = kind(*definition, {});
    } catch (const DiagnosticsFailure &) {
      if (!lenient)
        throw;
      continue;
    }
    if (resolved->keyword != "extend" && !lenient)
      if (auto errors = kind_errors(*resolved); !errors.empty())
        throw DiagnosticsFailure(std::move(errors));
    item = std::move(resolved);
  }
  return result;
}
// Makes each body declaration Nets or an Instance by the kind of its type. A
// type that names nothing makes an instance, whose resolution reports it,
// unless the declaration cannot be one. A declaration of an extension whose own
// base is unknown is left out; that extension's resolution reports the base.
std::shared_ptr<const ast::Module>
classify_module(const ast::Module &tree, const Symbols &symbols, bool lenient) {
  UniqueDiagnostics errors;
  auto result = std::make_shared<ast::Module>(tree);
  for (auto &item : result->items) {
    auto *definition = ast::as<ast::Definition>(item);
    if (!definition)
      continue;
    auto classified = std::make_shared<ast::Definition>(*definition);
    classified->body.clear();
    for (const auto &node : definition->body) {
      auto *declaration = ast::as<ast::Declaration>(node);
      if (!declaration) {
        classified->body.push_back(node);
        continue;
      }
      auto spelling = ast::dotted(declaration->type);
      auto symbol = symbols.find(spelling);
      bool net = net_type(symbols, spelling);
      try {
        for (auto &made : classify(*declaration, net))
          classified->body.push_back(std::move(made));
      } catch (const DiagnosticsFailure &failure) {
        auto *declared =
            symbol == symbols.end() ? nullptr : symbol->second.declaration;
        if (lenient || (declared && declared->keyword == "extend"))
          continue;
        if (net || symbol != symbols.end())
          errors.add_all(failure.diagnostics);
        else
          errors.add(error_json("resolve.missing-definition",
                                "unknown definition '" + spelling + "'",
                                declaration->type.front().span));
      }
    }
    item = std::move(classified);
  }
  if (!errors.items().empty())
    throw DiagnosticsFailure(errors.take());
  return result;
}
void bind_definitions(const std::string &module, const ast::Module &tree,
                      Symbols &symbols) {
  for (const auto &item : tree.items)
    if (auto *definition = ast::as<ast::Definition>(item))
      symbols[definition->name.text] =
          Symbol{module, definition->name.text, definition};
}
} // namespace
std::shared_ptr<const ast::Module> classified_module(const std::string &module,
                                                     const ast::Module &tree,
                                                     Symbols &symbols,
                                                     bool lenient) {
  // Declarations name types by kind, so extensions take their kinds first.
  auto result = extension_kinds(module, tree, symbols, lenient);
  bind_definitions(module, *result, symbols);
  result = classify_module(*result, symbols, lenient);
  bind_definitions(module, *result, symbols);
  return result;
}
namespace {
std::string read_source(const fs::path &path, const Sources &sources) {
  if (auto found = sources.find(path); found != sources.end())
    return found->second;
  std::ifstream stream(path, std::ios::binary);
  if (!stream)
    throw std::runtime_error("cannot read " + path.string());
  std::ostringstream contents;
  contents << stream.rdbuf();
  if (stream.bad())
    throw std::runtime_error("cannot read " + path.string());
  // Match Python's text-mode universal newlines for disk input only.
  auto raw = contents.str();
  std::string text;
  text.reserve(raw.size());
  for (std::size_t i = 0; i < raw.size(); ++i) {
    if (raw[i] == '\r') {
      if (i + 1 < raw.size() && raw[i + 1] == '\n')
        ++i;
      text += '\n';
    } else
      text += raw[i];
  }
  return text;
}
bool reserved_module(const std::string &name) {
  return name == "noscheme" || name.starts_with("noscheme.");
}
fs::path relative_module(std::string name) {
  std::replace(name.begin(), name.end(), '.', '/');
  return name + ".nosch";
}
bool beneath(const fs::path &path, const fs::path &root) {
  auto [p, r] =
      std::mismatch(path.begin(), path.end(), root.begin(), root.end());
  return r == root.end();
}
fs::path module_path(const ProjectConfig &config, const std::string &name,
                     const Span &span, const Sources &sources) {
  if (!std::regex_match(name, qualified) || reserved_module(name))
    fail("import.module", "invalid or reserved module name '" + name + "'",
         span);
  std::vector<fs::path> matches;
  for (const auto &root : config.search_paths()) {
    auto candidate = root / relative_module(name);
    auto path = resolved(candidate);
    if (!beneath(path, root))
      fail("import.escape",
           "module '" + name + "' escapes search root " + root.string(), span);
    if (fs::is_regular_file(candidate) || sources.contains(path))
      matches.push_back(path);
  }
  if (matches.empty())
    fail("import.missing-module",
         "module '" + name + "' not found in configured search paths", span);
  if (matches.size() != 1) {
    std::string paths;
    for (const auto &path : matches)
      paths += (paths.empty() ? "" : ", ") + path.string();
    fail("import.ambiguous-module",
         "module '" + name + "' matches multiple paths: " + paths, span);
  }
  return matches.front();
}
} // namespace

std::string source_text(const fs::path &path, const Sources &sources) {
  return read_source(resolved(path), sources);
}

std::vector<fs::path> ProjectConfig::search_paths() const {
  auto paths = source_paths;
  paths.insert(paths.end(), library_paths.begin(), library_paths.end());
  return paths;
}

fs::path ProjectConfig::state_path() const {
  return path.parent_path() / ("noscheme." + target + ".state.json");
}
ProjectConfig read_config(fs::path path, const Sources &sources,
                          const std::string &target) {
  if (fs::is_directory(path))
    path /= "noscheme.toml";
  path = resolved(path);
  std::string text;
  try {
    text = read_source(path, sources);
  } catch (const std::runtime_error &e) {
    fail("project.io", e.what(), Source("", path.string()).span(0, 0), true);
  }
  auto shared_source = [&] {
    try {
      return std::make_shared<const Source>(text, path.string());
    } catch (const Failure &e) {
      fail("project.io",
           "cannot decode configuration: " + std::string(e.what()),
           Source("", path.string()).span(0, 0), true);
    }
  }();
  const auto &source = *shared_source;
  const auto file_span = source.span(0, text.size());
  auto location = [&](const std::string &key,
                      const std::string &target_name = "") -> Span {
    std::string escaped;
    for (char c : key) {
      if (std::string_view(R"(\.^$|()[]{}*+?)").find(c) !=
          std::string_view::npos)
        escaped += '\\';
      escaped += c;
    }
    std::regex pattern("^[ \\t]*(?:" + escaped + "|\"" + escaped + "\"|'" +
                           escaped + "')[ \\t]*=",
                       std::regex::multiline);
    std::string searched = text;
    std::size_t offset = 0;
    if (!target_name.empty()) {
      std::regex header("^\\s*\\[\\s*targets\\s*\\.\\s*(?:" + target_name +
                            "|\"" + target_name + "\"|'" + target_name +
                            "')\\s*\\]",
                        std::regex::multiline);
      std::smatch found;
      if (!std::regex_search(text, found, header))
        return {&source, 0, text.size()};
      offset = found.position() + found.length();
      searched = text.substr(offset);
      if (std::regex_search(searched, found,
                            std::regex("^\\s*\\[", std::regex::multiline)))
        searched.resize(found.position());
    }
    std::smatch match;
    if (!std::regex_search(searched, match, pattern))
      return {&source, 0, text.size()};
    auto start = offset + static_cast<std::size_t>(match.position());
    return {&source, start, start + match.length()};
  };
  auto invalid = [&](const std::string &key, const std::string &message,
                     const std::string &target_name = "") {
    fail("project.config", message, location(key, target_name), true);
  };
  toml::table data;
  try {
    // tomllib rejects a BOM; toml++ otherwise accepts one.
    if (text.starts_with("\xef\xbb\xbf"))
      fail("project.toml", "unexpected UTF-8 BOM", file_span, true);
    data = toml::parse(text, path.string());
  } catch (const toml::parse_error &e) {
    fail("project.toml", std::string(e.description()), file_span, true);
  }
  const std::set<std::string> required{"language_version", "targets",
                                       "source_paths", "library_paths",
                                       "output_dir"};
  for (const auto &[key, value] : data)
    if (!required.contains(std::string(key.str())) && key != "erc_modules" &&
        key != "erc_rule_sets" && key != "default_target")
      invalid(std::string(key.str()),
              "unknown configuration key '" + std::string(key.str()) + "'");
  for (const auto &key : required)
    if (!data.contains(key))
      invalid(key, "missing required configuration key '" + key + "'");
  if (data["language_version"].value<std::string>() != "0.1")
    invalid("language_version", "language_version must be the string \"0.1\"");
  std::string selected;
  const auto *targets = data["targets"].as_table();
  if (!targets || targets->empty())
    invalid("targets", "targets must be a nonempty table");
  for (const auto &[key, value] : *targets) {
    const std::string name(key.str());
    if (!std::regex_match(name, std::regex("[A-Za-z_][A-Za-z0-9_-]*")))
      invalid("targets", "invalid target name '" + name + "'");
    auto *settings = value.as_table();
    if (!settings)
      invalid("targets",
              "target '" + name + "' allows only root and output_dir");
    for (const auto &[field, ignored] : *settings)
      if (field != "root" && field != "output_dir")
        invalid("targets",
                "target '" + name + "' allows only root and output_dir");
    auto root = (*settings)["root"].value<std::string>();
    if (!root || !std::regex_match(*root, qualified) ||
        root->find('.') == std::string::npos)
      invalid("root", "target '" + name + "' must name a qualified root block",
              name);
    auto output = (*settings)["output_dir"].value<std::string>();
    if (settings->contains("output_dir") &&
        (!output || output->empty() || output->find('\0') != std::string::npos))
      invalid("output_dir", "output_dir must be a nonempty path string", name);
  }
  auto default_target = data["default_target"].value<std::string>();
  if (data.contains("default_target") &&
      (!default_target || !targets->contains(*default_target)))
    invalid("default_target", "default_target must name a configured target");
  selected = target.empty() ? default_target.value_or("") : target;
  if (selected.empty()) {
    if (targets->size() != 1)
      invalid("targets", "select a target with --target or default_target");
    selected = std::string(targets->begin()->first.str());
  }
  if (!targets->contains(selected))
    invalid("targets", "unknown target '" + selected + "'");
  const auto &settings = *(*targets)[selected].as_table();
  auto base_output = data["output_dir"].value<std::string>();
  if (!base_output || base_output->empty() ||
      base_output->find('\0') != std::string::npos)
    invalid("output_dir", "output_dir must be a nonempty path string");
  auto board = settings["root"].value<std::string>();
  auto target_output = settings["output_dir"].value<std::string>().value_or(
      (fs::path(*base_output) / selected).string());
  if (reserved_module(*board))
    invalid("root",
            "noscheme is reserved for builtins and cannot contain the root",
            selected);
  auto strings = [&](const std::string &key, const std::string &message,
                     bool paths) {
    std::vector<std::string> result;
    const auto *array = data[key].as_array();
    if (!array)
      invalid(key, message);
    for (const auto &item : *array) {
      auto value = item.value<std::string>();
      if (!value || value->empty() ||
          (paths && value->find('\0') != std::string::npos))
        invalid(key, message);
      result.push_back(*value);
    }
    return result;
  };
  auto directories = [&](const std::string &key) {
    std::vector<fs::path> result;
    for (const auto &value : strings(
             key, key + " must be an array of nonempty path strings", true)) {
      auto directory = resolved(path.parent_path() / value);
      if (!fs::is_directory(directory))
        invalid(key, "search path is not a directory: " + directory.string());
      result.push_back(directory);
    }
    return result;
  };
  ProjectConfig config;
  config.path = path;
  config.target = selected;
  config.language_version = "0.1";
  const auto dot = board->rfind('.');
  config.board_module = board->substr(0, dot);
  config.board_name = board->substr(dot + 1);
  config.source_paths = directories("source_paths");
  config.library_paths = directories("library_paths");
  const auto roots = config.search_paths();
  if (roots.empty())
    invalid("source_paths",
            "at least one source or library search path is required");
  if (std::set<fs::path>(roots.begin(), roots.end()).size() != roots.size())
    invalid("source_paths", "search paths contain duplicate directories");
  config.output_dir = resolved(path.parent_path() / target_output);
  if (fs::exists(config.output_dir) && !fs::is_directory(config.output_dir))
    invalid("output_dir", "output_dir is not a directory");
  if (data.contains("erc_modules")) {
    std::set<fs::path> seen;
    for (const auto &value :
         strings("erc_modules",
                 "erc_modules must be an array of Python file paths", true)) {
      auto module = resolved(path.parent_path() / value);
      if (!seen.insert(module).second || !fs::is_regular_file(module) ||
          module.extension() != ".py")
        invalid("erc_modules",
                "erc_modules must name distinct existing Python files");
      config.erc_modules.push_back(module);
    }
  }
  const std::string rules_error =
      "erc_rule_sets must be an array of distinct rule-set names";
  config.erc_rule_sets = data.contains("erc_rule_sets")
                             ? strings("erc_rule_sets", rules_error, false)
                             : std::vector<std::string>{"std.default"};
  if (std::set<std::string>(config.erc_rule_sets.begin(),
                            config.erc_rule_sets.end())
          .size() != config.erc_rule_sets.size())
    invalid("erc_rule_sets", rules_error);
  config.span = location("root", selected);
  config.source = shared_source;
  return config;
}

Project load_project(fs::path path, const Sources &sources,
                     ParsedSources *cache, const ErcExportLoader &erc_loader,
                     const std::string &target) {
  Project project;
  project.config = read_config(path, sources, target);
  const auto &config = project.config;
  ErcExports extensions;
  if (erc_loader || !config.erc_modules.empty()) {
    if (!erc_loader)
      fail("native.erc-registry",
           "project requires ERC registry exports from the Python bridge",
           config.span, true);
    extensions = erc_loader(config);
  }
  std::map<std::string, LoadedModule> parsed;
  std::vector<std::string> active, order;
  struct Visit {
    std::string name;
    Span span;
    bool finish;
  };
  std::vector<Visit> stack{{config.board_module, config.span, false}};
  while (!stack.empty()) {
    auto visit = std::move(stack.back());
    stack.pop_back();
    if (visit.finish) {
      active.pop_back();
      order.push_back(visit.name);
      continue;
    }
    if (auto cycle = std::find(active.begin(), active.end(), visit.name);
        cycle != active.end()) {
      std::string message = "import cycle: ";
      for (auto it = cycle; it != active.end(); ++it)
        message += *it + " -> ";
      fail("import.cycle", message + visit.name, visit.span);
    }
    if (parsed.contains(visit.name))
      continue;
    auto file = module_path(config, visit.name, visit.span, sources);
    std::shared_ptr<const ast::Module> tree;
    try {
      auto text = read_source(file, sources);
      if (cache && cache->contains(file) && cache->at(file).text == text)
        tree = cache->at(file).ast;
      else {
        auto source = std::make_shared<const Source>(text, file.string());
        try {
          tree = parse(source);
        } catch (const Failure &e) {
          throw DiagnosticsFailure(Json::array({source->diagnostic(e)}));
        }
        if (cache)
          (*cache)[file] = {std::move(text), tree};
      }
    } catch (const DiagnosticsFailure &) {
      throw;
    } catch (const std::runtime_error &e) {
      fail("project.io", "cannot read module '" + visit.name + "': " + e.what(),
           visit.span, true);
    }
    parsed.emplace(visit.name, LoadedModule{visit.name, file, tree, {}});
    active.push_back(visit.name);
    stack.push_back({visit.name, visit.span, true});
    for (auto it = tree->items.rbegin(); it != tree->items.rend(); ++it) {
      auto *import = ast::as<ast::Import>(*it);
      if (!import)
        continue;
      auto target = ast::dotted(import->module);
      if (extensions.contains(target)) {
        for (const auto &root : config.search_paths())
          if (fs::is_regular_file(root / relative_module(target)))
            fail("import.ambiguous-module",
                 "'" + target +
                     "' names both an ERC namespace and a source module",
                 import->span);
      } else if (target != "noscheme")
        stack.push_back({target, import->span, false});
    }
  }
  Symbols builtin_symbols;
  for (const auto &key : builtins)
    builtin_symbols.emplace(key, Symbol{"noscheme", key});
  for (const auto &name : order) {
    auto module = std::move(parsed.at(name));
    auto &symbols = module.symbols;
    std::map<std::string, Span> locations;
    std::set<std::string> namespaces;
    for (const auto &item : module.ast->items)
      if (auto *definition = ast::as<ast::Definition>(item)) {
        const auto &key = definition->name.text;
        symbols.emplace(key, Symbol{name, key, definition});
        locations[key] = definition->name.span;
      }
    for (const auto &item : module.ast->items) {
      auto *import = ast::as<ast::Import>(item);
      if (!import)
        continue;
      auto target = ast::dotted(import->module);
      const auto &exports = target == "noscheme" ? builtin_symbols
                            : extensions.contains(target)
                                ? extensions.at(target)
                                : project.modules.at(target).symbols;
      auto duplicate = [&](const std::string &key, const Span &span) {
        fail("import.duplicate-name",
             "name '" + key + "' is already declared or imported", span, false,
             locations.at(key));
      };
      auto bind = [&](const std::string &key, const Symbol &symbol,
                      const Span &span) {
        if (symbols.contains(key) ||
            std::any_of(namespaces.begin(), namespaces.end(),
                        [&](const auto &ns) {
                          return key == ns || ns.starts_with(key + ".");
                        }))
          duplicate(key, span);
        symbols.emplace(key, symbol);
        locations[key] = span;
      };
      if (import->names.empty()) {
        std::vector<std::string> prefixes;
        for (std::size_t end = 0; end <= target.size(); ++end)
          if (end == target.size() || target[end] == '.')
            prefixes.push_back(target.substr(0, end));
        for (const auto &prefix : prefixes)
          if (symbols.contains(prefix) ||
              (prefix == target && namespaces.contains(prefix)))
            duplicate(prefix, import->span);
        for (const auto &prefix : prefixes) {
          namespaces.insert(prefix);
          locations.try_emplace(prefix, import->span);
        }
        for (const auto &[key, symbol] : exports)
          bind(target + "." + key, symbol, import->span);
      } else {
        for (const auto &imported : import->names) {
          const auto &original = imported.name.text;
          const auto &binding = imported.local();
          auto found = exports.find(original);
          if (found == exports.end())
            fail("import.missing-name",
                 "module '" + target + "' does not export '" + original + "'",
                 imported.name.span);
          bind(binding.text, found->second, binding.span);
        }
      }
    }
    module.ast = classified_module(name, *module.ast, symbols);
    project.modules.emplace(name, std::move(module));
  }
  const auto &board_symbols = project.modules.at(config.board_module).symbols;
  auto board = board_symbols.find(config.board_name);
  if (board == board_symbols.end() || !board->second.declaration ||
      board->second.declaration->keyword != "block")
    fail("project.board",
         config.board_module + "." + config.board_name +
             " must name a block definition",
         config.span);
  project.board = board->second;
  return project;
}

Json project_summary(const Project &project) {
  auto modules = Json::array();
  for (const auto &[name, module] : project.modules) {
    auto symbols = Json::object();
    for (const auto &[key, symbol] : module.symbols)
      symbols[key] =
          object("module", symbol.module, "name", symbol.name, "kind",
                 symbol.declaration ? symbol.declaration->keyword : "builtin");
    modules.push_back(object("name", name, "path", module.path.string(),
                             "symbols", std::move(symbols)));
  }
  auto erc_modules = Json::array();
  for (const auto &path : project.config.erc_modules)
    erc_modules.push_back(path.string());
  return object(
      "schema_version", 1, "stage", "project-loading", "config",
      project.config.path.string(), "language_version",
      project.config.language_version, "board",
      project.board.module + "." + project.board.name, "output_dir",
      project.config.output_dir.string(), "erc_modules", std::move(erc_modules),
      "modules", std::move(modules), "remaining_stages",
      Json::array({"definition-resolution", "elaboration", "ERC", "export"}));
}
} // namespace noscheme
