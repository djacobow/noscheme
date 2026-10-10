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
std::size_t characters(std::string_view text) {
  std::size_t count = 0;
  for (unsigned char c : text)
    count += (c & 0xc0) != 0x80;
  return count;
}
// Parse a signed literal together so -9223372036854775808 is representable.
Value literal(const ast::Number &number, bool negative = false) {
  auto text = number.spelling;
  const auto prefix = text.find_first_of(numeric_prefixes);
  if (prefix != std::string::npos) {
    int exponent = prefix_exponent(text[prefix]);
    auto dot = text.find('.');
    auto fraction = dot == std::string::npos
                        ? text.substr(prefix + 1)
                        : text.substr(dot + 1, prefix - dot - 1);
    auto digits = text.substr(0, std::min(dot, prefix)) + fraction;
    exponent -= static_cast<int>(fraction.size());
    auto first = digits.find_first_not_of('0');
    if (first == std::string::npos)
      return Integer(0);
    digits.erase(0, first);
    while (exponent < 0 && digits.back() == '0') {
      digits.pop_back();
      ++exponent;
    }
    if (exponent >= 0) {
      if (digits.size() + exponent > 19)
        throw IntegerOverflow();
      digits.append(exponent, '0');
      return parse_integer((negative ? "-" : "") + digits);
    }
    text = digits + "e" + std::to_string(exponent);
  } else if (number.integer) {
    return parse_integer((negative ? "-" : "") + text);
  }
  double result = std::strtod(text.c_str(), nullptr);
  return negative ? -result : result;
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
} // namespace

