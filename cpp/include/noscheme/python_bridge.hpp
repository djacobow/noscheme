#pragma once
#include "noscheme/cancellation.hpp"
#include "noscheme/project.hpp"
#include <chrono>

namespace noscheme {
// Shared Linux subprocess transport: bounded JSON, no shell, deadline, and
// process-group cleanup. One child per request prevents stale Python modules.
Json parse_bridge_json(const std::string &text);
Json python_request(const Json &request, const fs::path &python,
                    const Json &span, std::chrono::milliseconds timeout,
                    const std::string &code, std::stop_token stop = {});
void validate_bridge_response(const Json &response, const Json &span,
                              const std::string &code);
[[noreturn]] void bridge_error(const std::string &message, const Json &span,
                               const std::string &code);
} // namespace noscheme
