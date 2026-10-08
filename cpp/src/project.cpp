#include "noscheme/project.hpp"
#include "noscheme/validation.hpp"
#include <algorithm>
#include <fstream>
#include <regex>
#include <set>
#include <sstream>
#include <toml++/toml.hpp>

namespace noscheme {
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
const std::set<std::string> builtins{"role", "nettype", "unused",
                                     "num",  "bias",    "std"};
fs::path resolved(const fs::path &path) {
  return fs::weakly_canonical(fs::absolute(path));
}
[[noreturn]] void fail(std::string code, std::string message, const Json &span,
                       bool config = false, const Json &previous = nullptr) {
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
std::string dotted(const Json &names) {
  std::string result;
  for (const auto &name : names) {
    if (!result.empty())
      result += '.';
    result += name["text"].get<std::string>();
  }
  return result;
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
                     const Json &span, const Sources &sources) {
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

ProjectConfig read_config(fs::path path, const Sources &sources) {
  if (fs::is_directory(path))
    path /= "noscheme.toml";
  path = resolved(path);
  std::string text;
  try {
    text = read_source(path, sources);
  } catch (const std::runtime_error &e) {
    fail("project.io", e.what(), Source("", path.string()).span(0, 0), true);
  }
  auto source = [&] {
    try {
      return Source(text, path.string());
    } catch (const Failure &e) {
      fail("project.io",
           "cannot decode configuration: " + std::string(e.what()),
           Source("", path.string()).span(0, 0), true);
    }
  }();
  const auto file_span = source.span(0, text.size());
  auto span = [&](const std::string &key) {
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
    std::smatch match;
    return std::regex_search(text, match, pattern)
               ? source.span(match.position(),
                             match.position() + match.length())
               : file_span;
  };
  auto invalid = [&](const std::string &key, const std::string &message) {
    fail("project.config", message, span(key), true);
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
  const std::set<std::string> required{"language_version", "board",
                                       "source_paths", "library_paths",
                                       "output_dir"};
  for (const auto &[key, value] : data)
    if (!required.contains(std::string(key.str())) && key != "erc_modules" &&
        key != "erc_rule_sets")
      invalid(std::string(key.str()),
              "unknown configuration key '" + std::string(key.str()) + "'");
  for (const auto &key : required)
    if (!data.contains(key))
      invalid(key, "missing required configuration key '" + key + "'");
  if (data["language_version"].value<std::string>() != "0.1")
    invalid("language_version", "language_version must be the string \"0.1\"");
  auto board = data["board"].value<std::string>();
  if (!board || !std::regex_match(*board, qualified) ||
      board->find('.') == std::string::npos)
    invalid("board",
            "board must name a module and definition, such as \"main.Blink\"");
  if (reserved_module(*board))
    invalid("board",
            "noscheme is reserved for builtins and cannot contain the board");
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
  auto output = data["output_dir"].value<std::string>();
  if (!output || output->empty() || output->find('\0') != std::string::npos)
    invalid("output_dir", "output_dir must be a nonempty path string");
  config.output_dir = resolved(path.parent_path() / *output);
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
  config.span = span("board");
  return config;
}

Project load_project(fs::path path, const Sources &sources,
                     ParsedSources *cache, const ErcExportLoader &erc_loader) {
  Project project;
  project.config = read_config(path, sources);
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
    Json span;
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
    std::shared_ptr<const Json> tree;
    try {
      auto text = read_source(file, sources);
      if (cache && cache->contains(file) && cache->at(file).text == text)
        tree = cache->at(file).ast;
      else {
        Source source(text, file.string());
        try {
          tree = std::make_shared<const Json>(parse(source));
        } catch (const Failure &e) {
          throw DiagnosticsFailure(Json::array({source.diagnostic(e)}));
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
    for (auto it = (*tree)["items"].rbegin(); it != (*tree)["items"].rend();
         ++it) {
      if ((*it)["node"] != "Import")
        continue;
      auto target = dotted((*it)["module"]);
      if (extensions.contains(target)) {
        for (const auto &root : config.search_paths())
          if (fs::is_regular_file(root / relative_module(target)))
            fail("import.ambiguous-module",
                 "'" + target +
                     "' names both an ERC namespace and a source module",
                 (*it)["span"]);
      } else if (target != "noscheme")
        stack.push_back({target, (*it)["span"], false});
    }
  }
  Symbols builtin_symbols;
  for (const auto &key : builtins)
    builtin_symbols.emplace(key, Symbol{"noscheme", key});
  for (const auto &name : order) {
    auto module = std::move(parsed.at(name));
    auto &symbols = module.symbols;
    std::map<std::string, Json> locations;
    std::set<std::string> namespaces;
    for (const auto &item : (*module.ast)["items"])
      if (item["node"] == "Definition") {
        auto key = item["name"]["text"].get<std::string>();
        symbols.emplace(key, Symbol{name, key, &item});
        locations[key] = item["name"]["span"];
      }
    for (const auto &imp : (*module.ast)["items"]) {
      if (imp["node"] != "Import")
        continue;
      auto target = dotted(imp["module"]);
      const auto &exports = target == "noscheme" ? builtin_symbols
                            : extensions.contains(target)
                                ? extensions.at(target)
                                : project.modules.at(target).symbols;
      auto duplicate = [&](const std::string &key, const Json &span) {
        fail("import.duplicate-name",
             "name '" + key + "' is already declared or imported", span, false,
             locations.at(key));
      };
      auto bind = [&](const std::string &key, const Symbol &symbol,
                      const Json &span) {
        if (symbols.contains(key) ||
            std::any_of(namespaces.begin(), namespaces.end(),
                        [&](const auto &ns) {
                          return key == ns || ns.starts_with(key + ".");
                        }))
          duplicate(key, span);
        symbols.emplace(key, symbol);
        locations[key] = span;
      };
      if (imp["names"].empty()) {
        std::vector<std::string> prefixes;
        for (std::size_t end = 0; end <= target.size(); ++end)
          if (end == target.size() || target[end] == '.')
            prefixes.push_back(target.substr(0, end));
        for (const auto &prefix : prefixes)
          if (symbols.contains(prefix) ||
              (prefix == target && namespaces.contains(prefix)))
            duplicate(prefix, imp["span"]);
        for (const auto &prefix : prefixes) {
          namespaces.insert(prefix);
          locations.try_emplace(prefix, imp["span"]);
        }
        for (const auto &[key, symbol] : exports)
          bind(target + "." + key, symbol, imp["span"]);
      } else {
        for (const auto &imported : imp["names"]) {
          auto original = imported["name"]["text"].get<std::string>();
          const auto &binding = imported["alias"].is_null() ? imported["name"]
                                                            : imported["alias"];
          auto found = exports.find(original);
          if (found == exports.end())
            fail("import.missing-name",
                 "module '" + target + "' does not export '" + original + "'",
                 imported["name"]["span"]);
          bind(binding["text"], found->second, binding["span"]);
        }
      }
    }
    project.modules.emplace(name, std::move(module));
  }
  const auto &board_symbols = project.modules.at(config.board_module).symbols;
  auto board = board_symbols.find(config.board_name);
  if (board == board_symbols.end() || !board->second.declaration ||
      (*board->second.declaration)["kind"] != "board")
    fail("project.board",
         config.board_module + "." + config.board_name +
             " must name a board definition",
         config.span);
  project.board = board->second;
  return project;
}

Json project_summary(const Project &project) {
  auto modules = Json::array();
  for (const auto &[name, module] : project.modules) {
    auto symbols = Json::object();
    for (const auto &[key, symbol] : module.symbols)
      symbols[key] = object(
          "module", symbol.module, "name", symbol.name, "kind",
          symbol.declaration ? (*symbol.declaration)["kind"] : Json("builtin"));
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