[[noreturn]] void Evaluator::error(std::string code, std::string message,
                                   const Json &span,
                                   const Json &related) const {
  throw DiagnosticsFailure(Json::array({object(
      "code", std::move(code), "severity", "error", "message",
      std::move(message), "span", span, "related", related, "instance_path",
      instance_path, "configuration", nullptr, "application", nullptr)}));
}
Value Evaluator::number(Integer value, const Span &span) {
  if (!integer_fits(value, limits.integer_bits))
    error("number.limit", "integer exceeds configured bit limit", span);
  return value;
}
Value Evaluator::number(double value, const Span &span) {
  if (!std::isfinite(value))
    error("number.nonfinite", "numeric result must be finite", span);
  return value;
}
double Evaluator::floating(const Value &value, const Span &span) {
  if (auto p = std::get_if<double>(&value))
    return *p;
  if (auto p = std::get_if<Integer>(&value))
    return static_cast<double>(*p);
  error("expression.type", "expected a number", span);
}
std::string Evaluator::bounded(std::string text, const Span &span) {
  if (characters(text) > limits.string_length)
    error("format.limit", "string exceeds configured length limit", span);
  return text;
}
Integer Evaluator::integer(const Value &value, const Span &span,
                           bool explicit_conversion) {
  if (auto p = std::get_if<Integer>(&value)) {
    number(*p, span);
    return *p;
  }
  if (auto p = std::get_if<double>(&value)) {
    if (!explicit_conversion)
      warnings.push_back(object(
          "code", "number.implicit-integer", "severity", "warning", "message",
          "double converted to integer by truncation toward zero; use num.int "
          "to make this explicit",
          "span", span.json(), "related", Json::array(), "instance_path",
          instance_path, "configuration", nullptr, "application", nullptr));
    try {
      auto converted = integer_from_double(*p);
      number(converted, span);
      return converted;
    } catch (const IntegerOverflow &) {
      error("number.limit", "integer exceeds configured bit limit", span);
    }
  }
  error("expression.type", "expected a number", span);
}
std::size_t Evaluator::dimension(const Value &value, const Span &span) {
  auto n = integer(value, span);
  if (n < 1 || static_cast<std::uint64_t>(n) > limits.dimension)
    error("array.dimension",
          "dimension must be between 1 and " + std::to_string(limits.dimension),
          span);
  return static_cast<std::size_t>(n);
}
std::size_t Evaluator::index(const Value &value, const Span &span,
                             std::size_t size, bool stop) {
  auto n = integer(value, span);
  if (n < 0 || (stop ? static_cast<std::uint64_t>(n) > size
                     : static_cast<std::uint64_t>(n) >= size))
    error("array.index", "index is outside the array bounds", span);
  return static_cast<std::size_t>(n);
}
std::string Evaluator::display(const Value &value, const Span &) {
  if (auto p = std::get_if<std::string>(&value))
    return *p;
  if (auto p = std::get_if<Integer>(&value))
    return std::to_string(*p);
  return float_display(std::get<double>(value));
}
Value Evaluator::evaluate(const ast::Node &expr, const Environment &env,
                          std::size_t depth) {
  using ast::as;
  using ast::Kind;
  const auto &span = expr.span;
  if (depth > limits.expression_depth)
    error("expression.limit", "expression exceeds configured depth limit",
          span);
  auto run = [&](const ast::Node &child) {
    return evaluate(child, env, depth + 1);
  };
  auto numeric = [&](Value value, const Span &where) {
    if (std::holds_alternative<std::string>(value))
      error("expression.type", "expected a number", where);
    return value;
  };
  try {
    switch (expr.kind) {
    case Kind::Number: {
      auto value = literal(as<ast::Number>(expr));
      if (auto p = std::get_if<Integer>(&value))
        return number(*p, span);
      return number(std::get<double>(value), span);
    }
    case Kind::String:
      return bounded(as<ast::String>(expr).value, span);
    case Kind::Name: {
      const auto &name = as<ast::Name>(expr).text;
      auto found = env.find(name);
      if (found == env.end())
        error("parameter.missing", "no value for parameter '" + name + "'",
              span);
      return found->second;
    }
    case Kind::Group:
      return run(*as<ast::Group>(expr).expression);
    case Kind::Unary: {
      const auto &n = as<ast::Unary>(expr);
      if (auto direct = as<ast::Number>(n.operand); direct && n.op == "-") {
        auto value = literal(*direct, true);
        if (auto p = std::get_if<Integer>(&value))
          return number(*p, span);
        return number(std::get<double>(value), span);
      }
      auto value = numeric(run(*n.operand), span);
      if (auto p = std::get_if<Integer>(&value))
        return number(n.op == "-" ? integer_subtract(0, *p) : *p, span);
      auto d = std::get<double>(value);
      return number(n.op == "-" ? -d : d, span);
    }
    case Kind::Binary: {
      const auto &n = as<ast::Binary>(expr);
      auto left = numeric(run(*n.left), n.left->span);
      auto right = numeric(run(*n.right), n.right->span);
      const auto &op = n.op;
      auto a = std::get_if<Integer>(&left), b = std::get_if<Integer>(&right);
      if (op == "<<" || op == ">>") {
        if (!a || !b || *b < 0)
          error(
              "number.shift",
              "shifts require integer-tagged operands and a nonnegative count",
              span);
        if (*b >= static_cast<Integer>(
                      std::min(limits.integer_bits, std::size_t(64))))
          error("number.limit", "shift count exceeds configured limit", span);
        auto shift = static_cast<unsigned>(*b);
        if (op == "<<") {
          auto result = *a;
          for (unsigned i = 0; i < shift; ++i)
            result = integer_multiply(result, 2);
          return number(result, span);
        }
        return number(Integer(*a >> shift), span);
      }
      if (op == "/") {
        auto x = floating(left, span), y = floating(right, span);
        if (y == 0)
          error("number.divide-by-zero", "division by zero", span);
        return number(x / y, span);
      }
      if (a && b) {
        if (op == "+")
          return number(integer_add(*a, *b), span);
        if (op == "-")
          return number(integer_subtract(*a, *b), span);
        return number(integer_multiply(*a, *b), span);
      }
      auto x = floating(left, span), y = floating(right, span);
      return number(op == "+" ? x + y : op == "-" ? x - y : x * y, span);
    }
    case Kind::Call: {
      const auto &call = as<ast::Call>(expr);
      const auto *member = as<ast::Member>(call.callee);
      if (!member)
        break;
      std::vector<Value> args;
      Environment kwargs;
      for (const auto &arg : call.arguments)
        args.push_back(run(*arg));
      for (const auto &arg : call.keywords)
        kwargs[arg.name.text] = run(*arg.value);
      const auto &helper = member->name.text;
      if (helper == "format") {
        auto base = run(*member->base);
        if (!std::holds_alternative<std::string>(base))
          error("expression.type", ".format requires a string",
                member->base->span);
        return format(std::get<std::string>(base), args, kwargs, span);
      }
      auto space = ast::expression_path(*member->base);
      if (space.empty())
        error("expression.call", "arbitrary method calls are not allowed",
              span);
      auto symbol = symbols.find(space);
      if (env.contains(space.substr(0, space.find('.'))) ||
          symbol == symbols.end() || symbol->second.module != "noscheme" ||
          symbol->second.name != "num")
        error("expression.call", "helper namespace must be explicitly imported",
              member->span);
      if (args.size() != 1 || !kwargs.empty())
        error("expression.arguments",
              "numeric helpers take one positional argument", span);
      auto value = numeric(args[0], span);
      if (helper != "int" && helper != "ceil" && helper != "floor" &&
          helper != "round")
        error("expression.call", "unknown numeric helper", member->span);
      if (auto p = std::get_if<Integer>(&value))
        return number(*p, span);
      double d = std::get<double>(value);
      return number(integer_from_double(helper == "ceil"    ? std::ceil(d)
                                        : helper == "floor" ? std::floor(d)
                                        : helper == "round" ? std::round(d)
                                                            : std::trunc(d)),
                    span);
    }
    default:
      break;
    }
    error("expression.unsupported", "unsupported expression", span);
  } catch (const IntegerOverflow &) {
    error("number.limit", "integer exceeds configured bit limit", span);
  } catch (const std::overflow_error &) {
    error("number.overflow", "number cannot be represented as a finite double",
          span);
  }
}

