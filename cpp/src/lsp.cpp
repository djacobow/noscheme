// Experimental stdio LSP. Only advertise implemented capabilities.
#include "noscheme/formatter.hpp"
#include "noscheme/lsp_analysis.hpp"
#include "noscheme/source.hpp"
#include "noscheme/validation.hpp"
#include <algorithm>
#include <charconv>
#include <condition_variable>
#include <iostream>
#include <map>
#include <mutex>
#include <optional>
#include <set>
#include <string>
#include <thread>

namespace {
using noscheme::Json;
using noscheme::object;
constexpr std::size_t max_message = 16 * 1024 * 1024;

// Bound headers as well as payloads; framing failures cannot be resynchronized.
std::optional<std::string> receive() {
  std::string header;
  char c;
  while (std::cin.get(c)) {
    header += c;
    if (header.ends_with("\r\n\r\n"))
      break;
    if (header.size() > 8192)
      throw std::runtime_error("LSP header exceeds 8192 bytes");
  }
  if (header.empty() && std::cin.eof())
    return std::nullopt;
  if (!header.ends_with("\r\n\r\n"))
    throw std::runtime_error("truncated LSP header");
  std::optional<std::size_t> length;
  std::size_t start = 0;
  while (start + 2 < header.size()) {
    auto end = header.find("\r\n", start);
    auto line = header.substr(start, end - start);
    auto colon = line.find(':');
    if (colon == std::string::npos)
      throw std::runtime_error("invalid LSP header");
    auto name = line.substr(0, colon);
    std::transform(name.begin(), name.end(), name.begin(),
                   [](unsigned char ch) {
                     return ch >= 'A' && ch <= 'Z' ? ch + ('a' - 'A') : ch;
                   });
    if (name == "content-length") {
      if (length)
        throw std::runtime_error("duplicate Content-Length");
      auto value = line.substr(colon + 1);
      auto first = value.find_first_not_of(" \t");
      auto last = value.find_last_not_of(" \t");
      if (first == std::string::npos)
        throw std::runtime_error("missing Content-Length value");
      value = value.substr(first, last - first + 1);
      std::size_t size;
      auto [ptr, error] =
          std::from_chars(value.data(), value.data() + value.size(), size);
      if (error != std::errc{} || ptr != value.data() + value.size() ||
          size > max_message)
        throw std::runtime_error("invalid or oversized Content-Length");
      length = size;
    }
    start = end + 2;
  }
  if (!length)
    throw std::runtime_error("missing Content-Length");
  std::string body(*length, '\0');
  if (!std::cin.read(body.data(), static_cast<std::streamsize>(*length)))
    throw std::runtime_error("truncated LSP payload");
  return body;
}
void send(Json message) {
  static std::mutex output_mutex;
  std::lock_guard output_lock(output_mutex);
  message["jsonrpc"] = "2.0";
  auto body = message.dump();
  std::cout << "Content-Length: " << body.size() << "\r\n\r\n"
            << body << std::flush;
}
void error(const Json &id, int code, const std::string &message) {
  send(object("id", id, "error", object("code", code, "message", message)));
}
Json position(const noscheme::Source &source, std::size_t offset) {
  offset = std::min(offset, source.text.size());
  auto line =
      std::upper_bound(source.lines.begin(), source.lines.end(), offset) -
      source.lines.begin() - 1;
  std::size_t units = 0;
  for (auto i = source.lines[line]; i < offset; ++i) {
    auto ch = static_cast<unsigned char>(source.text[i]);
    if ((ch & 0xc0) != 0x80)
      units += ch >= 0xf0 ? 2 : 1;
  }
  return object("line", line, "character", units);
}
std::size_t byte_offset(const noscheme::Source &source, const Json &where) {
  if (!where.at("line").is_number_integer() ||
      !where.at("character").is_number_integer())
    throw std::invalid_argument("position must contain integers");
  auto line = where.at("line").get<std::int64_t>();
  auto character = where.at("character").get<std::int64_t>();
  if (line < 0 || character < 0 ||
      static_cast<std::size_t>(line) >= source.lines.size())
    throw std::invalid_argument("position outside document");
  auto offset = source.lines[line];
  std::int64_t units = 0;
  while (offset < source.text.size() && source.text[offset] != '\r' &&
         source.text[offset] != '\n' && units < character) {
    auto c = static_cast<unsigned char>(source.text[offset]);
    units += c >= 0xf0 ? 2 : 1;
    offset += c < 0x80 ? 1 : c < 0xe0 ? 2 : c < 0xf0 ? 3 : 4;
  }
  if (units != character)
    throw std::invalid_argument(
        "position outside line or inside surrogate pair");
  return offset;
}
struct Document {
  std::string text;
  std::int64_t version;
};
class Server {
  bool initialized = false, shutdown = false;
  std::map<std::string, Document> documents;
  noscheme::fs::path project, python;
  std::vector<noscheme::fs::path> workspace_roots;
  noscheme::ParsedSources cache;
  std::optional<noscheme::RegistryDescription> standard_registry;
  std::set<std::string> published;

