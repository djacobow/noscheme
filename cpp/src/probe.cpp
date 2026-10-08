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
        noscheme::Source source("component Eval:\n  params:\n    x: number = " +
                                    request["expression"].get<std::string>() +
                                    "\n",
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
          auto tree = noscheme::parse(source);
          const auto &expr =
              tree["items"][0]["body"][0]["entries"][0]["default"];
          noscheme::Environment env;
          for (const auto &[name, value] :
               request.value("environment", noscheme::Json::object()).items())
            env[name] = noscheme::decoded(value);
          auto result = evaluator.evaluate(expr, env);
          auto context = request.value("context", "");
          if (context == "integer")
            result = evaluator.integer(result, expr["span"]);
          if (context == "dimension")
            result =
                noscheme::Integer(evaluator.dimension(result, expr["span"]));
          if (context == "index")
            result = noscheme::Integer(
                evaluator.index(result, expr["span"], request.at("size"),
                                request.value("stop", false)));
          auto response = noscheme::object("value", noscheme::encoded(result),
                                           "warnings", evaluator.warnings);
          if (request.value("display", false))
            response["display"] = evaluator.display(result, expr["span"]);
          if (request.contains("quantity"))
            response["quantity"] =
                noscheme::format_quantity(result, request["quantity"]);
          std::cout << response.dump() << std::endl;
        } catch (const noscheme::Failure &e) {
          std::cout << noscheme::object(
                           "diagnostics",
                           noscheme::Json::array({source.diagnostic(e)}),
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
              response["circuit"] = noscheme::elaborate_project(
                  project, *registry, definitions, limits,
                  expression_limits(request.value("expression_limits",
                                                  noscheme::Json::object())));
              if (request.contains("erc_python")) {
                auto result = noscheme::run_erc(
                    project.config, *registry, response.at("circuit"),
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
              auto value = noscheme::Json::object();
              for (const auto &section : symbol.declaration->at("body")) {
                if (section.at("node") != "Section" || options.contains("path"))
                  continue;
                if (options.contains("pin") && section.at("name") == "pins") {
                  for (const auto &field : section.at("entries"))
                    if (field.at("key").at("first").at("text") ==
                        options.at("pin"))
                      value = reader.pin(field, scope);
                } else if (!options.contains("pin") &&
                           section.at("name") == "metadata")
                  value = reader.read(section.at("entries"), scope);
              }
              if (options.contains("path")) {
                const noscheme::Json *selected = symbol.declaration;
                for (const auto &step : options.at("path"))
                  selected = step.is_string()
                                 ? &selected->at(step.get<std::string>())
                                 : &selected->at(step.get<std::size_t>());
                if (options.value("as_pin", false))
                  value = reader.pin(*selected, scope);
                else
                  value = reader.read(options.value("single", false)
                                          ? noscheme::Json::array({*selected})
                                          : *selected,
                                      scope);
              }
              if (options.contains("parent"))
                value = noscheme::merge_metadata(options.at("parent"), value);
              return value;
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
              const noscheme::Json *instance = nullptr;
              for (const auto &node : symbol.declaration->at("body"))
                if (node.at("node") == "Instance" &&
                    node.at("name").at("text") == binding.at("instance"))
                  instance = &node;
              if (!instance)
                throw std::runtime_error("probe instance not found");
              auto key = noscheme::definition_symbol(project, symbol.module,
                                                     instance->at("component"),
                                                     {"component", "block"});
              auto arguments = noscheme::Json::array();
              for (const auto &section : instance->at("sections"))
                if (section.at("name") == "params")
                  for (const auto &arg : section.at("entries"))
                    arguments.push_back(arg);
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
                                   environment, instance->at("span"));
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
              asts[name] = *module.ast;
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
            noscheme::Source source(text, filename);
            auto ast = noscheme::parse(source, &timings);
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
      noscheme::Source source(request.at("source"),
                              request.value("filename", "<input>"));
      try {
        std::cout << (request.value("format", false)
                          ? noscheme::Json{{"formatted",
                                            noscheme::format_source(source)}}
                          : noscheme::Json{{"ast", noscheme::parse(source)}})
                         .dump()
                  << '\n';
      } catch (const noscheme::DiagnosticsFailure &e) {
        std::cout << noscheme::Json{{"diagnostics", e.diagnostics}}.dump()
                  << '\n';
      } catch (const noscheme::Failure &e) {
        std::cout << noscheme::Json{{"diagnostics",
                                     noscheme::Json::array(
                                         {source.diagnostic(e)})}}
                         .dump()
                  << '\n';
      }
    } catch (const std::exception &e) {
      std::cout << noscheme::Json{{"error", e.what()}}.dump() << '\n';
    }
    std::cout.flush();
  }
}
