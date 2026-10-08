#include "noscheme/validation.hpp"
#include <functional>
#include <map>
#include <set>
namespace noscheme {
namespace {
class Validator {
  Json errors = Json::array();
  void error(std::string code, std::string message, const Json &node,
             const Json *previous = nullptr) {
    Json related = Json::array();
    if (previous)
      related.push_back(
          {{"message", "previous declaration"}, {"span", (*previous)["span"]}});
    errors.push_back({{"code", code},
                      {"severity", "error"},
                      {"message", message},
                      {"span", node["span"]},
                      {"related", related},
                      {"instance_path", nullptr},
                      {"configuration", nullptr},
                      {"application", nullptr}});
  }
  void declared(const Json &name, bool physical = false) {
    if (physical && name["spelling"].get<std::string>().starts_with('"'))
      return;
    auto text = name["text"].get<std::string>();
    if (std::set<std::string>{"no_connect", "global", "true", "false", "number",
                              "string"}
            .contains(text))
      error("syntax.reserved-name",
            "'" + text + "' is reserved and cannot be declared as a name",
            name);
  }
  using Nodes = std::vector<std::reference_wrapper<const Json>>;
  static const Json &value(const Json &node) { return node; }
  static const Json &value(std::reference_wrapper<const Json> node) {
    return node.get();
  }
  template <class Range, class Key> void unique(const Range &nodes, Key key) {
    std::map<std::string, const Json *> seen;
    for (const auto &entry : nodes) {
      const auto &n = value(entry);
      auto name = key(n);
      auto id = name.dump();
      auto [it, added] = seen.emplace(id, &n);
      if (!added)
        error("syntax.duplicate", "duplicate entry " + id, n, it->second);
    }
  }
  static Json key(const Json &field) {
    const auto &k = field["key"];
    auto item = [](const Json &k) {
      return Json::array({k["first"]["text"], k["last"].is_null()
                                                  ? Json(nullptr)
                                                  : k["last"]["text"]});
    };
    if (k["items"].empty())
      return item(k);
    Json result = Json::array();
    for (const auto &i : k["items"])
      result.push_back(item(i));
    return result;
  }
  static Nodes only(const Json &nodes,
                    std::initializer_list<std::string> kinds) {
    Nodes a;
    for (const auto &n : nodes)
      for (const auto &kind : kinds)
        if (n["node"] == kind) {
          a.push_back(std::cref(n));
          break;
        }
    return a;
  }
  template <class Range>
  void fields(const Range &entries, bool defaults = false,
              bool pin_keys = false, bool instance_metadata = false) {
    unique(entries, key);
    for (const auto &entry : entries) {
      const auto &f = value(entry);
      if (!f["key"]["items"].empty() && !pin_keys)
        error("syntax.pin-list-context",
              "pin lists are only allowed for physical pins and connection "
              "bindings",
              f["key"]);
      if (!f["default"].is_null() && !defaults)
        error("syntax.default-context",
              "parameter default is only valid in a params declaration", f);
      if (defaults && (!f["fields"].empty() || f["value"].is_null() ||
                       !f["key"]["last"].is_null()))
        error("syntax.parameter",
              "parameter declarations require a type and cannot have nested "
              "fields or ranges",
              f);
      fields(f["fields"], false,
             instance_metadata && f["key"]["first"]["text"] == "pins");
    }
  }

public:
  Json run(const Json &module) {
    auto definitions = only(module["items"], {"Definition"});
    for (const auto &item : module["items"]) {
      if (item["node"] != "Import")
        continue;
      if (item["names"].empty()) {
        for (const auto &n : item["module"])
          declared(n);
      } else
        for (const auto &n : item["names"])
          declared(n["alias"].is_null() ? n["name"] : n["alias"]);
    }
    unique(definitions, [](const Json &n) { return n["name"]["text"]; });
    std::map<std::string, std::set<std::string>> allowed = {
        {"component", {"params", "pins", "metadata"}},
        {"block", {"params", "ports", "metadata"}},
        {"board", {"metadata"}},
        {"bundle", {"metadata"}},
        {"nettype", {}}};
    for (const auto &definition : definitions) {
      const auto &d = definition.get();
      declared(d["name"]);
      auto kind = d["kind"].get<std::string>();
      bool base = !d["base"].is_null();
      if (base && kind != "component" && kind != "block" && kind != "nettype")
        error(
            "syntax.inheritance-context",
            "only components, blocks, and net types may specialize definitions",
            d);
      if (kind == "nettype" && !base)
        error("syntax.nettype-base",
              "a net type extends a builtin nettype value or another net type",
              d);
      unique(only(d["body"], {"Section"}),
             [](const Json &n) { return n["name"]; });
      if (kind == "nettype")
        fields(only(d["body"], {"Field"}));
      unique(only(d["body"], {"Net", "Instance"}),
             [](const Json &n) { return n["name"]["text"]; });
      for (const auto &n : d["body"]) {
        auto type = n["node"].get<std::string>();
        if (type == "Section") {
          auto name = n["name"].get<std::string>();
          if (name == "params" || name == "ports" || name == "pins")
            for (const auto &entry : n["entries"]) {
              Json keys = entry["key"]["items"].empty()
                              ? Json::array({entry["key"]})
                              : entry["key"]["items"];
              for (const auto &k : keys) {
                declared(k["first"], name == "pins");
                if (!k["last"].is_null())
                  declared(k["last"], name == "pins");
              }
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
                  n);
          fields(n["entries"], name == "params", name == "pins");
        } else if (type == "Field") {
          // Lark commits to structural keywords in a definition body. ANTLR
          // permits them as names in other contexts, so retain that boundary.
          auto key_name = n["key"]["first"]["text"].get<std::string>();
          if (!n["key"]["first"]["spelling"].get<std::string>().starts_with(
                  '"') &&
              std::set<std::string>{"params", "defaults", "pins", "ports",
                                    "metadata", "inst", "net", "global"}
                  .contains(key_name)) {
            error("syntax.invalid",
                  "expected a declaration or an indented section", n);
            continue;
          }
          if (kind == "nettype")
            continue;
          declared(n["key"]["first"]);
          if (kind != "bundle" || base)
            error("syntax.declaration-context",
                  "bare fields are only allowed in bundle and net type "
                  "definitions",
                  n);
        } else if (type == "Net" || type == "Instance") {
          declared(n["name"]);
          if ((kind != "block" && kind != "board") || base)
            error("syntax.declaration-context",
                  "internal circuit declarations require a base block or board",
                  n);
          if (type == "Net") {
            if (n["global_"].get<bool>() && kind != "board")
              error("syntax.global-context", "only a board may declare globals",
                    n);
            fields(n["fields"]);
          } else {
            unique(n["sections"], [](const Json &s) { return s["name"]; });
            Nodes sections;
            for (const auto &section : n["sections"])
              sections.push_back(std::cref(section));
            for (const auto &s : n["sections"])
              if (s["name"] == "elements")
                for (const auto &element : s["entries"]) {
                  unique(element["sections"],
                         [](const Json &s) { return s["name"]; });
                  for (const auto &nested : element["sections"])
                    sections.push_back(std::cref(nested));
                }
            for (const auto &section : sections) {
              const auto &s = section.get();
              if (s["name"] == "params")
                unique(s["entries"],
                       [](const Json &e) { return e["name"]["text"]; });
              else if (s["name"] == "metadata")
                fields(s["entries"], false, false, true);
            }
          }
        }
      }
      fields(only(d["body"], {"Field"}));
    }
    return errors;
  }
};
} // namespace
Json validate_structure(const Json &module) { return Validator().run(module); }
} // namespace noscheme