  std::mutex mutex;
  std::condition_variable changed;
  bool stopping = false, pending = false;
  std::uint64_t generation = 0;
  std::stop_source cancellation;
  std::chrono::steady_clock::time_point due;
  std::thread worker;
  std::shared_ptr<const noscheme::LspAnalysis> latest;
  std::vector<Json> requests;
  bool markdown = false, versioned_edits = false;
  std::string analysis_failure;
  struct AnalysisOutput {
    std::map<std::string, Json> grouped;
    noscheme::LspAnalysis analysis;
  };
  void reject_requests(int code, const std::string &reason) {
    for (const auto &request : requests)
      error(request.at("id"), code, reason);
    requests.clear();
  }
  void respond(const Json &request) {
    auto id = request.at("id");
    try {
      const auto &params = request.at("params");
      auto uri = params.at("textDocument").at("uri").get<std::string>();
      auto path = noscheme::uri_path(uri);
      Json response = request.at("method") == "textDocument/references"
                          ? Json::array()
                          : Json(nullptr);
      if (path && latest && latest->sources.contains(*path)) {
        noscheme::Source source(latest->sources.at(*path), path->string());
        if (request.at("method") == "textDocument/formatting") {
          try {
            auto formatted = noscheme::format_source(source);
            Json edits = Json::array();
            if (formatted != source.text)
              edits.push_back(
                  object("range",
                         object("start", position(source, 0), "end",
                                position(source, source.text.size())),
                         "newText", formatted));
            send(object("id", id, "result", edits));
          } catch (const std::exception &e) {
            error(id, -32803, e.what());
          }
          return;
        }
        auto offset = byte_offset(source, params.at("position"));
        if (request.at("method") == "textDocument/prepareRename" ||
            request.at("method") == "textDocument/rename") {
          try {
            const noscheme::RenameContext *context = nullptr;
            for (const auto &candidate : latest->rename_contexts)
              for (const auto &[name, module] : candidate->project.modules)
                if (module.path == *path) {
                  if (context && context != candidate.get())
                    throw std::runtime_error(
                        "Multiple projects contain this file; select --project "
                        "for rename.");
                  context = candidate.get();
                }
            if (!context)
              throw std::runtime_error(
                  "Rename requires a successfully elaborated project.");
            auto plan = noscheme::rename_plan(
                *context, latest->sources, latest->references, *path, offset,
                request.at("method") == "textDocument/rename"
                    ? std::optional(params.at("newName").get<std::string>())
                    : std::nullopt);
            auto range = [&](const Json &span) {
              noscheme::Source source(
                  latest->sources.at(span.at("filename").get<std::string>()),
                  span.at("filename"));
              return object("start", position(source, span.at("start_pos")),
                            "end", position(source, span.at("end_pos")));
            };
            if (request.at("method") == "textDocument/prepareRename")
              send(object("id", id, "result",
                          object("range", range(plan.at("span")), "placeholder",
                                 plan.at("placeholder"))));
            else {
              Json changes = Json::object();
              for (const auto &edit : plan.at("edits")) {
                auto uri = noscheme::file_uri(
                    edit.at("span").at("filename").get<std::string>());
                if (!changes.contains(uri))
                  changes[uri] = Json::array();
                changes[uri].push_back(object("range", range(edit.at("span")),
                                              "newText", edit.at("newText")));
              }
              Json edit = object("changes", changes);
              if (versioned_edits) {
                Json entries = Json::array();
                for (const auto &[uri, edits] : changes.items())
                  entries.push_back(
                      object("textDocument",
                             object("uri", uri, "version",
                                    documents.contains(uri)
                                        ? Json(documents.at(uri).version)
                                        : Json()),
                             "edits", edits));
                edit = object("documentChanges", entries);
              }
              send(object("id", id, "result", edit));
            }
          } catch (const noscheme::DiagnosticsFailure &e) {
            error(id, -32803,
                  "Rename rejected: " +
                      e.diagnostics.front().at("message").get<std::string>());
          } catch (const std::exception &e) {
            error(id, -32803, e.what());
          }
          return;
        }
        if (request.at("method") == "textDocument/completion") {
          Json items = Json::array();
          {
            auto found = latest->completions.find(*path);
            const std::vector<noscheme::CompletionEntry> empty;
            auto completion = noscheme::complete(
                source.text, offset,
                found == latest->completions.end() ? empty : found->second);
            for (const auto &entry : completion.entries)
              items.push_back(object(
                  "label", entry.name, "kind", entry.kind, "detail",
                  entry.description, "textEdit",
                  object("range",
                         object("start", position(source, completion.start),
                                "end", position(source, completion.end)),
                         "newText",
                         entry.insert_text.empty() ? entry.name
                                                   : entry.insert_text)));
          }
          send(object("id", id, "result",
                      object("isIncomplete", false, "items", items)));
          return;
        }
        const noscheme::LspReference *selected = nullptr;
        for (const auto &reference : latest->references) {
          const auto &span = reference.span;
          if (span.at("filename") != path->string() ||
              offset < span.at("start_pos").get<std::size_t>() ||
              offset >= span.at("end_pos").get<std::size_t>())
            continue;
          if (!selected ||
              span.at("end_pos").get<std::size_t>() -
                      span.at("start_pos").get<std::size_t>() <
                  selected->span.at("end_pos").get<std::size_t>() -
                      selected->span.at("start_pos").get<std::size_t>())
            selected = &reference;
        }
        if (selected) {
          auto range = [](const noscheme::Source &text, const Json &span) {
            return object("start", position(text, span.at("start_pos")), "end",
                          position(text, span.at("end_pos")));
          };
          if (request.at("method") == "textDocument/references") {
            auto include =
                params.at("context").at("includeDeclaration").get<bool>();
            if (!selected->target.is_null()) {
              for (const auto &reference : latest->references) {
                if (reference.target != selected->target ||
                    (!include && reference.span == reference.target))
                  continue;
                noscheme::fs::path path =
                    reference.span.at("filename").get<std::string>();
                if (!latest->sources.contains(path))
                  continue;
                noscheme::Source text(latest->sources.at(path), path.string());
                auto location = object("uri", noscheme::file_uri(path), "range",
                                       range(text, reference.span));
                if (std::find(response.begin(), response.end(), location) ==
                    response.end())
                  response.push_back(std::move(location));
              }
            }
          } else if (request.at("method") == "textDocument/definition") {
            if (selected->target.is_null()) {
              send(object("id", id, "result", nullptr));
              return;
            }
            noscheme::fs::path target =
                selected->target.at("filename").get<std::string>();
            if (latest->sources.contains(target)) {
              noscheme::Source text(latest->sources.at(target),
                                    target.string());
              response = object("uri", noscheme::file_uri(target), "range",
                                range(text, selected->target));
            }
          } else {
            auto description = selected->description;
            if (markdown) {
              auto end = description.find('\n');
              description =
                  "```nosch\n" + description.substr(0, end) + "\n```" +
                  (end == std::string::npos ? "" : description.substr(end));
            }
            response =
                object("contents",
                       object("kind", markdown ? "markdown" : "plaintext",
                              "value", description),
                       "range", range(source, selected->span));
          }
        }
      }
      send(object("id", id, "result", response));
    } catch (const std::exception &e) {
      error(id, -32602, e.what());
    }
  }

