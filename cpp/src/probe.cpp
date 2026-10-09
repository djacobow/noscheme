#include "noscheme/circuit.hpp"
#include "noscheme/erc.hpp"
#include "noscheme/evaluate.hpp"
#include "noscheme/formatter.hpp"
#include "noscheme/metadata.hpp"
#include "noscheme/parameters.hpp"
#include "noscheme/project.hpp"
#include "noscheme/registry.hpp"
#include "noscheme/resolve.hpp"
#include "noscheme/source.hpp"
#include "noscheme/validation.hpp"
#include <chrono>
#include <iostream>
#include <iterator>
namespace {
noscheme::Limits expression_limits(const noscheme::Json &settings) {
  noscheme::Limits limits;
  limits.integer_bits = settings.value("integer_bits", limits.integer_bits);
  limits.string_length = settings.value("string_length", limits.string_length);
  limits.format_width = settings.value("format_width", limits.format_width);
  limits.format_precision =
      settings.value("format_precision", limits.format_precision);
  limits.expression_depth =
      settings.value("expression_depth", limits.expression_depth);
  limits.dimension = settings.value("dimension", limits.dimension);
  return limits;
}
// Follows a path of comparison-form keys and list positions, such as
// ["body", 2, "entries", 0], to the nodes it names.
std::vector<const noscheme::ast::Node *>
navigate(const noscheme::ast::Node &root, const noscheme::Json &path) {
  std::vector<const noscheme::ast::Node *> selected{&root};
  for (const auto &step : path) {
    if (step.is_number_integer()) {
      selected = {selected.at(step.get<std::size_t>())};
      continue;
    }
    const auto key = step.get<std::string>();
    std::vector<const noscheme::ast::Node *> children;
    noscheme::ast::for_each_child(
        *selected.at(0),
        [&](std::string_view name, const noscheme::ast::Node &child) {
          if (name == key)
            children.push_back(&child);
        });
    selected = std::move(children);
  }
  return selected;
}
} // namespace
// The probe accepts JSON lines to amortize process startup in differential
// tests.
int main() {
  std::string line;
  noscheme::ParsedSources parsed_sources;
  while (std::getline(std::cin, line)) {
    try {
      auto request = noscheme::Json::parse(line);
      if (request.contains("expression")) {
        auto source = std::make_shared<const noscheme::Source>(
            "component Eval:\n  params:\n    x: number = " +
                request["expression"].get<std::string>() + "\n",
            "expression.nosch");
        noscheme::Symbols symbols;
        for (const auto &[name, symbol] :
             request.value("symbols", noscheme::Json::object()).items())
          symbols[name] = {symbol.at("module"), symbol.at("name")};
        auto limits = expression_limits(
            request.value("limits", noscheme::Json::object()));
        noscheme::Evaluator evaluator(symbols, limits);
        evaluator.instance_path =
            request.value("instance_path", noscheme::Json(nullptr));
        try {
          using noscheme::ast::as;
          auto tree = noscheme::parse(source);
          const auto &definition =
              as<noscheme::ast::Definition>(*tree->items[0]);
          const auto &params = as<noscheme::ast::Section>(*definition.body[0]);
          const auto &expr =
              *as<noscheme::ast::Field>(*params.entries[0]).default_value;
          noscheme::Environment env;
          for (const auto &[name, value] :
               request.value("environment", noscheme::Json::object()).items())
            env[name] = noscheme::decoded(value);
          auto result = evaluator.evaluate(expr, env);
          auto context = request.value("context", "");
          if (context == "integer")
            result = evaluator.integer(result, expr.span);
          if (context == "dimension")
            result = noscheme::Integer(evaluator.dimension(result, expr.span));
          if (context == "index")
            result = noscheme::Integer(
                evaluator.index(result, expr.span, request.at("size"),
                                request.value("stop", false)));
          auto response = noscheme::object("value", noscheme::encoded(result),
                                           "warnings", evaluator.warnings);
          if (request.value("display", false))
            response["display"] = evaluator.display(result, expr.span);
          if (request.contains("quantity"))
            response["quantity"] =
                noscheme::format_quantity(result, request["quantity"]);
          std::cout << response.dump() << std::endl;
        } catch (const noscheme::Failure &e) {
          std::cout << noscheme::object(
                           "diagnostics",
                           noscheme::Json::array({source->diagnostic(e)}),
                           "warnings", evaluator.warnings)
                           .dump()
                    << std::endl;
        } catch (const noscheme::DiagnosticsFailure &e) {
          std::cout << noscheme::object("diagnostics", e.diagnostics,
                                        "warnings", evaluator.warnings)
                           .dump()
                    << std::endl;
        }
        continue;
      }
      if (request.contains("project")) {
        try {
          noscheme::Sources sources;
          for (const auto &[path, text] :
               request.value("sources", noscheme::Json::object()).items())
            sources[std::filesystem::weakly_canonical(
                std::filesystem::absolute(path))] = text;
          noscheme::ErcExportLoader exports;
          if (request.contains("erc_exports"))
            exports = [&](const noscheme::ProjectConfig &) {
              noscheme::ErcExports result;
              for (const auto &[space, names] : request["erc_exports"].items())
                for (const auto &[name, qualified] : names.items())
                  result[space][name] = {"<erc>", qualified.get<std::string>()};
              return result;
            };
          std::optional<noscheme::RegistryDescription> registry;
          if (request.contains("registry_response")) {
            registry = noscheme::registry_description(
                request.at("registry_response"), nullptr);
            exports = [&](const noscheme::ProjectConfig &) {
              return registry->exports();
            };
          }
          if (request.contains("registry_python")) {
            exports = [&](const noscheme::ProjectConfig &config) {
              registry = noscheme::load_registry(
                  config, request.at("registry_python").get<std::string>(),
                  std::chrono::milliseconds(
                      request.value("registry_timeout_ms", 30000)));
              return registry->exports();
            };
          }
          auto project =
              noscheme::load_project(request["project"].get<std::string>(),
                                     sources, &parsed_sources, exports);
          auto response =
              noscheme::object("project", noscheme::project_summary(project));
          if (registry)
            response["registry"] = registry->data;
          if (request.value("resolve_interfaces", false) ||
              request.value("resolve_all", false) ||
              request.value("elaborate", false)) {
            if (!registry)
              throw std::runtime_error("resolution requires registry");
            auto definitions =
                request.value("resolve_all", false) ||
                        request.value("elaborate", false)
                    ? noscheme::resolve_all(
                          project, *registry,
                          request.value("max_pins", std::size_t(100000)),
                          request.value("max_expression_depth",
                                        std::size_t(128)))
                    : noscheme::resolve_interfaces(
                          project, *registry,
                          request.value("max_pins", std::size_t(100000)));
            if (request.value("elaborate", false)) {
              noscheme::ExpansionLimits limits;
              auto settings =
                  request.value("expansion_limits", noscheme::Json::object());
              limits.instances = settings.value("instances", limits.instances);
              limits.leaves = settings.value("leaves", limits.leaves);
              limits.hierarchy_depth =
                  settings.value("hierarchy_depth", limits.hierarchy_depth);
              limits.type_depth =
                  settings.value("type_depth", limits.type_depth);
              auto circuit = noscheme::elaborate_project(
                  project, *registry, definitions, limits,
                  expression_limits(request.value("expression_limits",
                                                  noscheme::Json::object())));
              response["circuit"] = noscheme::to_json(circuit);
              if (request.contains("erc_python")) {
                auto result = noscheme::run_erc(
                    project.config, *registry, circuit,
                    request.at("erc_python").get<std::string>(),
                    request.value("configurations", noscheme::Json(nullptr)),
                    std::chrono::milliseconds(
                        request.value("erc_timeout_ms", 30000)));
                response["circuit"]["erc"] = std::move(result.report);
                response["circuit"]["erc_performed"] = true;
                response["reports"] =
                    noscheme::object("erc.json", std::move(result.json_report),
                                     "erc.txt", std::move(result.text_report));
              }
            } else
              response["definitions"] =
                  noscheme::interface_summary(definitions);
          }
          if (request.contains("metadata")) {
            if (!registry)
              throw std::runtime_error("metadata requires a registry snapshot");
            auto read_metadata = [&](const noscheme::Json &options) {
              const std::string module = options.at("module");
              const auto &symbol = project.modules.at(module).symbols.at(
                  options.at("definition"));
              const std::string scope = options.at("scope");
              auto locals = options.value("locals", std::set<std::string>{});
              noscheme::MetadataReader reader(project, *registry, symbol.module,
                                              locals);
              using noscheme::ast::as;
              noscheme::MetaFields value;
              for (const auto &node : symbol.declaration->body) {
                auto *section = as<noscheme::ast::Section>(node);
                if (!section || options.contains("path"))
                  continue;
                if (options.contains("pin") && section->name == "pins") {
                  for (const auto *field :
                       noscheme::ast::field_list(section->entries))
                    if (noscheme::Json(field->key.first.text) ==
                        options.at("pin"))
                      value = reader.pin(*field, scope);
                } else if (!options.contains("pin") &&
                           section->name == "metadata")
                  value = reader.read(section->entries, scope);
              }
              if (options.contains("path")) {
                std::vector<const noscheme::ast::Field *> fields;
                for (const auto *node :
                     navigate(*symbol.declaration, options.at("path")))
                  fields.push_back(&as<noscheme::ast::Field>(*node));
                if (options.value("as_pin", false))
                  value = reader.pin(*fields.at(0), scope);
                else
                  value = reader.read(fields, scope);
              }
              if (options.contains("parent_definition")) {
                const auto &parent = project.modules.at(module).symbols.at(
                    options.at("parent_definition"));
                for (const auto &node : parent.declaration->body)
                  if (auto *section =
                          noscheme::ast::as<noscheme::ast::Section>(node);
                      section && section->name == "metadata")
                    value = noscheme::merge_metadata(
                        reader.read(section->entries, scope), value);
              }
              return noscheme::to_json(value);
            };
            if (request.at("metadata").is_array()) {
              auto values = noscheme::Json::array();
              for (const auto &options : request.at("metadata"))
                values.push_back(read_metadata(options));
              response["metadata"] = std::move(values);
            } else
              response["metadata"] = read_metadata(request.at("metadata"));
          }
          if (request.value("resolve_parameters", false) ||
              request.contains("bind")) {
            auto definitions = noscheme::resolve_parameters(project);
            response["parameters"] = noscheme::parameter_summary(definitions);
            if (request.contains("bind")) {
              const auto &binding = request.at("bind");
              const auto module = binding.at("module").get<std::string>();
              const auto &symbol = project.modules.at(module).symbols.at(
                  binding.at("definition"));
              const noscheme::ast::Instance *instance = nullptr;
              for (const auto &node : symbol.declaration->body)
                if (auto *candidate =
                        noscheme::ast::as<noscheme::ast::Instance>(node);
                    candidate && noscheme::Json(candidate->name.text) ==
                                     binding.at("instance"))
                  instance = candidate;
              if (!instance)
                throw std::runtime_error("probe instance not found");
              auto key = noscheme::definition_symbol(project, symbol.module,
                                                     instance->component,
                                                     {"component", "block"});
              std::vector<const noscheme::ast::Argument *> arguments;
              for (const auto &section : instance->sections)
                if (section.name == "params")
                  for (const auto &arg : section.entries)
                    arguments.push_back(
                        &noscheme::ast::as<noscheme::ast::Argument>(*arg));
              noscheme::Environment environment;
              for (const auto &[name, value] :
                   binding.value("environment", noscheme::Json::object())
                       .items())
                environment[name] = noscheme::decoded(value);
              noscheme::ParameterEvaluator evaluator(project);
              evaluator.instance_path =
                  binding.value("instance_path", noscheme::Json(nullptr));
              try {
                const auto &definition = definitions.at(key);
                auto bound =
                    evaluator.bind(definition, arguments, symbol.module,
                                   environment, instance->span);
                auto values = noscheme::Json::object();
                for (const auto &[name, value] : bound)
                  values[name] = noscheme::encoded(value);
                response["bound"] = std::move(values);
                auto value = evaluator.part_value(definition, bound);
                response["part_value"] =
                    value ? noscheme::encoded(*value) : noscheme::Json(nullptr);
                response["warnings"] = evaluator.warnings;
              } catch (const noscheme::DiagnosticsFailure &e) {
                response = noscheme::object("diagnostics", e.diagnostics,
                                            "warnings", evaluator.warnings);
              }
            }
          }
          if (request.value("include_asts", false)) {
            auto asts = noscheme::Json::object();
            for (const auto &[name, module] : project.modules)
              asts[name] = noscheme::ast::to_json(*module.ast);
            response["asts"] = std::move(asts);
          }
          std::cout << response.dump() << std::endl;
        } catch (const noscheme::DiagnosticsFailure &e) {
          std::cout << noscheme::object("diagnostics", e.diagnostics,
                                        "exit_code", e.exit_code)
                           .dump()
                    << std::endl;
        }
        continue;
      }
      if (request.contains("benchmark_iterations")) {
        auto samples = noscheme::Json::array();
        auto phases = noscheme::Json::array();
        const auto text = request.at("source").get<std::string>();
        const auto filename = request.value("filename", "<input>");
        const int iterations = request.at("benchmark_iterations");
        if (iterations < 1 || iterations > 10000)
          throw std::runtime_error("benchmark_iterations must be 1..10000");
        for (int i = 0; i < iterations; ++i) {
          noscheme::ParseTimings timings;
          const auto start = std::chrono::steady_clock::now();
          {
            auto ast = noscheme::parse(
                std::make_shared<const noscheme::Source>(text, filename),
                &timings);
          }
          samples.push_back(std::chrono::duration<double>(
                                std::chrono::steady_clock::now() - start)
                                .count());
          phases.push_back({{"comments", timings.comments},
                            {"tokens", timings.tokens},
                            {"syntax", timings.syntax},
                            {"ast", timings.ast},
                            {"validation", timings.validation}});
        }
        std::cout
            << noscheme::Json{{"seconds", samples}, {"phases", phases}}.dump()
            << std::endl;
        continue;
      }
      auto source = std::make_shared<const noscheme::Source>(
          request.at("source"), request.value("filename", "<input>"));
      try {
        std::cout << (request.value("format", false)
                          ? noscheme::Json{{"formatted",
                                            noscheme::format_source(*source)}}
                          : noscheme::Json{{"ast",
                                            noscheme::ast::to_json(
                                                *noscheme::parse(source))}})
                         .dump()
                  << '\n';
      } catch (const noscheme::DiagnosticsFailure &e) {
        std::cout << noscheme::Json{{"diagnostics", e.diagnostics}}.dump()
                  << '\n';
      } catch (const noscheme::Failure &e) {
        std::cout << noscheme::Json{{"diagnostics",
                                     noscheme::Json::array(
                                         {source->diagnostic(e)})}}
                         .dump()
                  << '\n';
      }
    } catch (const std::exception &e) {
      std::cout << noscheme::Json{{"error", e.what()}}.dump() << '\n';
    }
    std::cout.flush();
  }
}
