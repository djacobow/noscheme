// Private JSON-lines adapter for the shared Python CLI front end. Compiler and
// export semantics live in the native library; publication stays in one place.
#include "noscheme/export.hpp"
#include "noscheme/formatter.hpp"
#include "noscheme/inspect.hpp"
#include "noscheme/validation.hpp"
#include <iostream>

namespace noscheme {
namespace {
Json run(const Json &request) {
  const std::string command = request.at("command");
  if (command == "parse" || command == "format") {
    auto source = std::make_shared<const Source>(request.at("source"),
                                                 request.at("filename"));
    try {
      if (command == "format")
        return object("result", format_source(*source));
      return object("result", object("schema_version", 1, "module",
                                     ast::to_json(*parse(source))));
    } catch (const Failure &error) {
      throw DiagnosticsFailure(Json::array({source->diagnostic(error)}));
    }
  }
  std::optional<RegistryDescription> registry;
  const fs::path python = request.at("python").get<std::string>();
  auto project = load_project(request.at("project").get<std::string>(), {},
                              nullptr, [&](const ProjectConfig &config) {
                                registry = load_registry(config, python);
                                return registry->exports();
                              });
  if (command == "load")
    return object("result", project_summary(project));
  if (!registry)
    registry = load_registry(project.config, python);
  auto definitions = resolve_all(project, *registry);
  if (command == "resolve") {
    auto values = interface_summary(definitions);
    for (auto &definition : values) {
      for (auto &[name, parameter] : definition["parameters"].items())
        parameter.erase("span");
      definition.erase("body");
      definition.erase("electrical");
    }
    return object("result",
                  object("schema_version", 1, "stage", "definition-resolution",
                         "board",
                         project.board.module + "." + project.board.name,
                         "definitions", values, "remaining_stages",
                         Json::array({"expression-evaluation", "elaboration",
                                      "ERC", "export"})));
  }
  if (command == "evaluate") {
    auto result = evaluate_templates(project, definitions);
    return object("result", result, "diagnostics", result["warnings"]);
  }
  if (command == "lint")
    return object("result", nullptr, "diagnostics",
                  unused_parameters(definitions));
  auto circuit = elaborate_project(project, *registry, definitions);
  auto erc = run_erc(project.config, *registry, circuit, python,
                     request.value("configurations", Json()));
  auto diagnostics = circuit.warnings;
  for (const auto &diagnostic : erc.report.at("diagnostics"))
    diagnostics.push_back(diagnostic);
  if (command == "export-kicad") {
    auto exported = export_kicad(
        project, circuit, erc, request.at("state"), request.at("footprints"),
        request.at("records").get<std::string>(), request.at("records_text"));
    for (const auto &warning : exported.warnings)
      diagnostics.push_back(warning);
    return object("result", exported.summary, "diagnostics", diagnostics,
                  "files", exported.files);
  }
  if (command != "check" && command != "compile")
    throw std::invalid_argument("unsupported native command: " + command);
  auto summary =
      object("stage", "circuit", "parts", circuit.parts.size(), "nets",
             circuit.nets.size(), "erc_performed", true, "erc_errors",
             erc.report["errors"], "erc_warnings", erc.report["warnings"],
             "erc_pending", erc.report["pending"], "erc_configurations",
             erc.report["configurations"]);
  auto files = Json::array();
  if (command == "compile") {
    auto data = to_json(circuit);
    data["erc"] = erc.report;
    data["erc_performed"] = true;
    auto out = project.config.output_dir;
    files.push_back(Json::array(
        {(out / "circuit.json").string(), data.dump(2, ' ', true) + "\n"}));
    files.push_back(
        Json::array({(out / "erc.json").string(), erc.json_report}));
    files.push_back(Json::array({(out / "erc.txt").string(), erc.text_report}));
    std::set<fs::path> protected_paths{
        fs::weakly_canonical(project.config.path)};
    for (const auto &[name, module] : project.modules)
      protected_paths.insert(fs::weakly_canonical(module.path));
    for (const auto &file : files)
      if (protected_paths.contains(
              fs::weakly_canonical(fs::path(file[0].get<std::string>()))))
        return object("io_error",
                      "circuit output must not overwrite project inputs",
                      "exit_code", 2);
    summary["output"] = (out / "circuit.json").string();
  }
  return object(
      "result", summary, "diagnostics", diagnostics, "files", files,
      "exit_code",
      command == "check" && erc.report["errors"].get<std::size_t>() > 0 ? 1
                                                                        : 0);
}
} // namespace
} // namespace noscheme
int main() {
  std::string line;
  while (std::getline(std::cin, line)) {
    noscheme::Json response;
    try {
      response = noscheme::run(noscheme::Json::parse(line));
    } catch (const noscheme::DiagnosticsFailure &error) {
      response = noscheme::object("diagnostics", error.diagnostics, "exit_code",
                                  error.exit_code);
    } catch (const std::exception &error) {
      response = noscheme::object("io_error", error.what(), "exit_code", 2);
    }
    std::cout << response.dump() << std::endl;
  }
}
