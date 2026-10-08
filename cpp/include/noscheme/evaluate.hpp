#pragma once
#include "noscheme/project.hpp"
#include <boost/multiprecision/cpp_int.hpp>
#include <variant>

namespace noscheme {
using Integer = boost::multiprecision::cpp_int;
using Value = std::variant<Integer, double, std::string>;
using Environment = std::map<std::string, Value>;
struct Limits {
  std::size_t integer_bits = 65536, string_length = 65536, format_width = 4096,
              format_precision = 100, expression_depth = 128,
              dimension = 100000;
};
class Evaluator {
  const Symbols &symbols;
  Value number(Integer value, const Json &span);
  Value number(double value, const Json &span);
  double floating(const Value &value, const Json &span);
  std::string bounded(std::string text, const Json &span);
  std::string format(const std::string &text, const std::vector<Value> &args,
                     const Environment &kwargs, const Json &span);

public:
  Limits limits;
  Json warnings = Json::array();
  Json instance_path = nullptr;
  explicit Evaluator(const Symbols &symbols, Limits limits = {})
      : symbols(symbols), limits(limits) {}
  [[noreturn]] void error(std::string code, std::string message,
                          const Json &span,
                          const Json &related = Json::array()) const;
  Value evaluate(const Json &expression, const Environment &environment = {},
                 std::size_t depth = 0);
  Integer integer(const Value &value, const Json &span,
                  bool explicit_conversion = false);
  std::size_t dimension(const Value &value, const Json &span);
  std::size_t index(const Value &value, const Json &span, std::size_t size,
                    bool stop = false);
  std::string display(const Value &value, const Json &span);
};
Json encoded(const Value &value);
Value decoded(const Json &value);
std::string format_quantity(const Value &value, const std::string &quantity);
} // namespace noscheme
