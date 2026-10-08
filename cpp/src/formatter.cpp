#include "noscheme/formatter.hpp"
#include "noscheme/lsp_completion.hpp"
#include <algorithm>
#include <cctype>
#include <map>
#include <memory>
#include <optional>
#include <regex>
#include <variant>
namespace noscheme {
namespace {
struct Seq;
using Part = std::variant<std::string, std::shared_ptr<Seq>>;
using Parts = std::vector<Part>;
struct Item {
  Parts parts;
  std::size_t start, end;
};
struct Seq {
  std::string open, close;
  std::vector<Item> items;
  std::size_t start, end;
  bool comma, magic;
};
struct Comment {
  std::size_t start, end;
  std::string text;
};
struct Line {
  std::size_t depth;
  std::string text;
  std::size_t start, end;
  bool opens = false, closes = false, definition = false, own_comment = false;
  std::optional<Comment> comment{};
};
std::size_t start(const Json &n) { return n.at("span").at("start_pos"); }
std::size_t end(const Json &n) { return n.at("span").at("end_pos"); }
Parts join(Parts a, const Parts &b) {
  a.insert(a.end(), b.begin(), b.end());
  return a;
}
std::string flat(const Parts &parts) {
  std::string result;
  for (const auto &p : parts) {
    if (auto text = std::get_if<std::string>(&p))
      result += *text;
    else {
      const auto &seq = *std::get<std::shared_ptr<Seq>>(p);
      result += seq.open;
      for (std::size_t i = 0; i < seq.items.size(); ++i) {
        if (i)
          result += ", ";
        result += flat(seq.items[i].parts);
      }
      result += seq.close;
    }
  }
  return result;
}
std::string path(const Json &names) {
  std::string result;
  for (const auto &n : names) {
    if (!result.empty())
      result += '.';
    result += n.at("text").get<std::string>();
  }
  return result;
}
Json shape(Json value);
class Formatter {
  const Source &source;
  Json ast;
  std::string masked;
  std::vector<Comment> comments;
  std::vector<Line> lines;
  std::size_t trim(std::size_t pos) const {
    pos = std::min(pos, masked.size());
    while (pos && std::isspace(static_cast<unsigned char>(masked[pos - 1])))
      --pos;
    return pos;
  }
  std::size_t column(std::size_t pos) const {
    auto line = pos ? source.text.rfind('\n', pos - 1) : std::string::npos;
    return pos - (line == std::string::npos ? 0 : line + 1);
  }
  bool has_comment(std::size_t begin, std::size_t finish) const {
    return std::any_of(comments.begin(), comments.end(), [&](const auto &c) {
      return begin < c.start && c.start < finish;
    });
  }
  Part seq(std::string open, std::string close, std::size_t begin,
           std::size_t finish, std::vector<Item> items, bool comma = true) {
    auto last = items.empty() ? begin + 1 : items.back().end;
    bool magic =
        comma && !items.empty() &&
        masked.substr(last, finish - 1 - last).find(',') != std::string::npos;
    return std::make_shared<Seq>(Seq{std::move(open), std::move(close),
                                     std::move(items), begin, finish, comma,
                                     magic});
  }
  Parts expr(const Json &n) {
    auto kind = n.at("node").get<std::string>();
    if (kind == "Name")
      return {n.at("text").get<std::string>()};
    if (kind == "Number" || kind == "String")
      return {n.at("spelling").get<std::string>()};
    if (kind == "Group")
      return {seq("(", ")", start(n), end(n),
                  {{expr(n.at("expression")), start(n.at("expression")),
                    end(n.at("expression"))}},
                  false)};
    if (kind == "ListExpr") {
      std::vector<Item> items;
      for (const auto &v : n.at("items"))
        items.push_back({expr(v), start(v), end(v)});
      return {seq("[", "]", start(n), end(n), std::move(items))};
    }
    if (kind == "Unary")
      return join({n.at("operator").get<std::string>()}, expr(n.at("operand")));
    if (kind == "Binary")
      return join(join(expr(n.at("left")),
                       {" " + n.at("operator").get<std::string>() + " "}),
                  expr(n.at("right")));
    if (kind == "Member")
      return join(expr(n.at("base")),
                  {"." + n.at("name").at("text").get<std::string>()});
    if (kind == "Index") {
      auto result = join({std::string("[")}, expr(n.at("start")));
      if (!n.at("stop").is_null())
        result = join(join(result, {std::string(":")}), expr(n.at("stop")));
      return join(result, {std::string("]")});
    }
    if (kind == "Subscript")
      return join(expr(n.at("base")), expr(n.at("selection")));
    if (kind == "Call") {
      std::vector<Item> items;
      for (const auto &v : n.at("arguments"))
        items.push_back({expr(v), start(v), end(v)});
      for (const auto &v : n.at("keywords"))
        items.push_back(
            {join({v.at("name").at("text").get<std::string>() + "="},
                  expr(v.at("value"))),
             start(v), end(v)});
      auto opening = masked.find('(', end(n.at("callee")));
      return join(expr(n.at("callee")),
                  {seq("(", ")", opening, end(n), std::move(items))});
    }
    throw std::logic_error("unhandled formatting expression: " + kind);
  }
  std::string key(const Json &n) {
    if (!n.at("items").empty()) {
      std::string result = "[";
      for (const auto &v : n.at("items")) {
        if (result != "[")
          result += ", ";
        result += key(v);
      }
      if (n.at("items").size() == 1 &&
          masked.substr(start(n), end(n) - start(n)).find(',') !=
              std::string::npos)
        result += ',';
      return result + "]";
    }
    auto result = n.at("first").at("spelling").get<std::string>();
    if (!n.at("last").is_null())
      result += ".." + n.at("last").at("spelling").get<std::string>();
    return result;
  }
  Parts endpoint(const Json &n) {
    Parts result;
    for (const auto &index : n.at("instance_indices"))
      result = join(result, expr(index));
    if (!result.empty())
      result.push_back(std::string("."));
    result.push_back(key(n.at("head")));
    for (const auto &s : n.at("selectors"))
      result = join(result, s.at("node") == "Index"
                                ? expr(s)
                                : Parts{"." + s.at("text").get<std::string>()});
    return result;
  }
  bool broken(const Part &p) const {
    if (std::holds_alternative<std::string>(p))
      return false;
    const auto &s = *std::get<std::shared_ptr<Seq>>(p);
    if (s.magic || has_comment(s.start, s.end - 1))
      return true;
    for (const auto &i : s.items)
      for (const auto &v : i.parts)
        if (broken(v))
          return true;
    return false;
  }
  void layout(const Parts &parts, std::size_t depth, std::size_t begin,
              std::size_t finish, const std::string &suffix = "") {
    auto text = flat(parts) + suffix;
    std::optional<std::size_t> forced, breakable;
    for (std::size_t i = 0; i < parts.size(); ++i) {
      if (!forced && broken(parts[i]))
        forced = i;
      if (auto s = std::get_if<std::shared_ptr<Seq>>(&parts[i]);
          s && !(*s)->items.empty())
        breakable = i;
    }
    auto width = std::count_if(text.begin(), text.end(), [](unsigned char c) {
      return (c & 0xc0) != 0x80;
    });
    if (!forced && (2 * depth + width <= 100 || !breakable)) {
      lines.push_back({depth, text, begin, finish});
      return;
    }
    auto at = forced ? *forced : *breakable;
    const auto &s = *std::get<std::shared_ptr<Seq>>(parts[at]);
    lines.push_back({depth,
                     flat(Parts(parts.begin(), parts.begin() + at)) + s.open,
                     begin, s.start + 1, true});
    for (const auto &i : s.items)
      layout(i.parts, depth + 1, i.start, i.end, s.comma ? "," : "");
    auto close = lines.size();
    layout(join({s.close}, Parts(parts.begin() + at + 1, parts.end())), depth,
           s.end - 1, finish, suffix);
    lines[close].closes = true;
  }
  void statement(const Json &n, std::size_t depth, Parts parts,
                 const Json &body = Json::array(), bool definition = false) {
    auto first = lines.size();
    layout(parts, depth, start(n),
           trim(body.empty() ? end(n) : start(body.front())));
    lines[first].definition = definition;
    lines.back().opens = !body.empty();
    for (const auto &child : body)
      item(child, depth + 1);
  }
  void item(const Json &n, std::size_t depth) {
    auto kind = n.at("node").get<std::string>();
    if (kind == "Import") {
      auto text = "import " + path(n.at("module"));
      if (!n.at("names").empty()) {
        text = "from " + path(n.at("module")) + " import ";
        bool first = true;
        for (const auto &v : n.at("names")) {
          if (!first)
            text += ", ";
          first = false;
          text += v.at("name").at("text").get<std::string>();
          if (!v.at("alias").is_null())
            text += " as " + v.at("alias").at("text").get<std::string>();
        }
      }
      statement(n, depth, {text});
    } else if (kind == "Definition") {
      auto text = n.at("kind").get<std::string>() + " " +
                  n.at("name").at("text").get<std::string>();
      if (!n.at("base").is_null())
        text += " extends " + path(n.at("base"));
      statement(n, depth, {text + ":"}, n.at("body"), true);
    } else if (kind == "Section")
      statement(n, depth, {n.at("name").get<std::string>() + ":"},
                n.at("entries"));
    else if (kind == "Net")
      statement(n, depth,
                join({std::string(n.at("global_").get<bool>() ? "global net "
                                                              : "net ") +
                      n.at("name").at("text").get<std::string>() + ": "},
                     expr(n.at("type"))),
                n.at("fields"));
    else if (kind == "Field") {
      Parts parts{key(n.at("key")) + ":"};
      if (!n.at("value").is_null())
        parts = join(join(parts, {std::string(" ")}), expr(n.at("value")));
      if (!n.at("default").is_null())
        parts = join(join(parts, {std::string(" = ")}), expr(n.at("default")));
      statement(n, depth, parts, n.at("fields"));
    } else if (kind == "Binding")
      statement(n, depth,
                join(join(endpoint(n.at("endpoint")), {std::string(": ")}),
                     expr(n.at("target"))));
    else if (kind == "Argument")
      statement(n, depth,
                join({n.at("name").at("text").get<std::string>() + ": "},
                     expr(n.at("value"))));
    else if (kind == "ElementOverride") {
      Parts parts;
      for (const auto &i : n.at("indices"))
        parts = join(parts, expr(i));
      statement(n, depth, join(parts, {std::string(":")}), n.at("sections"));
    } else if (kind == "Instance") {
      Parts parts{"inst " + n.at("name").at("text").get<std::string>()};
      for (const auto &i : n.at("dimensions"))
        parts = join(parts, expr(i));
      parts.push_back(": " + path(n.at("component")));
      auto opening = end(n.at("component").back());
      while (opening < masked.size() &&
             (masked[opening] == ' ' || masked[opening] == '\t'))
        ++opening;
      auto closing = opening;
      bool inline_list = opening < masked.size() && masked[opening] == '(';
      if (inline_list) {
        // The AST preserves source spans; locate the argument delimiter in the
        // string-masked buffer so literals cannot close the list early.
        auto code = code_mask(source.text);
        int depth = 1;
        for (closing = opening + 1; closing < code.size() && depth; ++closing) {
          if (code[closing] == '(')
            ++depth;
          if (code[closing] == ')')
            --depth;
        }
      }
      std::vector<Item> args;
      for (const auto &v : n.at("positional"))
        args.push_back({expr(v), start(v), end(v)});
      auto body = Json::array();
      for (const auto &section : n.at("sections")) {
        auto kept = section;
        kept["entries"] = Json::array();
        for (const auto &v : section.at("entries")) {
          if (inline_list && start(v) < closing) {
            auto parts =
                v.at("node") == "Binding"
                    ? join(
                          join(endpoint(v.at("endpoint")), {std::string(": ")}),
                          expr(v.at("target")))
                    : join({v.at("name").at("text").get<std::string>() + "="},
                           expr(v.at("value")));
            args.push_back({parts, start(v), end(v)});
          } else
            kept["entries"].push_back(v);
        }
        if (!kept["entries"].empty())
          body.push_back(kept);
      }
      std::sort(args.begin(), args.end(),
                [](const auto &a, const auto &b) { return a.start < b.start; });
      if (!args.empty())
        parts.push_back(seq("(", ")", opening, closing, std::move(args)));
      statement(n, depth, parts, body);
    } else
      throw std::logic_error("unhandled formatting statement: " + kind);
  }

public:
  explicit Formatter(const Source &source)
      : source(source), ast(parse(source)),
        masked(code_mask(source.text, true)) {
    for (std::size_t i = 0; i < source.text.size();) {
      if (source.text[i] == '"' && masked[i] == '"') {
        for (++i; i < source.text.size(); ++i) {
          if (source.text[i] == '\\')
            ++i;
          else if (source.text[i] == '"') {
            ++i;
            break;
          }
        }
      } else if (source.text.compare(i, 2, "//") == 0) {
        auto finish = source.text.find('\n', i);
        if (finish == std::string::npos)
          finish = source.text.size();
        comments.push_back({i, finish, source.text.substr(i, finish - i)});
        i = finish;
      } else if (source.text.compare(i, 2, "/*") == 0) {
        auto finish = source.text.find("*/", i + 2) + 2;
        comments.push_back({i, finish, source.text.substr(i, finish - i)});
        i = finish;
      } else
        ++i;
    }
  }
  bool equivalent(const Formatter &other) const {
    if (shape(ast) != shape(other.ast) ||
        comments.size() != other.comments.size())
      return false;
    for (std::size_t i = 0; i < comments.size(); ++i)
      if (comments[i].text != other.comments[i].text)
        return false;
    return true;
  }
  std::string render() {
    for (const auto &n : ast.at("items"))
      item(n, 0);
    std::map<std::size_t, std::vector<Comment>> leading;
    for (const auto &c : comments) {
      auto finish = source.text.find('\n', c.end);
      if (finish == std::string::npos)
        finish = source.text.size();
      auto nonspace = [](const std::string &s) {
        return s.find_first_not_of(" \t\r\n") != std::string::npos;
      };
      if (nonspace(source.text.substr(c.end, finish - c.end)))
        throw Failure("format.comment",
                      "a comment must end its line; move the code after it",
                      c.start, c.end);
      auto line_start = c.start - column(c.start);
      auto it = std::upper_bound(
          lines.begin(), lines.end(), c.start,
          [](auto pos, const auto &line) { return pos < line.start; });
      auto index = static_cast<std::size_t>(it - lines.begin());
      if (!nonspace(source.text.substr(line_start, c.start - line_start))) {
        if (index && lines[index - 1].end > c.start)
          throw Failure("format.comment",
                        "cannot place a comment inside this expression",
                        c.start, c.end);
        leading[index].push_back(c);
      } else {
        auto code = trim(c.start) - 1;
        auto it = std::upper_bound(
            lines.begin(), lines.end(), code,
            [](auto pos, const auto &line) { return pos < line.start; });
        if (it == lines.begin())
          throw Failure("format.comment", "cannot place trailing comment",
                        c.start, c.end);
        auto &line = *std::prev(it);
        if (line.end > c.start || line.comment)
          throw Failure("format.comment",
                        "cannot place a comment inside this expression",
                        c.start, c.end);
        line.comment = c;
      }
    }
    std::vector<Line> elements;
    std::vector<std::pair<std::size_t, std::size_t>> ancestors;
    for (std::size_t i = 0; i <= lines.size(); ++i) {
      for (const auto &c : leading[i]) {
        auto depth = i < lines.size() ? lines[i].depth : 0;
        if (i == lines.size() || column(c.start) > column(lines[i].start))
          for (auto it = ancestors.rbegin(); it != ancestors.rend(); ++it)
            if (it->first <= column(c.start)) {
              depth = it->second;
              break;
            }
        elements.push_back(
            {depth, c.text, c.start, c.end, false, false, false, true});
      }
      if (i < lines.size()) {
        while (!ancestors.empty() && ancestors.back().second >= lines[i].depth)
          ancestors.pop_back();
        ancestors.emplace_back(column(lines[i].start), lines[i].depth);
        elements.push_back(lines[i]);
      }
    }
    std::vector<bool> blank(elements.size());
    static const std::regex blank_line("\\n[ \\t\\r]*\\n");
    for (std::size_t i = 1; i < elements.size(); ++i) {
      const auto &prev = elements[i - 1], &line = elements[i];
      auto finish = prev.comment ? prev.comment->end : prev.end;
      blank[i] =
          !prev.opens && !line.closes &&
          std::regex_search(source.text.substr(finish, line.start - finish),
                            blank_line);
    }
    for (std::size_t i = 0; i < elements.size(); ++i)
      if (elements[i].definition) {
        auto j = i;
        while (j && elements[j - 1].own_comment && elements[j - 1].depth == 0)
          --j;
        blank[j] = blank[j] || j > 0;
      }
    std::string result;
    for (std::size_t i = 0; i < elements.size(); ++i) {
      const auto &line = elements[i];
      if (blank[i])
        result += '\n';
      result += std::string(line.depth * 2, ' ') + line.text +
                (line.comment ? "  " + line.comment->text : "") + "\n";
    }
    return result;
  }
};
Json shape(Json value) {
  if (value.is_array())
    for (auto &v : value)
      v = shape(v);
  else if (value.is_object()) {
    value.erase("span");
    for (auto &[k, v] : value.items())
      v = shape(v);
  }
  return value;
}
} // namespace
std::string format_source(const Source &source) {
  Formatter formatter(source);
  auto result = formatter.render();
  if (result == source.text)
    return result;
  // The normal native parser independently verifies the rewritten meaning.
  Source formatted(result, source.filename);
  if (!formatter.equivalent(Formatter(formatted)))
    throw Failure(
        "format.internal",
        "formatting would change this file's meaning; please report it", 0, 0);
  return result;
}
} // namespace noscheme
