#pragma once
#include "noscheme/ast.hpp"
#include "noscheme/json.hpp"
#include <memory>
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
// The module keeps its source alive for its spans.
std::shared_ptr<const ast::Module> parse(std::shared_ptr<const Source> source,
                                         ParseTimings *timings = nullptr);
// Parses bytes [begin, end) alone, with spans in the whole source.
std::shared_ptr<const ast::Module>
parse_fragment(std::shared_ptr<const Source> source, std::size_t begin,
               std::size_t end);
} // namespace noscheme