  void schedule() {
    latest.reset();
    analysis_failure.clear();
    reject_requests(-32801, "Document changed during request");
    cancellation.request_stop();
    cancellation = std::stop_source();
    ++generation;
    pending = true;
    due = std::chrono::steady_clock::now() + std::chrono::milliseconds(200);
    changed.notify_one();
  }
  void stop() {
    reject_requests(-32800, "Server shutting down");
    stopping = true;
    cancellation.request_stop();
    changed.notify_one();
  }
  void work() {
    std::unique_lock lock(mutex);
    while (!stopping) {
      changed.wait(lock, [&] { return stopping || pending; });
      if (stopping)
        break;
      auto revision = generation;
      if (changed.wait_until(lock, due,
                             [&] {
                               return stopping || generation != revision ||
                                      !requests.empty();
                             }) &&
          (stopping || generation != revision))
        continue;
      auto snapshot = documents;
      auto token = cancellation.get_token();
      pending = false;
      lock.unlock();
      AnalysisOutput output;
      std::string failure;
      try {
        output = analyze(
            snapshot, token, [&](const noscheme::LspAnalysis &analysis) {
              std::lock_guard guard(mutex);
              if (stopping || revision != generation || token.stop_requested())
                return;
              latest = std::make_shared<const noscheme::LspAnalysis>(analysis);
              for (const auto &request : requests)
                respond(request);
              requests.clear();
            });
      } catch (const noscheme::Cancelled &) {
      } catch (const std::exception &e) {
        failure = e.what();
      }
      lock.lock();
      if (stopping || revision != generation || token.stop_requested())
        continue;
      if (!failure.empty()) {
        analysis_failure = failure;
        reject_requests(-32603, failure);
        send(object("method", "window/logMessage", "params",
                    object("type", 1, "message", failure)));
        continue;
      }
      latest = std::make_shared<const noscheme::LspAnalysis>(
          std::move(output.analysis));
      for (const auto &request : requests)
        respond(request);
      requests.clear();
      auto &grouped = output.grouped;
      for (const auto &uri : published)
        if (!grouped.contains(uri))
          grouped[uri] = Json::array();
      published.clear();
      for (const auto &[uri, diagnostics] : grouped) {
        auto params = object("uri", uri, "diagnostics", diagnostics);
        if (auto found = snapshot.find(uri); found != snapshot.end())
          params["version"] = found->second.version;
        if (!diagnostics.empty())
          published.insert(uri);
        send(object("method", "textDocument/publishDiagnostics", "params",
                    params));
      }
    }
  }

