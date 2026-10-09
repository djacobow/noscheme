#include "noscheme/source.hpp"
#include "noscheme/units.hpp"
#include "noscheme/validation.hpp"

// Load JSON before ANTLR, whose headers undefine the C stdio EOF macro.
#include "NoSchemeLexer.h"
#include "NoSchemeParser.h"
#include <algorithm>
#include <chrono>
#include <set>
namespace noscheme {
namespace {
using P = NoSchemeParser;
using Ctx = antlr4::ParserRuleContext;
using Tree = antlr4::tree::ParseTree;
using Terminal = antlr4::tree::TerminalNode;
std::size_t byte(const Source &s, std::size_t character) {
  return s.offsets[std::min(character, s.offsets.size() - 1)];
}
class Errors : public antlr4::BaseErrorListener {
  const Source &s;

public:
  explicit Errors(const Source &s) : s(s) {}
  void syntaxError(antlr4::Recognizer *, antlr4::Token *t, std::size_t line,
                   std::size_t column, const std::string &message,
                   std::exception_ptr) override {
    std::size_t pos =
        t ? byte(s, t->getStartIndex())
          : s.lines[std::min(line - 1, s.lines.size() - 1)] + column;
    throw Failure("syntax.invalid", message, pos, pos + 1);
  }
};
// Comments become one space per character, keeping line breaks, so ANTLR
// character positions still match the original source.
std::string mask(const Source &s) {
  antlr4::ANTLRInputStream input(s.text);
  NoSchemeLexer lexer(&input);
  lexer.removeErrorListeners();
  std::vector<std::pair<std::size_t, std::size_t>> comments;
  for (auto token = lexer.nextToken(); token->getType() != antlr4::Token::EOF;
       token = lexer.nextToken()) {
    auto type = token->getType();
    auto b = byte(s, token->getStartIndex()),
         e = byte(s, token->getStopIndex() + 1);
    if (type == P::BLOCK_COMMENT || type == P::LINE_COMMENT)
      comments.emplace_back(b, e);
    else if (type == P::MUL && token->getText() == "/" &&
             s.text.compare(b, 2, "/*") == 0)
      throw Failure("syntax.invalid", "unterminated block comment", b, b + 1);
  }
  std::string masked;
  masked.reserve(s.text.size() + 1);
  std::size_t copied = 0;
  for (auto [b, e] : comments) {
    masked.append(s.text, copied, b - copied);
    for (auto i = b; i < e; ++i) {
      auto c = s.text[i];
      if (c == '\r' || c == '\n')
        masked += c;
      else if ((static_cast<unsigned char>(c) & 0xc0) != 0x80)
        masked += ' ';
    }
    copied = e;
  }
  masked.append(s.text, copied);
  // A masked byte's character index locates it in the original source.
  auto original = [&](std::size_t j) {
    std::size_t character = 0;
    for (std::size_t i = 0; i < j; ++i)
      character += (static_cast<unsigned char>(masked[i]) & 0xc0) != 0x80;
    return byte(s, character);
  };
  for (std::size_t i = 0; i < masked.size();) {
    auto e = masked.find('\n', i);
    if (e == std::string::npos)
      e = masked.size();
    auto first = masked.find_first_not_of(" \t\r", i);
    if (first < e)
      if (auto tab =
              std::find(masked.begin() + i, masked.begin() + first, '\t');
          tab != masked.begin() + first) {
        auto at = original(tab - masked.begin());
        throw Failure("syntax.indentation",
                      "tabs are not allowed in indentation", at, at + 1);
      }
    i = e + 1;
  }
  if (masked.empty() || masked.back() != '\n')
    masked += '\n';
  return masked;
}
// ANTLR performs lexical recognition. This adapter only supplies off-side
// layout tokens.
std::vector<std::unique_ptr<antlr4::Token>> layout(NoSchemeLexer &lexer,
                                                   const Source &source) {
  std::vector<std::unique_ptr<antlr4::Token>> result;
  std::vector<std::size_t> indents{0};
  int depth = 0;
  auto synthetic = [&](antlr4::Token *origin, std::size_t kind) {
    auto t = std::make_unique<antlr4::CommonToken>(origin);
    t->setType(kind);
    result.push_back(std::move(t));
  };
  while (true) {
    auto t = lexer.nextToken();
    auto kind = t->getType();
    auto raw = t.get();
    if (kind == antlr4::Token::EOF) {
      while (indents.size() > 1) {
        indents.pop_back();
        synthetic(raw, P::DEDENT);
      }
      result.push_back(std::move(t));
      break;
    }
    if (kind == P::LPAREN || kind == P::LBRACK)
      ++depth;
    if (kind == P::RPAREN || kind == P::RBRACK)
      --depth;
    if (kind != P::NL) {
      result.push_back(std::move(t));
      continue;
    }
    if (depth > 0)
      continue;
    auto text = t->getText();
    auto indent = text.size() - text.rfind('\n') - 1;
    result.push_back(std::move(t));
    if (indent > indents.back()) {
      indents.push_back(indent);
      synthetic(raw, P::INDENT);
    }
    while (indent < indents.back()) {
      indents.pop_back();
      synthetic(raw, P::DEDENT);
    }
    if (indent != indents.back()) {
      auto at = byte(source, raw->getStopIndex() + 1);
      throw Failure("syntax.indentation", "inconsistent indentation", at,
                    at + 1);
    }
  }
  return result;
}
// Builds typed nodes from the ANTLR tree. Positions come from the parsed
// text; spans refer to the owner, base bytes later, so a declaration parsed
// alone still reports positions in its file.
class Builder {
  const Source &s;
  const Source *owner;
  std::size_t base;
  std::size_t begin(Tree *t) {
    auto *c = dynamic_cast<Ctx *>(t);
    return byte(s,
                c ? c->getStart()->getStartIndex()
                  : dynamic_cast<Terminal *>(t)->getSymbol()->getStartIndex());
  }
  std::size_t end(Tree *t) {
    auto *c = dynamic_cast<Ctx *>(t);
    auto *token = c ? c->getStop() : dynamic_cast<Terminal *>(t)->getSymbol();
    return token->getType() == antlr4::Token::EOF
               ? s.text.size()
               : byte(s, token->getStopIndex() + 1);
  }
  Span span(std::size_t b, std::size_t e) const {
    return {owner, base + b, base + e};
  }
  Span span(Tree *t) { return span(begin(t), end(t)); }
  static Span spanning(const ast::Node &left, const ast::Node &right) {
    return {left.span.source, left.span.start, right.span.end};
  }
  template <class T> T at(Tree *t) {
    T node;
    node.span = span(t);
    return node;
  }
  template <class T> std::shared_ptr<T> shared(Span where) {
    auto node = std::make_shared<T>();
    node->span = where;
    return node;
  }
  std::string string(Tree *t) {
    try {
      return Json::parse(t->getText()).get<std::string>();
    } catch (const Json::exception &) {
      throw Failure("syntax.string",
                    "invalid string escape; use JSON-style string escapes",
                    begin(t), end(t));
    }
  }
  std::string tag(std::string text) {
    auto k = text.find_first_of(numeric_prefixes), dot = text.find('.');
    if (dot != std::string::npos && k + 1 < text.size())
      return "invalid";
    int exponent = prefix_exponent(text[k]);
    std::string digits = text.substr(0, k);
    int fraction = 0;
    if (dot != std::string::npos) {
      fraction = static_cast<int>(k - dot - 1);
      digits.erase(dot, 1);
    } else if (k + 1 < text.size()) {
      fraction = static_cast<int>(text.size() - k - 1);
      digits += text.substr(k + 1);
    }
    int scale = exponent - fraction;
    while (scale < 0 && !digits.empty() && digits.back() == '0') {
      digits.pop_back();
      ++scale;
    }
    return scale >= 0 || digits.empty() ? "integer" : "double";
  }
  ast::Name name(Tree *t) {
    auto result = at<ast::Name>(t);
    result.text = t->getText();
    return result;
  }
  std::vector<ast::Name> path(P::PathContext *x) {
    std::vector<ast::Name> result;
    for (auto *n : x->name())
      result.push_back(name(n));
    return result;
  }
  ast::Ptr member(ast::Ptr base, ast::Name field) {
    auto result = shared<ast::Member>(spanning(*base, field));
    result->base = std::move(base);
    result->name = std::move(field);
    return result;
  }
  ast::Ptr subscript(ast::Ptr base, ast::Index selection) {
    auto result = shared<ast::Subscript>(spanning(*base, selection));
    result->base = std::move(base);
    result->selection = std::move(selection);
    return result;
  }
  ast::Ptr suffix(ast::Ptr result, P::SuffixContext *x) {
    if (x->name())
      return member(std::move(result), name(x->name()));
    if (x->index())
      return subscript(std::move(result), index(x->index()));
    auto call = shared<ast::Call>(
        {result->span.source, result->span.start, span(x).end});
    std::set<std::string> seen;
    for (auto *arg : x->callArgument()) {
      if (arg->EQUAL()) {
        auto keyword = at<ast::KeywordArgument>(arg);
        keyword.name = name(arg->name());
        if (!seen.insert(keyword.name.text).second)
          throw Failure("syntax.duplicate",
                        "duplicate named argument '" + keyword.name.text + "'",
                        begin(arg->name()), end(arg->name()));
        keyword.value = expr(arg->expr());
        call->keywords.push_back(std::move(keyword));
      } else {
        if (!call->keywords.empty())
          throw Failure("syntax.argument-order",
                        "positional arguments must precede named arguments",
                        begin(arg), end(arg));
        call->arguments.push_back(expr(arg->expr()));
      }
    }
    call->callee = std::move(result);
    return call;
  }
  ast::Index index(P::IndexContext *x) {
    auto result = at<ast::Index>(x);
    result.start = expr(x->expr(0));
    if (x->COLON())
      result.stop = expr(x->expr(1));
    return result;
  }
  template <class T>
  std::vector<ast::Index> indices(const std::vector<T *> &xs) {
    std::vector<ast::Index> result;
    for (auto *x : xs)
      result.push_back(index(x));
    return result;
  }
  ast::Ptr reference(P::ReferenceContext *x) {
    auto first = name(x->name(0));
    ast::Ptr result = std::make_shared<ast::Name>(std::move(first));
    for (std::size_t i = 1; i < x->children.size(); ++i) {
      auto *child = x->children[i];
      if (auto *n = dynamic_cast<P::NameContext *>(child))
        result = member(std::move(result), name(n));
      else if (auto *selection = dynamic_cast<P::IndexContext *>(child))
        result = subscript(std::move(result), index(selection));
    }
    return result;
  }
  ast::Ptr atom(P::AtomContext *x) {
    if (x->name())
      return std::make_shared<ast::Name>(name(x->name()));
    if (x->STRING()) {
      auto result = shared<ast::String>(span(x));
      result->value = string(x);
      result->spelling = x->getText();
      return result;
    }
    if (x->LPAREN()) {
      auto result = shared<ast::Group>(span(x));
      result->expression = expr(x->expr(0));
      return result;
    }
    if (x->LBRACK()) {
      auto result = shared<ast::ListExpr>(span(x));
      for (auto *item : x->expr())
        result->items.push_back(expr(item));
      return result;
    }
    auto type = x->SCALED() ? tag(x->getText())
                : x->INT()  ? "integer"
                            : "double";
    if (type == "invalid")
      throw Failure(
          "syntax.number",
          "a scaled number has one decimal mark: 4.7k or 4k7, not 4.7k7",
          begin(x), end(x));
    auto result = shared<ast::Number>(span(x));
    result->spelling = x->getText();
    result->integer = type == "integer";
    return result;
  }
  ast::Ptr expr(Tree *tree) {
    auto *ctx = dynamic_cast<Ctx *>(tree);
    switch (ctx->getRuleIndex()) {
    case P::RuleExpr:
      return expr(ctx->children[0]);
    case P::RuleShift:
    case P::RuleSum:
    case P::RuleProduct: {
      auto result = expr(ctx->children[0]);
      for (std::size_t i = 1; i < ctx->children.size(); i += 2) {
        auto right = expr(ctx->children[i + 1]);
        auto binary = shared<ast::Binary>(spanning(*result, *right));
        binary->op = ctx->children[i]->getText();
        binary->left = std::move(result);
        binary->right = std::move(right);
        result = std::move(binary);
      }
      return result;
    }
    case P::RuleUnary: {
      auto *x = static_cast<P::UnaryContext *>(ctx);
      if (x->postfix())
        return expr(x->postfix());
      auto result = shared<ast::Unary>(span(x));
      result->op = x->ADD()->getText();
      result->operand = expr(x->unary());
      return result;
    }
    case P::RulePostfix: {
      auto *x = static_cast<P::PostfixContext *>(ctx);
      auto result = atom(x->atom());
      for (auto *selection : x->suffix())
        result = suffix(std::move(result), selection);
      return result;
    }
    case P::RuleReference:
      return reference(static_cast<P::ReferenceContext *>(ctx));
    default:
      throw std::logic_error("unhandled generated expression rule");
    }
  }
  ast::PinKey pin_key(P::PinKeyContext *x) {
    auto result = at<ast::PinKey>(x);
    result.spelling = x->getText();
    result.text =
        result.spelling.starts_with('"') ? string(x) : result.spelling;
    return result;
  }
  ast::FieldKey key_item(P::KeyItemContext *x) {
    auto result = at<ast::FieldKey>(x);
    result.first = pin_key(x->pinKey(0));
    if (x->RANGE())
      result.last = pin_key(x->pinKey(1));
    return result;
  }
  ast::FieldKey field_key(P::FieldKeyContext *x) {
    if (!x->LBRACK())
      return key_item(x->keyItem(0));
    auto result = at<ast::FieldKey>(x);
    for (auto *item : x->keyItem())
      result.items.push_back(key_item(item));
    result.first = result.items.front().first;
    return result;
  }
  ast::FieldKey aliased_key(P::AliasedKeyContext *x) {
    auto result = at<ast::FieldKey>(x);
    auto keys = x->pinKey();
    result.first = pin_key(keys.front());
    for (std::size_t i = 1; i < keys.size(); ++i)
      result.aliases.push_back(pin_key(keys[i]));
    return result;
  }
  ast::Ptr field_value(P::FieldValueContext *x) {
    if (x->RANGE()) {
      auto result = shared<ast::Range>(span(x));
      result->low = expr(x->expr(0));
      result->high = expr(x->expr(1));
      return result;
    }
    if (x->TOLERANCE()) {
      auto result = shared<ast::Tolerance>(span(x));
      result->center = expr(x->expr(0));
      result->deviation = expr(x->expr(1));
      result->percent = x->PERCENT() != nullptr;
      auto where = result->span;
      auto binary = [&](std::string op, ast::Ptr left, ast::Ptr right) {
        auto node = shared<ast::Binary>(where);
        node->op = std::move(op);
        node->left = std::move(left);
        node->right = std::move(right);
        return node;
      };
      auto bound = [&](const char *op) -> ast::Ptr {
        if (!result->percent)
          return binary(op, result->center, result->deviation);
        auto hundred = shared<ast::Number>(where);
        hundred->spelling = "100";
        hundred->integer = true;
        auto group = shared<ast::Group>(where);
        group->expression = binary(op, hundred, result->deviation);
        return binary("/", binary("*", result->center, group), hundred);
      };
      result->low = bound("-");
      result->high = bound("+");
      return result;
    }
    return expr(x->expr(0));
  }
  ast::Field field(P::FieldContext *x) {
    auto result = at<ast::Field>(x);
    result.key =
        x->fieldKey() ? field_key(x->fieldKey()) : aliased_key(x->aliasedKey());
    if (x->fieldValue())
      result.value = field_value(x->fieldValue());
    if (x->expr())
      result.default_value = expr(x->expr());
    result.fields = fields(x->field());
    return result;
  }
  std::vector<ast::Field> fields(const std::vector<P::FieldContext *> &xs) {
    std::vector<ast::Field> result;
    for (auto *x : xs)
      result.push_back(field(x));
    return result;
  }
  template <class T>
  ast::Section section(Tree *t, std::string section_name,
                       const std::vector<T *> &entries) {
    auto result = at<ast::Section>(t);
    result.name = std::move(section_name);
    for (auto *entry : entries)
      result.entries.push_back(entry_node(entry));
    return result;
  }
  ast::Ptr entry_node(P::FieldContext *x) {
    return std::make_shared<ast::Field>(field(x));
  }
  ast::FieldKey binding_key(P::BindingKeyContext *x) {
    if (!x->LBRACK()) {
      auto result = at<ast::FieldKey>(x);
      auto pins = x->bindingPin();
      auto pin = [&](P::BindingPinContext *p) {
        auto key = at<ast::PinKey>(p);
        key.spelling = p->getText();
        key.text = p->STRING() ? string(p) : key.spelling;
        return key;
      };
      result.first = pin(pins.front());
      if (pins.size() > 1)
        result.last = pin(pins.back());
      return result;
    }
    auto result = at<ast::FieldKey>(x);
    for (auto *item : x->keyItem())
      result.items.push_back(key_item(item));
    result.first = result.items.front().first;
    return result;
  }
  // A net reference, or flat(net) for an array net's elements in row-major
  // order.
  ast::Ptr target(P::TargetContext *x) {
    if (!x->LPAREN())
      return reference(x->reference());
    auto callee = name(x->name());
    if (callee.text != "flat")
      throw Failure("syntax.target",
                    "a connection target is a net reference or flat(net)",
                    begin(x->name()), end(x->name()));
    auto result = shared<ast::Call>(span(x));
    result->callee = std::make_shared<ast::Name>(std::move(callee));
    result->arguments.push_back(reference(x->reference()));
    return result;
  }
  ast::Ptr entry_node(P::BindingContext *x) {
    auto result = std::make_shared<ast::Binding>(at<ast::Binding>(x));
    auto *e = x->endpoint();
    auto &endpoint = result->endpoint;
    endpoint.span = span(e);
    endpoint.instance_indices = indices(e->index());
    endpoint.head =
        e->fieldKey() ? field_key(e->fieldKey()) : binding_key(e->bindingKey());
    for (auto *selection : e->endpointSuffix())
      endpoint.selectors.push_back(
          selection->name()
              ? ast::Ptr(std::make_shared<ast::Name>(name(selection->name())))
              : std::make_shared<ast::Index>(index(selection->index())));
    result->target = target(x->target());
    return result;
  }
  ast::Ptr entry_node(P::ArgumentContext *x) {
    auto result = std::make_shared<ast::Argument>(at<ast::Argument>(x));
    result->name = name(x->name());
    result->value = expr(x->expr());
    return result;
  }
  ast::Ptr entry_node(P::ElementOverrideContext *x) {
    auto result =
        std::make_shared<ast::ElementOverride>(at<ast::ElementOverride>(x));
    result->indices = indices(x->index());
    for (auto *child : x->children)
      if (dynamic_cast<P::ArgumentsContext *>(child) ||
          dynamic_cast<P::InstanceMetadataContext *>(child))
        result->sections.push_back(inst_section(child));
    return result;
  }
  ast::Section inst_section(Tree *t) {
    auto *ctx = dynamic_cast<Ctx *>(t);
    if (ctx->getRuleIndex() == P::RuleInstSection)
      return inst_section(ctx->children[0]);
    if (auto *x = dynamic_cast<P::ArgumentsContext *>(t))
      return section(x, "params", x->argument());
    if (auto *x = dynamic_cast<P::InstanceMetadataContext *>(t))
      return section(x, "metadata", x->field());
    auto *x = static_cast<P::ElementsContext *>(t);
    return section(x, "elements", x->elementOverride());
  }
  ast::Ptr instance(P::InstanceContext *x) {
    auto result = std::make_shared<ast::Instance>(at<ast::Instance>(x));
    result->name = name(x->name());
    result->dimensions = indices(x->index());
    result->component = path(x->path());
    // Bindings precede the sections and form the connections section.
    if (auto bindings = x->binding(); !bindings.empty()) {
      auto connections = section(x, "connections", bindings);
      connections.span = {owner, connections.entries.front()->span.start,
                          connections.entries.back()->span.end};
      result->sections.push_back(std::move(connections));
    }
    for (auto *child : x->instSection())
      result->sections.push_back(inst_section(child));
    std::vector<ast::Ptr> bindings, params;
    if (auto *args = x->inlineArguments())
      for (auto *arg : args->inlineArgument()) {
        if (arg->MUL()) {
          if (arg->getText() != "*" || result->wildcard)
            throw Failure("syntax.wildcard",
                          "write one * to connect same-name nets", begin(arg),
                          end(arg));
          result->wildcard = span(arg);
        } else if (arg->COLON()) {
          auto binding = std::make_shared<ast::Binding>(at<ast::Binding>(arg));
          auto &endpoint = binding->endpoint;
          endpoint.span = span(arg->pinKey());
          endpoint.head.span = endpoint.span;
          endpoint.head.first = pin_key(arg->pinKey());
          binding->target = target(arg->target());
          bindings.push_back(std::move(binding));
        } else if (arg->EQUAL()) {
          auto argument =
              std::make_shared<ast::Argument>(at<ast::Argument>(arg));
          argument->name = name(arg->name());
          argument->value = expr(arg->expr());
          params.push_back(std::move(argument));
        } else
          result->positional.push_back(target(arg->target()));
      }
    // Inline arguments join the first section of their kind, before its own
    // entries; otherwise they form a section at the end.
    for (auto &section : result->sections) {
      auto *inline_entries = section.name == "connections" ? &bindings
                             : section.name == "params"    ? &params
                                                           : nullptr;
      if (!inline_entries)
        continue;
      for (auto &entry : section.entries)
        inline_entries->push_back(std::move(entry));
      section.entries = std::move(*inline_entries);
      inline_entries->clear();
    }
    for (auto [entries, section_name] :
         {std::pair{&bindings, "connections"}, std::pair{&params, "params"}})
      if (!entries->empty()) {
        auto section = at<ast::Section>(x->inlineArguments());
        section.name = section_name;
        section.entries = std::move(*entries);
        result->sections.push_back(std::move(section));
      }
    return result;
  }
  void definition_item(P::DefinitionItemContext *x,
                       std::vector<ast::Ptr> &body) {
    if (auto *i = x->instance())
      body.push_back(instance(i));
    else if (auto *n = x->netDecl()) {
      // `net a, b: T` declares one net per name, sharing the type and fields.
      auto type = expr(n->expr());
      auto declared = fields(n->field());
      for (auto *declared_name : n->name()) {
        auto result = std::make_shared<ast::Net>(at<ast::Net>(n));
        result->name = name(declared_name);
        result->type = type;
        result->global = n->GLOBAL() != nullptr;
        result->fields = declared;
        body.push_back(std::move(result));
      }
    } else if (auto *s = x->section())
      body.push_back(std::make_shared<ast::Section>(
          section(s, s->sectionName()->getText(), s->field())));
    else
      body.push_back(entry_node(x->field()));
  }
  ast::Ptr item(Ctx *ctx) {
    if (auto *x = dynamic_cast<P::ImportDeclContext *>(ctx)) {
      auto result = std::make_shared<ast::Import>(at<ast::Import>(x));
      result->module = path(x->path());
      for (auto *imported : x->importedName()) {
        auto entry = at<ast::ImportedName>(imported);
        entry.name = name(imported->name(0));
        if (imported->AS())
          entry.alias = name(imported->name(1));
        result->names.push_back(std::move(entry));
      }
      return result;
    }
    auto *x = static_cast<P::DefinitionContext *>(ctx);
    auto result = std::make_shared<ast::Definition>(at<ast::Definition>(x));
    result->keyword = x->kind()->getText();
    result->name = name(x->name());
    if (x->path())
      result->base = path(x->path());
    for (auto *child : x->definitionItem())
      definition_item(child, result->body);
    return result;
  }

public:
  Builder(const Source &s, const Source *owner, std::size_t base)
      : s(s), owner(owner), base(base) {}
  std::vector<ast::Ptr> module(P::ModuleContext *ctx) {
    std::vector<ast::Ptr> items;
    for (auto *child : ctx->children)
      if (auto *c = dynamic_cast<Ctx *>(child))
        items.push_back(item(c));
    return items;
  }
};
std::vector<ast::Ptr> parse_items(const Source &source, const Source *owner,
                                  std::size_t base, ParseTimings *timings) {
  auto start = std::chrono::steady_clock::now();
  auto elapsed = [&] {
    auto end = std::chrono::steady_clock::now();
    auto duration = std::chrono::duration<double>(end - start).count();
    start = end;
    return duration;
  };
  auto masked = mask(source);
  if (timings)
    timings->comments = elapsed();
  antlr4::ANTLRInputStream input(masked);
  NoSchemeLexer lexer(&input);
  Errors errors(source);
  lexer.removeErrorListeners();
  lexer.addErrorListener(&errors);
  antlr4::ListTokenSource layout_source(layout(lexer, source), source.filename);
  antlr4::CommonTokenStream tokens(&layout_source);
  tokens.fill();
  if (timings)
    timings->tokens = elapsed();
  NoSchemeParser parser(&tokens);
  parser.removeErrorListeners();
  // ANTLR's documented two-stage strategy: try fast SLL prediction first,
  // then retry with full context and normal diagnostics if that cannot parse.
  auto *interpreter = parser.getInterpreter<antlr4::atn::ParserATNSimulator>();
  interpreter->setPredictionMode(antlr4::atn::PredictionMode::SLL);
  parser.setErrorHandler(std::make_shared<antlr4::BailErrorStrategy>());
  P::ModuleContext *tree;
  try {
    tree = parser.module();
  } catch (const antlr4::ParseCancellationException &) {
    parser.reset();
    interpreter->setPredictionMode(antlr4::atn::PredictionMode::LL);
    parser.setErrorHandler(std::make_shared<antlr4::DefaultErrorStrategy>());
    parser.addErrorListener(&errors);
    tree = parser.module();
  }
  if (timings)
    timings->syntax = elapsed();
  auto items = Builder(source, owner, base).module(tree);
  if (timings)
    timings->ast = elapsed();
  return items;
}
std::shared_ptr<const ast::Module>
validated(std::shared_ptr<const Source> source, std::vector<ast::Ptr> items,
          ParseTimings *timings) {
  auto start = std::chrono::steady_clock::now();
  auto result = std::make_shared<ast::Module>();
  result->span = {source.get(), 0, source->text.size()};
  result->source = std::move(source);
  result->items = std::move(items);
  auto diagnostics = validate_structure(*result);
  if (timings)
    timings->validation =
        std::chrono::duration<double>(std::chrono::steady_clock::now() - start)
            .count();
  if (!diagnostics.empty())
    throw DiagnosticsFailure(std::move(diagnostics));
  return result;
}
} // namespace
std::shared_ptr<const ast::Module> parse(std::shared_ptr<const Source> source,
                                         ParseTimings *timings) {
  auto items = parse_items(*source, source.get(), 0, timings);
  return validated(std::move(source), std::move(items), timings);
}
std::shared_ptr<const ast::Module>
parse_fragment(std::shared_ptr<const Source> source, std::size_t begin,
               std::size_t end) {
  Source fragment(source->text.substr(begin, end - begin), source->filename);
  auto items = parse_items(fragment, source.get(), begin, nullptr);
  return validated(std::move(source), std::move(items), nullptr);
}
} // namespace noscheme
