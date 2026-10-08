#include "noscheme/erc.hpp"
#include "noscheme/python_bridge.hpp"
#include "noscheme/validation.hpp"
namespace noscheme {
ErcResult run_erc(const ProjectConfig &config,
                  const RegistryDescription &registry, const Json &circuit,
                  const fs::path &python, const Json &configurations,
                  std::chrono::milliseconds timeout, std::stop_token stop) {
  const std::string code = "native.erc-bridge";
  auto paths = Json::array();
  for (const auto &path : config.erc_modules)
    paths.push_back(path.string());
  auto request = object("version", 1, "method", "check_circuit", "erc_modules",
                        paths, "span", config.span, "registry", registry.data,
                        "circuit", circuit, "configurations", configurations);
  auto response =
      python_request(request, python, config.span, timeout, code, stop);
  validate_bridge_response(response, config.span, code);
  try {
    const auto &report = response.at("erc");
    if (!report.is_object() ||
        !report.at("schema_version").is_number_integer() ||
        report.at("schema_version") != 1 ||
        !report.at("errors").is_number_integer() || report.at("errors") < 0 ||
        !report.at("warnings").is_number_integer() || report.at("warnings") < 0)
      throw std::invalid_argument("invalid ERC report summary");
    for (const auto *field :
         {"checks", "pending", "waivers", "configurations", "diagnostics"})
      if (!report.at(field).is_array())
        throw std::invalid_argument("invalid ERC report list");
    std::size_t errors = 0, warnings = 0;
    for (const auto &d : report.at("diagnostics")) {
      if (!d.at("code").is_string() || !d.at("message").is_string() ||
          !d.at("span").is_object() || !d.at("related").is_array())
        throw std::invalid_argument("invalid ERC diagnostic");
      if (d.at("severity") == "error")
        ++errors;
      else if (d.at("severity") == "warning")
        ++warnings;
      else
        throw std::invalid_argument("invalid ERC diagnostic severity");
    }
    if (report.at("errors") != errors || report.at("warnings") != warnings)
      throw std::invalid_argument("ERC counts disagree with diagnostics");
    const auto &reports = response.at("reports");
    auto json = reports.at("erc.json").get<std::string>();
    auto text = reports.at("erc.txt").get<std::string>();
    if (parse_bridge_json(json) != report)
      throw std::invalid_argument("JSON report disagrees with ERC result");
    return {report, std::move(json), std::move(text)};
  } catch (const std::exception &e) {
    bridge_error("invalid ERC response: " + std::string(e.what()), config.span,
                 code);
  }
}
} // namespace noscheme
