#include "noscheme/evaluate.hpp"
#include "noscheme/units.hpp"
#include "noscheme/validation.hpp"
#include <algorithm>
#include <charconv>
#include <cmath>
#include <cstdlib>
#include <format>
#include <limits>

namespace noscheme {
namespace {
std::size_t bits(const Integer &value) {
  return value == 0
             ? 0
             : boost::multiprecision::msb(Integer(value < 0 ? -value : value)) +
                   1;
}
std::size_t characters(std::string_view text) {
  std::size_t count = 0;
  for (unsigned char c : text)
    count += (c & 0xc0) != 0x80;
  return count;
}
Integer decimal(std::string_view text) {
  bool negative = !text.empty() && text.front() == '-';
  if (negative || (!text.empty() && text.front() == '+'))
    text.remove_prefix(1);
  Integer result = 0;
  for (char c : text) {
    if (c < '0' || c > '9')
      throw std::invalid_argument("invalid decimal integer");
    result *= 10;
    result += c - '0';
  }
  return negative ? Integer(-result) : result;
}
Integer power10(unsigned count) {
  Integer result = 1, base = 10;
  while (count) {
    if (count & 1)
      result *= base;
    count >>= 1;
    if (count)
      base *= base;
  }
  return result;
}
Integer round_ratio(const Integer &n, const Integer &d) {
  Integer q = n / d, remainder = n % d;
  if (2 * remainder > d || (2 * remainder == d && bit_test(q, 0)))
    ++q;
  return q;
}
// Round the exact ratio once to IEEE binary64, including subnormal values.
double ratio_double(Integer n, Integer d) {
  bool negative = (n < 0) != (d < 0);
  if (n < 0)
    n = -n;
  if (d < 0)
    d = -d;
  if (!n)
    return negative ? -0.0 : 0.0;
  int exponent = static_cast<int>(bits(n)) - static_cast<int>(bits(d));
  if ((exponent >= 0 && n < (d << exponent)) ||
      (exponent < 0 && (n << -exponent) < d))
    --exponent;
  if (exponent > 1023)
    throw std::overflow_error("double overflow");
  const int quantum = std::max(exponent - 52, -1074);
  if (quantum < 0)
    n <<= -quantum;
  else
    d <<= quantum;
  Integer mantissa = round_ratio(n, d);
  auto result = std::ldexp(mantissa.convert_to<double>(), quantum);
  if (!std::isfinite(result))
    throw std::overflow_error("double overflow");
  return negative ? -result : result;
}
std::pair<Integer, Integer> ratio(const Value &value) {
  if (auto p = std::get_if<Integer>(&value))
    return {*p, 1};
  double number = std::get<double>(value);
  int exponent;
  auto mantissa = std::frexp(number, &exponent);
  Integer n(std::ldexp(mantissa, 53)), d = 1;
  exponent -= 53;
  if (exponent < 0)
    d <<= -exponent;
  else
    n <<= exponent;
  return {n, d};
}
std::string float_display(double value) {
  char buffer[64];
  auto converted = std::to_chars(buffer, buffer + sizeof(buffer), value,
                                 std::chars_format::scientific);
  std::string text(buffer, converted.ptr);
  auto e = text.find('e');
  int exponent = std::stoi(text.substr(e + 1));
  auto coefficient = text.substr(0, e);
  if (exponent < -4 || exponent >= 16)
    return text;
  std::string sign;
  if (coefficient.starts_with('-')) {
    sign = "-";
    coefficient.erase(0, 1);
  }
  if (auto dot = coefficient.find('.'); dot != std::string::npos)
    coefficient.erase(dot, 1);
  const int point = exponent + 1;
  if (point <= 0)
    return sign + "0." + std::string(-point, '0') + coefficient;
  if (point >= static_cast<int>(coefficient.size()))
    return sign + coefficient + std::string(point - coefficient.size(), '0');
  coefficient.insert(point, ".");
  return sign + coefficient;
}
std::string expression_path(const Json &expr) {
  if (expr["node"] == "Name")
    return expr["text"];
  if (expr["node"] == "Member") {
    auto base = expression_path(expr["base"]);
    if (!base.empty())
      return base + "." + expr["name"]["text"].get<std::string>();
  }
  return {};
}
} // namespace

[[noreturn]] void Evaluator::error(std::string code, std::string message,
                                   const Json &span,
                                   const Json &related) const {
  throw DiagnosticsFailure(Json::array({object(
      "code", std::move(code), "severity", "error", "message",
      std::move(message), "span", span, "related", related, "instance_path",
      instance_path, "configuration", nullptr, "application", nullptr)}));
}
Value Evaluator::number(Integer value, const Json &span) {
  if (bits(value) > limits.integer_bits)
    error("number.limit", "integer exceeds configured bit limit", span);
  return value;
}
Value Evaluator::number(double value, const Json &span) {
  if (!std::isfinite(value))
    error("number.nonfinite", "numeric result must be finite", span);
  return value;
}
double Evaluator::floating(const Value &value, const Json &span) {
  if (auto p = std::get_if<double>(&value))
    return *p;
  if (auto p = std::get_if<Integer>(&value))
    return ratio_double(*p, 1);
  error("expression.type", "expected a number", span);
}
std::string Evaluator::bounded(std::string text, const Json &span) {
  if (characters(text) > limits.string_length)
    error("format.limit", "string exceeds configured length limit", span);
  return text;
}
Integer Evaluator::integer(const Value &value, const Json &span,
                           bool explicit_conversion) {
  if (auto p = std::get_if<Integer>(&value))
    return *p;
  if (auto p = std::get_if<double>(&value)) {
    if (!explicit_conversion)
      warnings.push_back(object(
          "code", "number.implicit-integer", "severity", "warning", "message",
          "double converted to integer by truncation toward zero; use num.int "
          "to make this explicit",
          "span", span, "related", Json::array(), "instance_path",
          instance_path, "configuration", nullptr, "application", nullptr));
    return Integer(*p);
  }
  error("expression.type", "expected a number", span);
}
std::size_t Evaluator::dimension(const Value &value, const Json &span) {
  auto n = integer(value, span);
  if (n < 1 || n > limits.dimension)
    error("array.dimension",
          "dimension must be between 1 and " + std::to_string(limits.dimension),
          span);
  return n.convert_to<std::size_t>();
}
std::size_t Evaluator::index(const Value &value, const Json &span,
                             std::size_t size, bool stop) {
  auto n = integer(value, span);
  if (n < 0 || (stop ? n > size : n >= size))
    error("array.index", "index is outside the array bounds", span);
  return n.convert_to<std::size_t>();
}
std::string Evaluator::display(const Value &value, const Json &) {
  if (auto p = std::get_if<std::string>(&value))
    return *p;
  if (auto p = std::get_if<Integer>(&value))
    return p->str();
  return float_display(std::get<double>(value));
}
Value Evaluator::evaluate(const Json &expr, const Environment &env,
                          std::size_t depth) {
  const auto &span = expr["span"];
  if (depth > limits.expression_depth)
    error("expression.limit", "expression exceeds configured depth limit",
          span);
  auto run = [&](const Json &child) { return evaluate(child, env, depth + 1); };
  auto numeric = [&](Value value, const Json &where) {
    if (std::holds_alternative<std::string>(value))
      error("expression.type", "expected a number", where);
    return value;
  };
  try {
    const auto kind = expr["node"].get<std::string>();
    if (kind == "Number") {
      auto text = expr["spelling"].get<std::string>();
      const auto prefix = text.find_first_of(numeric_prefixes);
      if (prefix != std::string::npos) {
        int exponent = prefix_exponent(text[prefix]);
        auto dot = text.find('.');
        auto fraction = dot == std::string::npos
                            ? text.substr(prefix + 1)
                            : text.substr(dot + 1, prefix - dot - 1);
        auto digits = text.substr(0, std::min(dot, prefix)) + fraction;
        exponent -= static_cast<int>(fraction.size());
        while (exponent < 0 && !digits.empty() && digits.back() == '0') {
          digits.pop_back();
          ++exponent;
        }
        if (digits.empty())
          return number(Integer(0), span);
        if (exponent >= 0) {
          const auto first = digits.find_first_not_of('0');
          if (first != std::string::npos &&
              digits.size() - first > limits.integer_bits)
            error("number.limit", "integer literal exceeds configured limit",
                  span);
          return number(Integer(decimal(digits) * power10(exponent)), span);
        }
        auto literal = digits + "e" + std::to_string(exponent);
        return number(std::strtod(literal.c_str(), nullptr), span);
      }
      if (expr["tag"] == "integer") {
        auto first = text.find_first_not_of('0');
        if (first != std::string::npos &&
            text.size() - first > limits.integer_bits)
          error("number.limit", "integer literal exceeds configured limit",
                span);
        return number(decimal(text), span);
      }
      return number(std::strtod(text.c_str(), nullptr), span);
    }
    if (kind == "String")
      return bounded(expr["value"], span);
    if (kind == "Name") {
      auto name = expr["text"].get<std::string>();
      if (!env.contains(name))
        error("parameter.missing", "no value for parameter '" + name + "'",
              span);
      return env.at(name);
    }
    if (kind == "Group")
      return run(expr["expression"]);
    if (kind == "Unary") {
      auto value = numeric(run(expr["operand"]), span);
      if (auto p = std::get_if<Integer>(&value))
        return number(expr["operator"] == "-" ? Integer(-*p) : *p, span);
      auto d = std::get<double>(value);
      return number(expr["operator"] == "-" ? -d : d, span);
    }
    if (kind == "Binary") {
      auto left = numeric(run(expr["left"]), expr["left"]["span"]);
      auto right = numeric(run(expr["right"]), expr["right"]["span"]);
      auto op = expr["operator"].get<std::string>();
      auto a = std::get_if<Integer>(&left), b = std::get_if<Integer>(&right);
      if (op == "<<" || op == ">>") {
        if (!a || !b || *b < 0)
          error(
              "number.shift",
              "shifts require integer-tagged operands and a nonnegative count",
              span);
        if (*b > limits.integer_bits)
          error("number.limit", "shift count exceeds configured limit", span);
        auto shift = b->convert_to<unsigned>();
        if (op == "<<")
          return number(Integer(*a << shift), span);
        if (*a >= 0)
          return number(Integer(*a >> shift), span);
        return number(Integer(-(((-*a) + (Integer(1) << shift) - 1) >> shift)),
                      span);
      }
      if (op == "/") {
        if (a && b) {
          if (*b == 0)
            error("number.divide-by-zero", "division by zero", span);
          return number(ratio_double(*a, *b), span);
        }
        auto x = floating(left, span), y = floating(right, span);
        if (y == 0)
          error("number.divide-by-zero", "division by zero", span);
        return number(x / y, span);
      }
      if (a && b) {
        if (op == "+")
          return number(Integer(*a + *b), span);
        if (op == "-")
          return number(Integer(*a - *b), span);
        return number(Integer(*a * *b), span);
      }
      auto x = floating(left, span), y = floating(right, span);
      return number(op == "+" ? x + y : op == "-" ? x - y : x * y, span);
    }
    if (kind == "Call" && expr["callee"]["node"] == "Member") {
      const auto &member = expr["callee"];
      std::vector<Value> args;
      Environment kwargs;
      for (const auto &arg : expr["arguments"])
        args.push_back(run(arg));
      for (const auto &arg : expr["keywords"])
        kwargs[arg["name"]["text"]] = run(arg["value"]);
      const auto helper = member["name"]["text"].get<std::string>();
      if (helper == "format") {
        auto base = run(member["base"]);
        if (!std::holds_alternative<std::string>(base))
          error("expression.type", ".format requires a string",
                member["base"]["span"]);
        return format(std::get<std::string>(base), args, kwargs, span);
      }
      auto space = expression_path(member["base"]);
      if (space.empty())
        error("expression.call", "arbitrary method calls are not allowed",
              span);
      auto symbol = symbols.find(space);
      if (env.contains(space.substr(0, space.find('.'))) ||
          symbol == symbols.end() || symbol->second.module != "noscheme" ||
          symbol->second.name != "num")
        error("expression.call", "helper namespace must be explicitly imported",
              member["span"]);
      if (args.size() != 1 || !kwargs.empty())
        error("expression.arguments",
              "numeric helpers take one positional argument", span);
      auto value = numeric(args[0], span);
      if (helper != "int" && helper != "ceil" && helper != "floor" &&
          helper != "round")
        error("expression.call", "unknown numeric helper", member["span"]);
      if (auto p = std::get_if<Integer>(&value))
        return number(*p, span);
      double d = std::get<double>(value);
      return number(Integer(helper == "ceil"    ? std::ceil(d)
                            : helper == "floor" ? std::floor(d)
                            : helper == "round" ? std::round(d)
                                                : std::trunc(d)),
                    span);
    }
    error("expression.unsupported", "unsupported expression", span);
  } catch (const std::overflow_error &) {
    error("number.overflow", "number cannot be represented as a finite double",
          span);
  }
}

std::string Evaluator::format(const std::string &text,
                              const std::vector<Value> &args,
                              const Environment &kwargs, const Json &span) {
  std::string result;
  std::size_t length = 0;
  auto append = [&](const std::string &part) {
    length += characters(part);
    if (length > limits.string_length)
      error("format.limit", "formatted string exceeds configured length limit",
            span);
    result += part;
  };
  auto digit = [](char c) { return c >= '0' && c <= '9'; };
  auto letter = [](char c) {
    return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || c == '_' ||
           c == '#';
  };
  for (std::size_t i = 0; i < text.size();) {
    if (text[i] != '{' && text[i] != '}') {
      auto end = text.find_first_of("{}", i);
      if (end == std::string::npos)
        end = text.size();
      append(text.substr(i, end - i));
      i = end;
      continue;
    }
    if (i + 1 < text.size() && text[i] == text[i + 1]) {
      append(text.substr(i, 1));
      i += 2;
      continue;
    }
    if (text[i] == '}')
      error("format.invalid", "single closing brace in format string", span);
    auto start = ++i;
    int nesting = 1;
    for (; i < text.size(); ++i) {
      if (text[i] == '{')
        ++nesting;
      if (text[i] == '}' && --nesting == 0)
        break;
    }
    if (i == text.size())
      error("format.invalid", "unclosed format field", span);
    auto field = text.substr(start, i++ - start);
    auto colon = field.find(':'), bang = field.find('!');
    if (bang > colon)
      bang = std::string::npos;
    if (bang != std::string::npos &&
        (bang + 1 == field.size() ||
         (bang + 2 < field.size() && field[bang + 2] != ':')))
      error("format.invalid", "invalid conversion syntax", span);
    auto name = field.substr(0, std::min(colon, bang));
    if (name.find('{') != std::string::npos ||
        (name.find('[') != std::string::npos &&
         name.find(']') == std::string::npos))
      error("format.invalid", "invalid field syntax", span);
    bool numeric_name =
        !name.empty() && std::all_of(name.begin(), name.end(), digit);
    bool identifier_name = !name.empty() && letter(name[0]) &&
                           std::all_of(name.begin(), name.end(), [&](char c) {
                             return letter(c) || digit(c);
                           });
    if (bang != std::string::npos || (!identifier_name && !numeric_name))
      error("format.field",
            "use an explicit numeric index or name; traversal and conversions "
            "are forbidden",
            span);
    const Value *value;
    if (numeric_name) {
      if (name.size() > 9 || std::stoul(name) >= args.size())
        error("format.argument",
              "missing positional format argument '" + name + "'", span);
      value = &args[std::stoul(name)];
    } else {
      auto found = kwargs.find(name);
      if (found == kwargs.end())
        error("format.argument", "missing named format argument '" + name + "'",
              span);
      value = &found->second;
    }
    auto spec = colon == std::string::npos ? "" : field.substr(colon + 1);
    if (spec.empty()) {
      append(display(*value, span));
      continue;
    }
    std::size_t pos = 0;
    while (pos < spec.size() && digit(spec[pos]))
      ++pos;
    auto width_text = pos ? spec.substr(0, pos) : "0";
    bool has_precision = pos < spec.size() && spec[pos] == '.';
    std::string precision_text = "0";
    if (has_precision) {
      auto begin = ++pos;
      while (pos < spec.size() && digit(spec[pos]))
        ++pos;
      precision_text = spec.substr(begin, pos - begin);
    }
    if (pos + 1 != spec.size() || precision_text.empty() ||
        std::string_view("dfeg").find(spec[pos]) == std::string_view::npos)
      error("format.spec",
            "supported numeric formats are [width][.precision]d/f/e/g", span);
    char format_type = spec[pos];
    if (width_text.size() > 6 || precision_text.size() > 6)
      error("format.limit", "format width or precision exceeds limits", span);
    auto width = std::stoul(width_text), precision = std::stoul(precision_text);
    if (width > limits.format_width || precision > limits.format_precision)
      error("format.limit", "format width or precision exceeds limits", span);
    if (std::holds_alternative<std::string>(*value))
      error("expression.type", "expected a number", span);
    std::string rendered;
    if (format_type == 'd') {
      if (!std::holds_alternative<Integer>(*value) || has_precision)
        error("format.type",
              "d format requires an integer tag and no precision", span);
      rendered = std::get<Integer>(*value).str();
      if (rendered.size() < width) {
        auto zero = width_text.starts_with('0');
        rendered.insert(zero && rendered.starts_with('-') ? 1 : 0,
                        width - rendered.size(), zero ? '0' : ' ');
      }
    } else {
      try {
        double number = floating(*value, span);
        // Python accepts redundant leading zeros in width and precision;
        // std::format requires their canonical spelling.
        std::string normalized;
        if (width) {
          if (width_text.starts_with('0'))
            normalized += '0';
          normalized += std::to_string(width);
        }
        if (has_precision)
          normalized += "." + std::to_string(precision);
        normalized += format_type;
        rendered = std::vformat("{:" + normalized + "}",
                                std::make_format_args(number));
      } catch (const std::overflow_error &) {
        error("format.invalid",
              "invalid formatting: integer too large for double", span);
      } catch (const std::format_error &e) {
        error("format.invalid", e.what(), span);
      }
    }
    append(rendered);
  }
  return result;
}

Json encoded(const Value &value) {
  if (auto p = std::get_if<Integer>(&value))
    return object("type", "number", "tag", "integer", "value", p->str());
  if (auto p = std::get_if<double>(&value))
    return object("type", "number", "tag", "double", "value", *p);
  return object("type", "string", "value", std::get<std::string>(value));
}
Value decoded(const Json &value) {
  if (value.at("type") == "string")
    return value.at("value").get<std::string>();
  if (value.at("tag") == "integer")
    return decimal(value.at("value").get<std::string>());
  double result = value.at("value");
  if (!std::isfinite(result))
    throw std::invalid_argument("nonfinite input value");
  return result;
}
std::string format_quantity(const Value &value, const std::string &quantity) {
  auto [n, d] = ratio(value);
  const auto &unit = quantity_units.at(quantity);
  if (!n)
    return "0" + unit;
  bool negative = n < 0;
  if (negative)
    n = -n;
  int exponent =
      static_cast<int>(n.str().size()) - static_cast<int>(d.str().size());
  if ((exponent >= 0 && n < d * power10(exponent)) ||
      (exponent < 0 && n * power10(-exponent) < d))
    --exponent;
  int engineering =
      std::clamp(static_cast<int>(std::floor(exponent / 3.0)) * 3, -18, 15);
  int scale = 5 - exponent;
  Integer rounded = scale >= 0 ? round_ratio(Integer(n * power10(scale)), d)
                               : round_ratio(n, Integer(d * power10(-scale)));
  // A six-digit rounding carry promotes an engineering prefix when possible.
  if (rounded >= 1000000) {
    rounded /= 10;
    --scale;
    ++exponent;
  }
  if (exponent >= engineering + 3 && engineering < 15)
    engineering += 3;
  auto digits = rounded.str();
  int places = scale + engineering;
  if (places <= 0)
    digits += std::string(-places, '0');
  else {
    if (digits.size() <= static_cast<std::size_t>(places))
      digits.insert(0, places + 1 - digits.size(), '0');
    digits.insert(digits.size() - places, ".");
    while (digits.back() == '0')
      digits.pop_back();
    if (digits.back() == '.')
      digits.pop_back();
  }
  auto prefix = numeric_prefixes[(engineering + 18) / 3];
  return (negative ? "-" : "") + digits +
         (prefix == 'R' ? "" : std::string(1, prefix)) + unit;
}
} // namespace noscheme
