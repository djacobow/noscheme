#pragma once
#include "noscheme/json.hpp"
#include <stdexcept>
#include <string>
#include <vector>
namespace noscheme {
struct Failure : std::runtime_error {
  std::string code;
  std::size_t start, end;
  Failure(std::string code, std::string message, std::size_t start,
          std::size_t end)
      : runtime_error(std::move(message)), code(std::move(code)), start(start),
        end(end) {}
};
class Source {
public:
  std::string text, filename;
  std::vector<std::size_t> lines{0}, characters, offsets;
  Source(std::string text, std::string filename);
  Json span(std::size_t start, std::size_t end) const;
  Json diagnostic(const Failure &error) const;
};
// Optional benchmark timings in seconds. Normal callers need not collect them.
struct ParseTimings {
  double comments = 0, tokens = 0, syntax = 0, ast = 0, validation = 0;
};
Json parse(const Source &source, ParseTimings *timings = nullptr);
} // namespace noscheme
