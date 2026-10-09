#include "noscheme/validation.hpp"
#include <functional>
#include <map>
#include <set>
namespace noscheme {
namespace {
using ast::as;
using ast::Kind;
class Validator {
  Json errors = Json::array();
  void error(std::string code, std::string message, const Span &span,
             const Span *previous = nullptr) {
    Json related = Json::array();
    if (previous)
      related.push_back(
          object("message", "previous declaration", "span", previous->json()));
    errors.push_back(object("code", std::move(code), "severity", "error",
                            "message", std::move(message), "span", span.json(),
                            "related", std::move(related), "instance_path",
                            nullptr, "configuration", nullptr, "application",
                            nullptr));
  }
  void reserved(const std::string &text, const Span &span) {
    static const std::set<std::string> names{"no_connect", "global", "true",
                                             "false",      "number", "string"};
    if (names.contains(text))
      error("syntax.reserved-name",
            "'" + text + "' is reserved and cannot be declared as a name",
            span);
  }
  void declared(const ast::Name &name) { reserved(name.text, name.span); }
  void declared(const ast::PinKey &key, bool physical = false) {
    if (!(physical && key.spelling.starts_with('"')))
      reserved(key.text, key.span);
  }
  // Keys are compared by an internal spelling; the message shows the
  // comparison form's JSON key.
  template <class T, class Key, class Describe>
  void unique(const std::vector<const T *> &nodes, Key key, Describe describe) {
    std::map<std::string, const T *> seen;
    for (const auto *n : nodes) {
      auto [it, added] = seen.emplace(key(*n), n);
      if (!added)
        error("syntax.duplicate", "duplicate entry " + describe(*n).dump(),
              n->span, &it->second->span);
    }
  }
  template <class T> void unique_names(const std::vector<const T *> &nodes) {
    unique(
        nodes, [](const T &n) { return n.name.text; },
        [](const T &n) { return Json(n.name.text); });
  }
  void unique_sections(const std::vector<const ast::Section *> &sections) {
    unique(
        sections, [](const ast::Section &n) { return n.name; },
        [](const ast::Section &n) { return Json(n.name); });
  }
  static Json key_json(const ast::FieldKey &k) {
    auto item = [](const ast::FieldKey &k) {
      return Json::array(
          {k.first.text, k.last ? Json(k.last->text) : Json(nullptr)});
    };
    if (k.items.empty())
      return item(k);
    Json result = Json::array();
    for (const auto &i : k.items)
      result.push_back(item(i));
    return result;
  }
  static std::string key_id(const ast::FieldKey &k) {
    auto item = [](const ast::FieldKey &k) {
      return k.first.text + (k.last ? "\x1f" + k.last->text : "\x1e");
    };
    if (k.items.empty())
      return item(k);
    std::string result = "\x1d";
    for (const auto &i : k.items)
      result += item(i) + "\x1c";
    return result;
  }
  template <class T>
  static std::vector<const T *> only(const std::vector<ast::Ptr> &nodes) {
    std::vector<const T *> result;
    for (const auto &n : nodes)
      if (auto *x = as<T>(n))
        result.push_back(x);
    return result;
  }
  static std::vector<const ast::Field *>
  pointers(const std::vector<ast::Field> &fields) {
    std::vector<const ast::Field *> result;
    for (const auto &f : fields)
      result.push_back(&f);
    return result;
  }
  void fields(const std::vector<const ast::Field *> &entries,
              bool defaults = false, bool pin_keys = false,
              bool instance_metadata = false, bool pins = false) {
    unique(
        entries, [](const ast::Field &f) { return key_id(f.key); },
        [](const ast::Field &f) { return key_json(f.key); });
    for (const auto *f : entries) {
      if (!f->key.aliases.empty() && !pins)
        error("syntax.alias-context",
              "pin aliases are declared in a component's pins section",
              f->key.span);
      if ((ast::is(f->value, Kind::Range) ||
           ast::is(f->value, Kind::Tolerance)) &&
          (f->key.first.text != "voltage" || f->default_value))
        error("syntax.range-context",
              "ranges and tolerances are only valid as a voltage value",
              f->value->span);
      if (!f->key.items.empty() && !pin_keys)
        error("syntax.pin-list-context",
              "pin lists are only allowed for physical pins and connection "
              "bindings",
              f->key.span);
      if (f->default_value && !defaults)
        error("syntax.default-context",
              "parameter default is only valid in a params declaration",
              f->span);
      if (defaults && (!f->fields.empty() || !f->value || f->key.last))
        error("syntax.parameter",
              "parameter declarations require a type and cannot have nested "
              "fields or ranges",
              f->span);
      fields(pointers(f->fields), false,
             instance_metadata && f->key.first.text == "pins");
    }
  }

public:
  Json run(const ast::Module &module) {
    auto definitions = only<ast::Definition>(module.items);
    for (const auto *item : only<ast::Import>(module.items)) {
      if (item->names.empty()) {
        for (const auto &n : item->module)
          declared(n);
      } else
        for (const auto &n : item->names)
          declared(n.local());
    }
    unique_names(definitions);
    std::map<std::string, std::set<std::string>> allowed = {
        {"component", {"params", "pins", "metadata"}},
        {"block", {"params", "ports", "metadata"}},
        {"board", {"metadata"}},
        {"bundle", {"metadata"}},
        {"nettype", {}}};
    for (const auto *d : definitions) {
      declared(d->name);
      const auto &kind = d->keyword;
      bool base = !d->base.empty();
      if (base && kind != "component" && kind != "block" && kind != "nettype")
        error(
            "syntax.inheritance-context",
            "only components, blocks, and net types may specialize definitions",
            d->span);
      if (kind == "nettype" && !base)
        error("syntax.nettype-base",
              "a net type extends a builtin nettype value or another net type",
              d->span);
      unique_sections(only<ast::Section>(d->body));
      auto bare = only<ast::Field>(d->body);
      if (kind == "nettype")
        fields(bare);
      std::vector<std::pair<std::string, const ast::Node *>> named;
      for (const auto &n : d->body)
        if (auto *net = as<ast::Net>(n))
          named.emplace_back(net->name.text, net);
        else if (auto *instance = as<ast::Instance>(n))
          named.emplace_back(instance->name.text, instance);
      {
        std::map<std::string, const ast::Node *> seen;
        for (const auto &[name, n] : named) {
          auto [it, added] = seen.emplace(name, n);
          if (!added)
            error("syntax.duplicate", "duplicate entry " + Json(name).dump(),
                  n->span, &it->second->span);
        }
      }
      for (const auto &n : d->body) {
        if (auto *section = as<ast::Section>(n)) {
          const auto &name = section->name;
          auto entries = only<ast::Field>(section->entries);
          if (name == "params" || name == "ports" || name == "pins")
            for (const auto *entry : entries) {
              auto check = [&](const ast::FieldKey &k) {
                declared(k.first, name == "pins");
                if (k.last)
                  declared(*k.last, name == "pins");
              };
              if (entry->key.items.empty())
                check(entry->key);
              else
                for (const auto &k : entry->key.items)
                  check(k);
            }
          auto permitted = allowed.at(kind);
          if (base) {
            permitted.erase("params");
            permitted.insert("defaults");
          }
          if (!permitted.contains(name))
            error("syntax.section-context",
                  name + " section is not allowed in this " + kind +
                      " definition",
                  section->span);
          fields(entries, name == "params", name == "pins", false,
                 name == "pins" && kind == "component");
        } else if (auto *field = as<ast::Field>(n)) {
          // Lark commits to structural keywords in a definition body. ANTLR
          // permits them as names in other contexts, so retain that boundary.
          const auto &first = field->key.first;
          if (!first.spelling.starts_with('"') &&
              std::set<std::string>{"params", "defaults", "pins", "ports",
                                    "metadata", "inst", "net", "global"}
                  .contains(first.text)) {
            error("syntax.invalid",
                  "expected a declaration or an indented section", field->span);
            continue;
          }
          if (kind == "nettype")
            continue;
          declared(first);
          if (kind != "bundle" || base)
            error("syntax.declaration-context",
                  "bare fields are only allowed in bundle and net type "
                  "definitions",
                  field->span);
        } else {
          bool is_net = n->kind == Kind::Net;
          declared(is_net ? as<ast::Net>(*n).name : as<ast::Instance>(*n).name);
          if ((kind != "block" && kind != "board") || base)
            error("syntax.declaration-context",
                  "internal circuit declarations require a base block or board",
                  n->span);
          if (auto *net = as<ast::Net>(n)) {
            if (net->global && kind != "board")
              error("syntax.global-context", "only a board may declare globals",
                    net->span);
            fields(pointers(net->fields));
          } else {
            const auto &instance = as<ast::Instance>(*n);
            std::vector<const ast::Section *> sections;
            for (const auto &section : instance.sections)
              sections.push_back(&section);
            unique_sections(sections);
            for (const auto &s : instance.sections)
              if (s.name == "elements")
                for (const auto *element :
                     only<ast::ElementOverride>(s.entries)) {
                  std::vector<const ast::Section *> nested;
                  for (const auto &section : element->sections)
                    nested.push_back(&section);
                  unique_sections(nested);
                  sections.insert(sections.end(), nested.begin(), nested.end());
                }
            for (const auto *s : sections) {
              if (s->name == "params")
                unique_names(only<ast::Argument>(s->entries));
              else if (s->name == "metadata")
                fields(only<ast::Field>(s->entries), false, false, true);
            }
          }
        }
      }
      fields(bare);
    }
    return errors;
  }
};
} // namespace
Json validate_structure(const ast::Module &module) {
  return Validator().run(module);
}
void UniqueDiagnostics::add(const Json &diagnostic) {
  auto hash = std::hash<Json>{}(diagnostic);
  auto [begin, end] = index_.equal_range(hash);
  for (auto it = begin; it != end; ++it)
    if (items_[it->second] == diagnostic)
      return;
  index_.emplace(hash, items_.size());
  items_.push_back(diagnostic);
}
} // namespace noscheme