  AnalysisOutput
  analyze(const std::map<std::string, Document> &documents,
          std::stop_token token,
          const std::function<void(const noscheme::LspAnalysis &)> &ready) {
    noscheme::Sources sources;
    std::map<noscheme::fs::path, std::string> uris;
    std::map<std::string, Json> grouped;
    for (const auto &[uri, doc] : documents) {
      grouped[uri] = Json::array();
      if (auto path = noscheme::uri_path(uri)) {
        sources[*path] = doc.text;
        uris[*path] = uri;
      }
    }
    auto result =
        noscheme::analyze_documents(sources, project, python, cache, token,
                                    ready, workspace_roots, &standard_registry);
    // Non-file buffers still receive parser diagnostics, without project
    // loading.
    for (const auto &[uri, doc] : documents) {
      if (noscheme::uri_path(uri))
        continue;
      noscheme::check_cancelled(token);
      noscheme::Source source(doc.text, uri);
      result.sources[uri] = doc.text;
      try {
        noscheme::parse(source);
      } catch (const noscheme::Failure &e) {
        result.diagnostics.push_back(source.diagnostic(e));
      } catch (const noscheme::DiagnosticsFailure &e) {
        for (const auto &d : e.diagnostics)
          result.diagnostics.push_back(d);
      }
    }
    // Many warnings can refer to the same file. Build its UTF-8/line index
    // once.
    std::map<noscheme::fs::path, noscheme::Source> indexed_sources;
    auto location = [&](const Json &span) {
      noscheme::fs::path path = span.at("filename").get<std::string>();
      auto uri = uris.contains(path)                 ? uris.at(path)
                 : documents.contains(path.string()) ? path.string()
                                                     : noscheme::file_uri(path);
      auto indexed = indexed_sources.find(path);
      if (indexed == indexed_sources.end()) {
        auto found = result.sources.find(path);
        indexed =
            indexed_sources
                .try_emplace(path,
                             found == result.sources.end() ? "" : found->second,
                             path.string())
                .first;
      }
      const auto &source = indexed->second;
      return object("uri", uri, "range",
                    object("start", position(source, span.at("start_pos")),
                           "end", position(source, span.at("end_pos"))));
    };
    for (const auto &diagnostic : result.diagnostics) {
      auto where = location(diagnostic.at("span"));
      auto uri = where.at("uri").get<std::string>();
      if (!grouped.contains(uri))
        grouped[uri] = Json::array();
      auto item =
          object("range", where.at("range"), "severity",
                 diagnostic.value("severity", "error") == "error" ? 1 : 2,
                 "code", diagnostic.at("code"), "source", "noscheme-native",
                 "message", diagnostic.at("message"));
      Json related = Json::array();
      for (const auto &r : diagnostic.value("related", Json::array()))
        related.push_back(object("location", location(r.at("span")), "message",
                                 r.at("message")));
      if (!related.empty())
        item["relatedInformation"] = related;
      item["data"] =
          object("instance_path", diagnostic.value("instance_path", Json()),
                 "configuration", diagnostic.value("configuration", Json()),
                 "application", diagnostic.value("application", Json()));
      grouped[uri].push_back(std::move(item));
    }
    noscheme::check_cancelled(token);
    return {std::move(grouped), std::move(result)};
  }

public:
  Server(noscheme::fs::path project, noscheme::fs::path python)
      : project(std::move(project)), python(std::move(python)),
        worker([this] { work(); }) {}
  ~Server() {
    {
      std::lock_guard lock(mutex);
      stop();
    }
    worker.join();
  }
  // nullopt continues serving; exit returns the protocol's process status.
  std::optional<int> handle(const Json &message) {
    std::lock_guard lock(mutex);
    if (!message.is_object() || message.value("jsonrpc", Json()) != "2.0" ||
        !message.contains("method") || !message["method"].is_string() ||
        (message.contains("id") && !message["id"].is_string() &&
         !message["id"].is_number_integer())) {
      error(nullptr, -32600, "Invalid Request");
      return {};
    }
    bool request = message.contains("id");
    auto id = message.value("id", Json());
    auto method = message["method"].get<std::string>();
    if (method == "exit" && !request)
      return shutdown ? 0 : 1;
    if (method == "initialize" && request && !initialized) {
      initialized = true;
      auto params = message.value("params", Json::object());
      if (params.is_object()) {
        auto folders = params.value("workspaceFolders", Json());
        if (folders.is_array()) {
          for (const auto &folder : folders)
            if (folder.is_object() && folder.contains("uri") &&
                folder.at("uri").is_string())
              if (auto path = noscheme::uri_path(folder.at("uri")))
                workspace_roots.push_back(*path);
        } else if (params.contains("rootUri") &&
                   params.at("rootUri").is_string()) {
          if (auto path = noscheme::uri_path(params.at("rootUri")))
            workspace_roots.push_back(*path);
        }
        auto capabilities = params.value("capabilities", Json::object());
        if (capabilities.contains("workspace") &&
            capabilities["workspace"].is_object()) {
          auto workspace = capabilities["workspace"];
          if (workspace.contains("workspaceEdit") &&
              workspace["workspaceEdit"].is_object())
            versioned_edits =
                workspace["workspaceEdit"].value("documentChanges", false);
        }
        if (capabilities.contains("textDocument") &&
            capabilities["textDocument"].is_object()) {
          auto text = capabilities["textDocument"];
          if (text.contains("hover") && text["hover"].is_object())
            for (const auto &format :
                 text["hover"].value("contentFormat", Json::array()))
              if (format == "markdown")
                markdown = true;
        }
      }
      send(object(
          "id", id, "result",
          object(
              "capabilities",
              object("completionProvider",
                     object("triggerCharacters", Json::array({".", "\"", ":"})),
                     "definitionProvider", true, "referencesProvider", true,
                     "documentFormattingProvider", true, "renameProvider",
                     object("prepareProvider", true), "hoverProvider", true,
                     "positionEncoding", "utf-16", "textDocumentSync",
                     object("openClose", true, "change", 1, "save", true)),
              "serverInfo",
              object("name", "noscheme-native", "version", "0.1.0"))));
      return {};
    }
    if (!initialized || shutdown) {
      if (request)
        error(id, initialized ? -32600 : -32002,
              initialized ? "Server has shut down" : "Server not initialized");
      return {};
    }
    if (method == "shutdown" && request) {
      shutdown = true;
      stop();
      send(object("id", id, "result", nullptr));
      return {};
    }
    if (!request && method == "$/cancelRequest") {
      auto params = message.value("params", Json::object());
      if (params.is_object() && params.contains("id")) {
        std::erase_if(requests, [&](const Json &pending_request) {
          if (pending_request.at("id") != params["id"])
            return false;
          error(params["id"], -32800, "Request cancelled");
          return true;
        });
      }
      return {};
    }
    if (request && (method == "textDocument/definition" ||
                    method == "textDocument/prepareRename" ||
                    method == "textDocument/rename" ||
                    method == "textDocument/formatting" ||
                    method == "textDocument/references" ||
                    method == "textDocument/hover" ||
                    method == "textDocument/completion")) {
      try {
        const auto &params = message.at("params");
        auto uri = params.at("textDocument").at("uri").get<std::string>();
        if (!documents.contains(uri)) {
          send(object("id", id, "result", nullptr));
          return {};
        }
        noscheme::Source source(documents.at(uri).text, uri);
        if (method != "textDocument/formatting")
          byte_offset(source, params.at("position"));
        if (latest && !noscheme::inputs_current(*latest))
          schedule();
        if (latest)
          respond(message);
        else if (!analysis_failure.empty())
          error(id, -32603, analysis_failure);
        else if (requests.size() >= 128)
          error(id, -32603, "Too many pending requests");
        else {
          requests.push_back(message);
          changed.notify_one();
        }
      } catch (const std::exception &e) {
        error(id, -32602, e.what());
      }
      return {};
    }
    if (request) {
      error(id, -32601, "Method not found: " + method);
      return {};
    }
    if (method == "workspace/didChangeWatchedFiles" && !request) {
      schedule();
      return {};
    }
    if (method != "textDocument/didOpen" &&
        method != "textDocument/didChange" &&
        method != "textDocument/didClose" && method != "textDocument/didSave")
      return {};
    try {
      const auto &params = message.at("params");
      const auto &text_document = params.at("textDocument");
      auto uri = text_document.at("uri").get<std::string>();
      if (method == "textDocument/didSave") {
        schedule();
        return {};
      }
      if (method == "textDocument/didClose") {
        documents.erase(uri);
        send(object("method", "textDocument/publishDiagnostics", "params",
                    object("uri", uri, "diagnostics", Json::array())));
        published.erase(uri);
        schedule();
        return {};
      }
      if (!text_document.at("version").is_number_integer())
        throw std::runtime_error("document version must be an integer");
      auto version = text_document.at("version").get<std::int64_t>();
      std::string text;
      if (method == "textDocument/didOpen") {
        if (documents.contains(uri))
          throw std::runtime_error("document is already open");
        text = text_document.at("text").get<std::string>();
      } else {
        auto found = documents.find(uri);
        if (found == documents.end() || version <= found->second.version)
          return {};
        const auto &changes = params.at("contentChanges");
        if (!changes.is_array() || changes.empty())
          throw std::runtime_error("expected full document changes");
        for (const auto &change : changes) {
          if (change.contains("range"))
            throw std::runtime_error(
                "native LSP currently requires full document synchronization");
          text = change.at("text").get<std::string>();
        }
      }
      Document next{std::move(text), version};
      noscheme::Source validated(next.text, uri);
      documents.insert_or_assign(uri, std::move(next));
      schedule();
    } catch (const std::exception &failure) {
      // Invalid notifications and analysis failures have no JSON-RPC response.
      send(object("method", "window/logMessage", "params",
                  object("type", 1, "message", failure.what())));
    }
    return {};
  }
};
} // namespace
int main(int argc, char **argv) {
  noscheme::fs::path project, python = "python3";
  for (int i = 1; i < argc; ++i) {
    std::string argument = argv[i];
    if ((argument == "--project" || argument == "--python") && i + 1 < argc) {
      (argument == "--project" ? project : python) = argv[++i];
      continue;
    }
    if (argument == "--help") {
      std::cout << "noscheme-native-lsp [--project PATH] [--python "
                   "PATH]\nExperimental project "
                   "diagnostics over LSP stdio.\n";
      return 0;
    }
    std::cerr << "Unknown or incomplete option: " << argument << '\n';
    return 2;
  }
  try {
    Server server(project, python);
    while (auto body = receive()) {
      Json message;
      try {
        message =
            Json::parse(*body, [](int depth, Json::parse_event_t, Json &) {
              if (depth > 128)
                throw std::runtime_error("JSON nesting exceeds 128");
              return true;
            });
      } catch (const std::exception &) {
        error(nullptr, -32700, "Parse error");
        continue;
      }
      if (auto status = server.handle(message))
        return *status;
    }
    return 0;
  } catch (const std::exception &failure) {
    std::cerr << "noscheme-native-lsp: " << failure.what() << '\n';
    return 1;
  }
}
