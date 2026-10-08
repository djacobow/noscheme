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
std::string mask(const Source &s) {
  antlr4::ANTLRInputStream input(s.text);
  NoSchemeLexer lexer(&input);
  lexer.removeErrorListeners();
  std::vector<bool> comments(s.text.size(), false);
  for (auto token = lexer.nextToken(); token->getType() != antlr4::Token::EOF;
       token = lexer.nextToken()) {
    auto type = token->getType();
    auto b = byte(s, token->getStartIndex()),
         e = byte(s, token->getStopIndex() + 1);
    if (type == P::BLOCK_COMMENT || type == P::LINE_COMMENT) {
      for (auto i = b; i < e; ++i)
        comments[i] = true;
    } else if (type == P::MUL && token->getText() == "/" &&
               s.text.compare(b, 2, "/*") == 0)
      throw Failure("syntax.invalid", "unterminated block comment", b, b + 1);
  }
  std::string masked;
  for (std::size_t i = 0; i + 1 < s.offsets.size(); ++i) {
    auto b = s.offsets[i], e = s.offsets[i + 1];
    if (comments[b] && s.text[b] != '\r' && s.text[b] != '\n')
      masked += ' ';
    else
      masked += s.text.substr(b, e - b);
  }
  Source masked_source(masked, s.filename);
  for (std::size_t i = 0; i < masked.size();) {
    auto e = masked.find('\n', i);
    if (e == std::string::npos)
      e = masked.size();
    auto first = masked.find_first_not_of(" \t\r", i);
    if (first < e)
      for (auto j = i; j < first; ++j)
        if (masked[j] == '\t')
          throw Failure("syntax.indentation",
                        "tabs are not allowed in indentation",
                        byte(s, masked_source.characters[j]),
                        byte(s, masked_source.characters[j] + 1));
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
class Builder {
  const Source &s;
  Json empty = Json::array();
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
  Json node(std::string kind, Tree *t, Json fields) {
    fields["node"] = std::move(kind);
    fields["span"] = s.span(begin(t), end(t));
    return fields;
  }
  Json spanning(std::string kind, const Json &left, const Json &right,
                Json fields) {
    fields["node"] = std::move(kind);
    fields["span"] =
        s.span(left["span"]["start_pos"], right["span"]["end_pos"]);
    return fields;
  }
  Json name(Tree *t) { return node("Name", t, object("text", t->getText())); }
  template <class T> Json all(const std::vector<T *> &items) {
    Json a = empty;
    for (auto *item : items)
      a.push_back(build(item));
    return a;
  }
  Json string(Tree *t) {
    try {
      return Json::parse(t->getText());
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
  Json suffix(Json result, Tree *tree) {
    auto *x = dynamic_cast<P::SuffixContext *>(tree);
    if (x->name())
      return spanning("Member", result, build(x->name()),
                      object("base", result, "name", build(x->name())));
    if (x->index())
      return spanning("Subscript", result, build(x->index()),
                      object("base", result, "selection", build(x->index())));
    Json args = empty, kwargs = empty;
    std::set<std::string> seen;
    for (auto *arg : x->callArgument()) {
      if (arg->EQUAL()) {
        auto n = build(arg->name());
        auto text = n["text"].get<std::string>();
        if (!seen.insert(text).second)
          throw Failure("syntax.duplicate",
                        "duplicate named argument '" + text + "'",
                        begin(arg->name()), end(arg->name()));
        kwargs.push_back(node("KeywordArgument", arg,
                              object("name", n, "value", build(arg->expr()))));
      } else {
        if (!kwargs.empty())
          throw Failure("syntax.argument-order",
                        "positional arguments must precede named arguments",
                        begin(arg), end(arg));
        args.push_back(build(arg->expr()));
      }
    }
    auto call =
        node("Call", x,
             object("callee", result, "arguments", args, "keywords", kwargs));
    call["span"] = s.span(result["span"]["start_pos"], end(x));
    return call;
  }

public:
  explicit Builder(const Source &s) : s(s) {}
  Json build(Tree *tree) {
    auto *ctx = dynamic_cast<Ctx *>(tree);
    if (!ctx)
      throw std::logic_error("expected parser rule");
    switch (ctx->getRuleIndex()) {
    case P::RuleModule: {
      Json items = empty;
      for (auto *child : ctx->children)
        if (dynamic_cast<Ctx *>(child))
          items.push_back(build(child));
      return object("node", "Module", "span", s.span(0, s.text.size()), "items",
                    std::move(items));
    }
    case P::RuleName:
      return name(ctx);
    case P::RulePath: {
      auto *x = static_cast<P::PathContext *>(ctx);
      return all(x->name());
    }
    case P::RuleImportDecl: {
      auto *x = static_cast<P::ImportDeclContext *>(ctx);
      return node(
          "Import", x,
          object("module", build(x->path()), "names", all(x->importedName())));
    }
    case P::RuleImportedName: {
      auto *x = static_cast<P::ImportedNameContext *>(ctx);
      return node("ImportedName", x,
                  object("name", build(x->name(0)), "alias",
                         x->AS() ? build(x->name(1)) : Json(nullptr)));
    }
    case P::RuleDefinition: {
      auto *x = static_cast<P::DefinitionContext *>(ctx);
      return node("Definition", x,
                  object("kind", x->kind()->getText(), "name", build(x->name()),
                         "base", x->path() ? build(x->path()) : Json(nullptr),
                         "body", all(x->definitionItem())));
    }
    case P::RuleDefinitionItem:
    case P::RuleInstSection:
    case P::RuleExpr:
      return build(ctx->children[0]);
    case P::RuleSection: {
      auto *x = static_cast<P::SectionContext *>(ctx);
      return node("Section", x,
                  object("name", x->sectionName()->getText(), "entries",
                         all(x->field())));
    }
    case P::RuleField: {
      auto *x = static_cast<P::FieldContext *>(ctx);
      return node(
          "Field", x,
          object("key", build(x->fieldKey()), "value",
                 x->expr().empty() ? Json(nullptr) : build(x->expr(0)),
                 "default",
                 x->expr().size() < 2 ? Json(nullptr) : build(x->expr(1)),
                 "fields", all(x->field())));
    }
    case P::RulePinKey:
      return node("PinKey", ctx,
                  object("text",
                         ctx->getText().starts_with('"') ? string(ctx)
                                                         : Json(ctx->getText()),
                         "spelling", ctx->getText()));
    case P::RuleKeyItem: {
      auto *x = static_cast<P::KeyItemContext *>(ctx);
      return node("FieldKey", x,
                  object("first", build(x->pinKey(0)), "last",
                         x->RANGE() ? build(x->pinKey(1)) : Json(nullptr),
                         "items", empty));
    }
    case P::RuleFieldKey: {
      auto *x = static_cast<P::FieldKeyContext *>(ctx);
      if (!x->LBRACK())
        return build(x->keyItem(0));
      auto items = all(x->keyItem());
      return node(
          "FieldKey", x,
          object("first", items[0]["first"], "last", nullptr, "items", items));
    }
    case P::RuleNetDecl: {
      auto *x = static_cast<P::NetDeclContext *>(ctx);
      return node("Net", x,
                  object("name", build(x->name()), "type", build(x->expr()),
                         "global_", x->GLOBAL() != nullptr, "fields",
                         all(x->field())));
    }
    case P::RuleIndex: {
      auto *x = static_cast<P::IndexContext *>(ctx);
      return node("Index", x,
                  object("start", build(x->expr(0)), "stop",
                         x->COLON() ? build(x->expr(1)) : Json(nullptr)));
    }
    case P::RuleEndpoint: {
      auto *x = static_cast<P::EndpointContext *>(ctx);
      Json selectors = empty;
      for (auto *sel : x->endpointSuffix())
        selectors.push_back(sel->name() ? build(sel->name())
                                        : build(sel->index()));
      return node("Endpoint", x,
                  object("instance_indices", all(x->index()), "head",
                         build(x->fieldKey()), "selectors", selectors));
    }
    case P::RuleBinding: {
      auto *x = static_cast<P::BindingContext *>(ctx);
      return node("Binding", x,
                  object("endpoint", build(x->endpoint()), "target",
                         build(x->reference())));
    }
    case P::RuleArgument: {
      auto *x = static_cast<P::ArgumentContext *>(ctx);
      return node("Argument", x,
                  object("name", build(x->name()), "value", build(x->expr())));
    }
    case P::RuleConnections: {
      auto *x = static_cast<P::ConnectionsContext *>(ctx);
      return node("Section", x,
                  object("name", "connections", "entries", all(x->binding())));
    }
    case P::RuleArguments: {
      auto *x = static_cast<P::ArgumentsContext *>(ctx);
      return node("Section", x,
                  object("name", "params", "entries", all(x->argument())));
    }
    case P::RuleInstanceMetadata: {
      auto *x = static_cast<P::InstanceMetadataContext *>(ctx);
      return node("Section", x,
                  object("name", "metadata", "entries", all(x->field())));
    }
    case P::RuleElements: {
      auto *x = static_cast<P::ElementsContext *>(ctx);
      return node(
          "Section", x,
          object("name", "elements", "entries", all(x->elementOverride())));
    }
    case P::RuleElementOverride: {
      auto *x = static_cast<P::ElementOverrideContext *>(ctx);
      Json sections = empty;
      for (auto *child : x->children)
        if (dynamic_cast<P::ArgumentsContext *>(child) ||
            dynamic_cast<P::InstanceMetadataContext *>(child))
          sections.push_back(build(child));
      return node(
          "ElementOverride", x,
          object("indices", all(x->index()), "sections", std::move(sections)));
    }
    case P::RuleInstance: {
      auto *x = static_cast<P::InstanceContext *>(ctx);
      Json positional = empty, bindings = empty, params = empty,
           sections = all(x->instSection());
      if (auto *args = x->inlineArguments())
        for (auto *arg : args->inlineArgument()) {
          if (arg->COLON()) {
            auto pk = build(arg->pinKey());
            auto key =
                node("FieldKey", arg->pinKey(),
                     object("first", pk, "last", nullptr, "items", empty));
            auto ep = node("Endpoint", arg->pinKey(),
                           object("instance_indices", empty, "head", key,
                                  "selectors", empty));
            bindings.push_back(node(
                "Binding", arg,
                object("endpoint", ep, "target", build(arg->reference()))));
          } else if (arg->EQUAL())
            params.push_back(node("Argument", arg,
                                  object("name", build(arg->name()), "value",
                                         build(arg->expr()))));
          else
            positional.push_back(build(arg->reference()));
        }
      for (auto &section : sections) {
        Json *in = section["name"] == "connections" ? &bindings
                   : section["name"] == "params"    ? &params
                                                    : nullptr;
        if (in) {
          for (auto &entry : section["entries"])
            in->push_back(std::move(entry));
          section["entries"] = std::move(*in);
          *in = Json::array();
        }
      }
      if (!bindings.empty())
        sections.push_back(node(
            "Section", x->inlineArguments(),
            object("name", "connections", "entries", std::move(bindings))));
      if (!params.empty())
        sections.push_back(
            node("Section", x->inlineArguments(),
                 object("name", "params", "entries", std::move(params))));
      return node("Instance", x,
                  object("name", build(x->name()), "dimensions",
                         all(x->index()), "component", build(x->path()),
                         "sections", std::move(sections), "positional",
                         std::move(positional)));
    }
    case P::RuleShift:
    case P::RuleSum:
    case P::RuleProduct: {
      auto result = build(ctx->children[0]);
      for (std::size_t i = 1; i < ctx->children.size(); i += 2) {
        auto right = build(ctx->children[i + 1]);
        result = spanning("Binary", result, right,
                          object("operator", ctx->children[i]->getText(),
                                 "left", result, "right", right));
      }
      return result;
    }
    case P::RuleUnary: {
      auto *x = static_cast<P::UnaryContext *>(ctx);
      if (x->postfix())
        return build(x->postfix());
      return node("Unary", x,
                  object("operator", x->ADD()->getText(), "operand",
                         build(x->unary())));
    }
    case P::RulePostfix: {
      auto *x = static_cast<P::PostfixContext *>(ctx);
      auto result = build(x->atom());
      for (auto *sel : x->suffix())
        result = suffix(std::move(result), sel);
      return result;
    }
    case P::RuleReference: {
      auto *x = static_cast<P::ReferenceContext *>(ctx);
      auto result = build(x->name(0));
      for (std::size_t i = 1; i < x->children.size(); ++i) {
        auto *child = x->children[i];
        if (dynamic_cast<P::NameContext *>(child)) {
          auto n = build(child);
          result =
              spanning("Member", result, n, object("base", result, "name", n));
        } else if (dynamic_cast<P::IndexContext *>(child)) {
          auto index = build(child);
          result = spanning("Subscript", result, index,
                            object("base", result, "selection", index));
        }
      }
      return result;
    }
    case P::RuleAtom: {
      auto *x = static_cast<P::AtomContext *>(ctx);
      if (x->name())
        return build(x->name());
      if (x->STRING())
        return node("String", x,
                    object("value", string(x), "spelling", x->getText()));
      if (x->LPAREN())
        return node("Group", x, object("expression", build(x->expr(0))));
      if (x->LBRACK())
        return node("ListExpr", x, object("items", all(x->expr())));
      auto type = x->SCALED() ? tag(x->getText())
                  : x->INT()  ? "integer"
                              : "double";
      if (type == "invalid")
        throw Failure(
            "syntax.number",
            "a scaled number has one decimal mark: 4.7k or 4k7, not 4.7k7",
            begin(x), end(x));
      return node("Number", x, object("spelling", x->getText(), "tag", type));
    }
    default:
      throw std::logic_error("unhandled generated parser rule");
    }
  }
};
} // namespace
Json parse(const Source &source, ParseTimings *timings) {
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
  auto result = Builder(source).build(tree);
  if (timings)
    timings->ast = elapsed();
  auto diagnostics = validate_structure(result);
  if (timings)
    timings->validation = elapsed();
  if (!diagnostics.empty())
    throw DiagnosticsFailure(std::move(diagnostics));
  return result;
}
} // namespace noscheme