std::string Evaluator::format(const std::string &text,
                              const std::vector<Value> &args,
                              const Environment &kwargs, const Span &span) {
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
      rendered = std::to_string(std::get<Integer>(*value));
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
    return object("type", "number", "tag", "integer", "value",
                  std::to_string(*p));
  if (auto p = std::get_if<double>(&value))
    return object("type", "number", "tag", "double", "value", *p);
  return object("type", "string", "value", std::get<std::string>(value));
}
MetaValue to_meta(const Value &value) {
  return std::visit([](const auto &v) { return MetaValue(v); }, value);
}
std::optional<Value> to_value(const MetaValue &value) {
  if (auto p = value.get_if<Integer>())
    return *p;
  if (auto p = value.get_if<double>())
    return *p;
  if (auto p = value.get_if<std::string>())
    return *p;
  return {};
}
Value decoded(const Json &value) {
  if (value.at("type") == "string")
    return value.at("value").get<std::string>();
  if (value.at("tag") == "integer")
    return parse_integer(value.at("value").get<std::string>());
  double result = value.at("value");
  if (!std::isfinite(result))
    throw std::invalid_argument("nonfinite input value");
  return result;
}
std::string format_quantity(const Value &value, const std::string &quantity) {
  const auto &unit = quantity_units.at(quantity);
  double number = std::holds_alternative<Integer>(value)
                      ? static_cast<double>(std::get<Integer>(value))
                      : std::get<double>(value);
  if (number == 0)
    return "0" + unit;
  bool negative = number < 0;
  char buffer[64];
  auto converted =
      std::to_chars(buffer, buffer + sizeof(buffer), std::abs(number),
                    std::chars_format::scientific, 5);
  std::string scientific(buffer, converted.ptr);
  auto e = scientific.find('e');
  int exponent = std::stoi(scientific.substr(e + 1));
  int engineering =
      std::clamp(static_cast<int>(std::floor(exponent / 3.0)) * 3, -18, 15);
  auto digits = scientific.substr(0, e);
  digits.erase(digits.find('.'), 1);
  int places = 5 - exponent + engineering;
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
