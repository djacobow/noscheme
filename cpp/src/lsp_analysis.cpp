#include "noscheme/lsp_analysis.hpp"
#include "noscheme/circuit.hpp"
#include "noscheme/erc.hpp"
#include "noscheme/validation.hpp"
#include <fstream>
#include <set>
namespace noscheme {
bool inputs_current(const LspAnalysis &analysis) {
  for (const auto &[path, expected] : analysis.disk_inputs) {
    std::optional<std::string> actual;
    try {
      actual = source_text(path);
    } catch (const std::exception &) {
    }
    if (actual != expected)
      return false;
  }
  return true;
}
std::optional<fs::path> uri_path(const std::string &uri) {
  std::string value;
  if (uri.starts_with("file://localhost/"))
    value = uri.substr(16);
  else if (uri.starts_with("file:///"))
    value = uri.substr(7);
  else
    return {};
  std::string decoded;
  auto hex = [](char c) -> int {
    if (c >= '0' && c <= '9')
      return c - '0';
    if (c >= 'a' && c <= 'f')
      return c - 'a' + 10;
    if (c >= 'A' && c <= 'F')
      return c - 'A' + 10;
    return -1;
  };
  for (std::size_t i = 0; i < value.size(); ++i) {
    if (value[i] == '?' || value[i] == '#')
      return {};
    if (value[i] != '%')
      decoded += value[i];
    else {
      if (i + 2 >= value.size() || hex(value[i + 1]) < 0 ||
          hex(value[i + 2]) < 0)
        return {};
      char c = static_cast<char>(hex(value[i + 1]) * 16 + hex(value[i + 2]));
      if (!c)
        return {};
      decoded += c;
      i += 2;
    }
  }
  return fs::weakly_canonical(fs::absolute(decoded));
}
std::string file_uri(const fs::path &path) {
  std::string uri = "file://";
  constexpr char hex[] = "0123456789ABCDEF";
  for (unsigned char c : path.string()) {
    if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
        (c >= '0' && c <= '9') || c == '/' || c == '-' || c == '_' ||
        c == '.' || c == '~')
      uri += c;
    else {
      uri += '%';
      uri += hex[c >> 4];
      uri += hex[c & 15];
    }
  }
  return uri;
}
LspAnalysis
analyze_documents(const Sources &sources, const fs::path &explicit_project,
                  const fs::path &python, ParsedSources &cache,
                  std::stop_token stop,
                  const std::function<void(const LspAnalysis &)> &ready,
                  const std::vector<fs::path> &workspace_roots,
                  std::optional<RegistryDescription> *standard_registry) {
  LspAnalysis result;
  result.sources = sources;
  auto capture_input = [&](const fs::path &path) {
    if (sources.contains(path))
      return;
    std::optional<std::string> text;
    try {
      text = source_text(path);
    } catch (const std::exception &) {
    }
    result.disk_inputs[path] = std::move(text);
  };
  // Built-ins are fixed for this server process, like imported Python code.
  // User registries are always reloaded, including their transitive imports.
  auto registry_for = [&](const ProjectConfig &settings) {
    if (settings.erc_modules.empty() && standard_registry && *standard_registry)
      return **standard_registry;
    auto registry =
        load_registry(settings, python, std::chrono::seconds(30), stop);
    if (settings.erc_modules.empty() && standard_registry)
      *standard_registry = registry;
    return registry;
  };
  std::set<fs::path> configs, indexed;
  std::optional<RegistryDescription> local_registry;
  struct ErcJob {
    ProjectConfig config;
    RegistryDescription registry;
    std::shared_ptr<const Circuit> circuit;
  };
  std::vector<ErcJob> erc_jobs;
  std::optional<std::vector<ProjectConfig>> discovered;
  UniqueDiagnostics diagnostics;
  auto append = [&](const Json &items) { diagnostics.add_all(items); };
  auto io_error = [&](const fs::path &path, const std::exception &e) {
    Source empty("", path.string());
    append(Json::array({empty.diagnostic(Failure("lsp.io", e.what(), 0, 0))}));
  };
  for (const auto &[path, text] : sources) {
    check_cancelled(stop);
    try {
      if (!explicit_project.empty())
        configs.insert(explicit_project);
      else {
        bool selected = false;
        auto parent = path.parent_path();
        while (!parent.empty()) {
          auto candidate = parent / "noscheme.toml";
          if (sources.contains(candidate) || fs::exists(candidate)) {
            configs.insert(candidate);
            selected = true;
            break;
          }
          auto next = parent.parent_path();
          if (next == parent)
            break;
          parent = next;
        }
        if (!selected && !workspace_roots.empty()) {
          if (!discovered)
            discovered = workspace_projects(workspace_roots, sources, stop);
          if (auto candidate = workspace_project(path, *discovered))
            configs.insert(*candidate);
        }
      }
      if (path.extension() != ".nosch")
        continue;
      auto found = cache.find(path);
      if (found == cache.end() || found->second.text != text) {
        auto source = std::make_shared<const Source>(text, path.string());
        try {
          cache[path] = {text, parse(source)};
        } catch (const Failure &e) {
          append(Json::array({source->diagnostic(e)}));
        }
      }
    } catch (const Cancelled &) {
      throw;
    } catch (const DiagnosticsFailure &e) {
      append(e.diagnostics);
    } catch (const std::exception &e) {
      io_error(path, e);
    }
  }
  for (const auto &config : configs) {
    check_cancelled(stop);
    capture_input(fs::is_directory(config) ? config / "noscheme.toml" : config);
    std::optional<RegistryDescription> registry;
    bool loaded = false;
    try {
      auto project = load_project(
          config, sources, &cache, [&](const ProjectConfig &settings) {
            for (const auto &path : settings.erc_modules)
              capture_input(path);
            registry = registry_for(settings);
            return registry->exports();
          });
      loaded = true;
      result.retain(project);
      result.completions.merge(completion_catalog(project, *registry));
      for (const auto &[name, module] : project.modules)
        if (!result.sources.contains(module.path))
          result.sources[module.path] = cache.at(module.path).text;
      check_cancelled(stop);
      auto definitions = resolve_interfaces(project, *registry);
      interface_completions(result.completions, project, *registry,
                            definitions);
      auto references = reference_index(project, definitions, *registry);
      result.references.insert(result.references.end(), references.begin(),
                               references.end());
      for (const auto &[name, module] : project.modules)
        indexed.insert(module.path);
      check_cancelled(stop);
      resolve_bodies(project, *registry, definitions);
      auto rename = std::make_shared<RenameContext>(RenameContext{
          std::move(project), *registry, std::move(definitions), nullptr});
      result.rename_contexts.push_back(rename);
      auto circuit = std::make_shared<const Circuit>(
          elaborate_project(rename->project, *registry, rename->definitions));
      elaborate_hover(result.references, rename->project, *circuit);
      append(circuit->warnings);
      rename->circuit = circuit;
      erc_jobs.push_back({rename->project.config, *registry, circuit});
    } catch (const Cancelled &) {
      throw;
    } catch (const DiagnosticsFailure &e) {
      append(e.diagnostics);
    } catch (const std::exception &e) {
      io_error(config, e);
    }
    if (registry) {
      local_registry = registry;
      Sources recovered = sources;
      std::map<fs::path, std::vector<std::size_t>> offsets;
      for (auto &[path, text] : recovered)
        if (path.extension() == ".nosch")
          text = completion_recovery(text, &offsets[path], !loaded);
      if (recovered != sources) {
        check_cancelled(stop);
        try {
          // Recovery supplies names only: never diagnostics, references, or
          // ERC.
          auto project = load_project(
              config, recovered, nullptr,
              [&](const ProjectConfig &) { return registry->exports(); });
          result.retain(project);
          auto catalog = completion_catalog(project, *registry);
          try {
            auto definitions = resolve_interfaces(project, *registry);
            interface_completions(catalog, project, *registry, definitions);
          } catch (const DiagnosticsFailure &) {
            // Imported names remain available when an interface is incomplete.
          }
          // Recovered AST scopes refer to inserted text; completion cursors
          // still refer to the original editor buffer. Remapped spans keep
          // the recovered source for their filename, so only their offsets
          // are meaningful.
          for (auto &[path, entries] : catalog) {
            if (!offsets.contains(path))
              continue;
            const auto &mapping = offsets.at(path);
            auto remap = [&](Span &span) {
              if (span) {
                span.start = mapping.at(span.start);
                span.end = mapping.at(span.end);
              }
            };
            for (auto &entry : entries) {
              remap(entry.visible_in);
              for (auto &span : entry.shadowed_in)
                remap(span);
            }
          }
          for (auto &[path, entries] : catalog)
            result.completions.insert_or_assign(path, std::move(entries));
        } catch (const Cancelled &) {
          throw;
        } catch (const std::exception
                     &) { /* Unrelated incomplete syntax remains unsupported. */
        }
      }
    }
  }
  check_cancelled(stop);
  bool need_local =
      std::any_of(sources.begin(), sources.end(), [&](const auto &entry) {
        return entry.first.extension() == ".nosch" &&
               !indexed.contains(entry.first);
      });
  if (need_local) {
    try {
      if (!local_registry) {
        ProjectConfig settings;
        settings.source =
            std::make_shared<const Source>("", sources.begin()->first.string());
        settings.span = {settings.source.get(), 0, 0};
        local_registry = registry_for(settings);
      }
      local_index(result, sources, cache, *local_registry, indexed, stop);
    } catch (const Cancelled &) {
      throw;
    } catch (const DiagnosticsFailure &) { /* Keep the original diagnostics. */
    }
  }
  for (const auto &[path, text] : result.sources)
    if (!sources.contains(path))
      result.disk_inputs[path] = text;
  // Interactive snapshots are complete before executing user ERC callbacks.
  // Diagnostics are published only after the full check, with version guards.
  result.diagnostics = diagnostics.items();
  if (ready)
    ready(result);
  for (const auto &job : erc_jobs) {
    check_cancelled(stop);
    try {
      append(run_erc(job.config, job.registry, *job.circuit, python, nullptr,
                     std::chrono::seconds(30), stop)
                 .report.at("diagnostics"));
    } catch (const Cancelled &) {
      throw;
    } catch (const DiagnosticsFailure &e) {
      append(e.diagnostics);
    } catch (const std::exception &e) {
      io_error(job.config.path, e);
    }
  }
  result.diagnostics = diagnostics.take();
  for (const auto &d : result.diagnostics) {
    auto capture = [&](const Json &span) {
      fs::path path = span.at("filename").get<std::string>();
      if (result.sources.contains(path))
        return;
      std::ifstream input(path);
      if (input)
        result.sources[path] =
            std::string(std::istreambuf_iterator<char>(input), {});
    };
    capture(d.at("span"));
    for (const auto &related : d.value("related", Json::array()))
      capture(related.at("span"));
  }
  // Bound reuse to the current analysis rather than accumulating closed
  // projects.
  std::erase_if(cache, [&](const auto &entry) {
    return !result.sources.contains(entry.first);
  });
  return result;
}
} // namespace noscheme
