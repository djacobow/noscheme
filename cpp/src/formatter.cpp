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
using ast::as;
using ast::Kind;
std::size_t start(const ast::Node &n) { return n.span.start; }
std::size_t end(const ast::Node &n) { return n.span.end; }
using Nodes = std::vector<const ast::Node *>;
template <class T> Nodes nodes(const std::vector<T> &items) {
  Nodes result;
  for (const auto &item : items) {
    if constexpr (std::is_same_v<T, ast::Ptr>)
      result.push_back(item.get());
    else
      result.push_back(&item);
  }
  return result;
}
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
Json shape(Json value);
class Formatter {
  std::shared_ptr<const Source> shared_source;
  const Source &source;
  std::shared_ptr<const ast::Module> tree;
  std::string masked;
  std::vector<Comment> comments;
  std::vector<Line> lines;
  std::map<const ast::Net *, std::vector<std::string>> net_names;
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
  Item item_of(const ast::Node &n) { return {expr(n), start(n), end(n)}; }
  Parts expr(const ast::Node &n) {
    switch (n.kind) {
    case Kind::Name:
      return {as<ast::Name>(n).text};
    case Kind::Number:
      return {as<ast::Number>(n).spelling};
    case Kind::String:
      return {as<ast::String>(n).spelling};
    case Kind::Group:
      return {seq("(", ")", start(n), end(n),
                  {item_of(*as<ast::Group>(n).expression)}, false)};
    case Kind::ListExpr: {
      std::vector<Item> items;
      for (const auto &v : as<ast::ListExpr>(n).items)
        items.push_back(item_of(*v));
      return {seq("[", "]", start(n), end(n), std::move(items))};
    }
    case Kind::Unary: {
      const auto &unary = as<ast::Unary>(n);
      return join({unary.op}, expr(*unary.operand));
    }
    case Kind::Binary: {
      const auto &binary = as<ast::Binary>(n);
      return join(join(expr(*binary.left), {" " + binary.op + " "}),
                  expr(*binary.right));
    }
    case Kind::Member: {
      const auto &member = as<ast::Member>(n);
      return join(expr(*member.base), {"." + member.name.text});
    }
    case Kind::Index: {
      const auto &index = as<ast::Index>(n);
      auto result = join({std::string("[")}, expr(*index.start));
      if (index.stop)
        result = join(join(result, {std::string(":")}), expr(*index.stop));
      return join(result, {std::string("]")});
    }
    case Kind::Subscript: {
      const auto &subscript = as<ast::Subscript>(n);
      return join(expr(*subscript.base), expr(subscript.selection));
    }
    case Kind::Range: {
      const auto &range = as<ast::Range>(n);
      return join(join(expr(*range.low), {std::string("..")}),
                  expr(*range.high));
    }
    case Kind::Tolerance: {
      // Keep the author's operator spelling, ± or +/-.
      const auto &tolerance = as<ast::Tolerance>(n);
      auto from = end(*tolerance.center), to = start(*tolerance.deviation);
      auto written = masked.substr(from, to - from);
      auto op = written.find("+/-") != std::string::npos ? "+/-" : "\u00b1";
      auto result =
          join(join(expr(*tolerance.center), {" " + std::string(op) + " "}),
               expr(*tolerance.deviation));
      if (tolerance.percent)
        result.push_back(std::string("%"));
      return result;
    }
    case Kind::Call: {
      const auto &call = as<ast::Call>(n);
      std::vector<Item> items;
      for (const auto &v : call.arguments)
        items.push_back(item_of(*v));
      for (const auto &v : call.keywords)
        items.push_back(
            {join({v.name.text + "="}, expr(*v.value)), start(v), end(v)});
      auto opening = masked.find('(', end(*call.callee));
      return join(expr(*call.callee),
                  {seq("(", ")", opening, end(n), std::move(items))});
    }
    default:
      throw std::logic_error("unhandled formatting expression: " +
                             std::string(ast::kind_name(n.kind)));
    }
  }
  std::string key(const ast::FieldKey &n) {
    if (!n.items.empty()) {
      std::string result = "[";
      for (const auto &v : n.items) {
        if (result != "[")
          result += ", ";
        result += key(v);
      }
      if (n.items.size() == 1 &&
          masked.substr(start(n), end(n) - start(n)).find(',') !=
              std::string::npos)
        result += ',';
      return result + "]";
    }
    auto result = n.first.spelling;
    if (n.last)
      result += ".." + n.last->spelling;
    if (!n.aliases.empty()) {
      result += " (";
      for (std::size_t i = 0; i < n.aliases.size(); ++i)
        result += (i ? ", " : "") + n.aliases[i].spelling;
      result += ")";
    }
    return result;
  }
  Parts endpoint(const ast::Endpoint &n) {
    Parts result;
    for (const auto &index : n.instance_indices)
      result = join(result, expr(index));
    if (!result.empty())
      result.push_back(std::string("."));
    result.push_back(key(n.head));
    for (const auto &s : n.selectors)
      result = join(result, s->kind == Kind::Index
                                ? expr(*s)
                                : Parts{"." + as<ast::Name>(*s).text});
    return result;
  }
  Parts binding(const ast::Binding &n) {
    return join(join(endpoint(n.endpoint), {std::string(": ")}),
                expr(*n.target));
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
  void statement(const ast::Node &n, std::size_t depth, Parts parts,
                 const Nodes &body = {}, bool definition = false) {
    auto first = lines.size();
    layout(parts, depth, start(n),
           trim(body.empty() ? end(n) : start(*body.front())));
    lines[first].definition = definition;
    lines.back().opens = !body.empty();
    for (const auto *child : body)
      item(*child, depth + 1);
  }
  void item(const ast::Node &n, std::size_t depth) {
    switch (n.kind) {
    case Kind::Import: {
      const auto &import = as<ast::Import>(n);
      auto text = "import " + ast::dotted(import.module);
      if (!import.names.empty()) {
        text = "from " + ast::dotted(import.module) + " import ";
        bool first = true;
        for (const auto &v : import.names) {
          if (!first)
            text += ", ";
          first = false;
          text += v.name.text;
          if (v.alias)
            text += " as " + v.alias->text;
        }
      }
      statement(n, depth, {text});
      return;
    }
    case Kind::Definition: {
      const auto &definition = as<ast::Definition>(n);
      auto text = definition.keyword + " " + definition.name.text;
      if (!definition.base.empty())
        text += " extends " + ast::dotted(definition.base);
      // `net a, b: T` parses to one Net per name, sharing the declaration's
      // span; print the declaration once.
      Nodes body;
      const ast::Net *group = nullptr;
      for (const auto &child : definition.body) {
        auto *net = as<ast::Net>(child);
        if (net && group && net->span == group->span) {
          net_names[group].push_back(net->name.text);
          continue;
        }
        group = net;
        if (net)
          net_names[net] = {net->name.text};
        body.push_back(child.get());
      }
      statement(n, depth, {text + ":"}, body, true);
      return;
    }
    case Kind::Section: {
      const auto &section = as<ast::Section>(n);
      statement(n, depth, {section.name + ":"}, nodes(section.entries));
      return;
    }
    case Kind::Net: {
      const auto &net = as<ast::Net>(n);
      std::string names;
      for (const auto &name : net_names.at(&net))
        names += (names.empty() ? "" : ", ") + name;
      statement(n, depth,
                join({std::string(net.global ? "global net " : "net ") + names +
                      ": "},
                     expr(*net.type)),
                nodes(net.fields));
      return;
    }
    case Kind::Field: {
      const auto &field = as<ast::Field>(n);
      Parts parts{key(field.key) + ":"};
      if (field.value)
        parts = join(join(parts, {std::string(" ")}), expr(*field.value));
      if (field.default_value)
        parts =
            join(join(parts, {std::string(" = ")}), expr(*field.default_value));
      statement(n, depth, parts, nodes(field.fields));
      return;
    }
    case Kind::Binding:
      statement(n, depth, binding(as<ast::Binding>(n)));
      return;
    case Kind::Argument: {
      const auto &argument = as<ast::Argument>(n);
      statement(n, depth,
                join({argument.name.text + ": "}, expr(*argument.value)));
      return;
    }
    case Kind::ElementOverride: {
      const auto &element = as<ast::ElementOverride>(n);
      Parts parts;
      for (const auto &i : element.indices)
        parts = join(parts, expr(i));
      statement(n, depth, join(parts, {std::string(":")}),
                nodes(element.sections));
      return;
    }
    case Kind::Instance:
      break;
    default:
      throw std::logic_error("unhandled formatting statement: " +
                             std::string(ast::kind_name(n.kind)));
    }
    {
      const auto &instance = as<ast::Instance>(n);
      Parts parts{"inst " + instance.name.text};
      for (const auto &i : instance.dimensions)
        parts = join(parts, expr(i));
      parts.push_back(": " + ast::dotted(instance.component));
      auto opening = end(instance.component.back());
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
      for (const auto &v : instance.positional)
        args.push_back(item_of(*v));
      if (instance.wildcard)
        args.push_back({{std::string("*")},
                        instance.wildcard->start,
                        instance.wildcard->end});
      // Sections without the entries written inline.
      std::vector<ast::Section> kept;
      for (const auto &section : instance.sections) {
        auto rest = section;
        rest.entries.clear();
        for (const auto &v : section.entries) {
          if (inline_list && start(*v) < closing) {
            auto *argument = as<ast::Argument>(v);
            auto parts = argument ? join({argument->name.text + "="},
                                         expr(*argument->value))
                                  : binding(as<ast::Binding>(*v));
            args.push_back({parts, start(*v), end(*v)});
          } else
            rest.entries.push_back(v);
        }
        if (!rest.entries.empty())
          kept.push_back(std::move(rest));
      }
      std::sort(args.begin(), args.end(),
                [](const auto &a, const auto &b) { return a.start < b.start; });
      if (!args.empty())
        parts.push_back(seq("(", ")", opening, closing, std::move(args)));
      // Connections lead the body without a section header.
      Nodes body;
      for (const auto &section : kept)
        if (section.name == "connections")
          for (const auto &entry : section.entries)
            body.push_back(entry.get());
      for (const auto &section : kept)
        if (section.name != "connections")
          body.push_back(&section);
      statement(n, depth, parts, body);
    }
  }

public:
  explicit Formatter(std::shared_ptr<const Source> shared)
      : shared_source(std::move(shared)), source(*shared_source),
        tree(parse(shared_source)), masked(code_mask(source.text, true)) {
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
    if (shape(ast::to_json(*tree)) != shape(ast::to_json(*other.tree)) ||
        comments.size() != other.comments.size())
      return false;
    for (std::size_t i = 0; i < comments.size(); ++i)
      if (comments[i].text != other.comments[i].text)
        return false;
    return true;
  }
  std::string render() {
    for (const auto &n : tree->items)
      item(*n, 0);
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
    // An instance's `*` is located by a bare span.
    if (auto wildcard = value.find("wildcard"); wildcard != value.end())
      *wildcard = !wildcard->is_null();
    for (auto &[k, v] : value.items())
      v = shape(v);
  }
  return value;
}
} // namespace
std::string format_source(const Source &source) {
  Formatter formatter(std::make_shared<const Source>(source));
  auto result = formatter.render();
  if (result == source.text)
    return result;
  // The normal native parser independently verifies the rewritten meaning.
  if (!formatter.equivalent(
          Formatter(std::make_shared<const Source>(result, source.filename))))
    throw Failure(
        "format.internal",
        "formatting would change this file's meaning; please report it", 0, 0);
  return result;
}
} // namespace noscheme
