#include "noscheme/python_bridge.hpp"
#include "noscheme/validation.hpp"
#include <algorithm>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <cstring>
#include <fcntl.h>
#include <poll.h>
#include <set>
#include <spawn.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;
namespace noscheme {
namespace {
constexpr std::size_t response_limit = 16 * 1024 * 1024;
struct Descriptor {
  int fd = -1;
  ~Descriptor() {
    if (fd >= 0)
      close(fd);
  }
  void reset() {
    if (fd >= 0)
      close(fd);
    fd = -1;
  }
};
struct Child {
  pid_t pid = -1;
  ~Child() {
    if (pid > 0) {
      kill(-pid, SIGKILL);
      while (waitpid(pid, nullptr, 0) < 0 && errno == EINTR) {
      }
    }
  }
};
} // namespace
[[noreturn]] void bridge_error(const std::string &message, const Json &span,
                               const std::string &code) {
  throw DiagnosticsFailure(Json::array(
      {object("code", code, "severity", "error", "message", message, "span",
              span, "related", Json::array(), "instance_path", nullptr,
              "configuration", nullptr, "application", nullptr)}));
}
void validate_bridge_response(const Json &response, const Json &span,
                              const std::string &code) {
  auto require = [](bool condition, const char *message) {
    if (!condition)
      throw std::invalid_argument(message);
  };
  try {
    require(response.is_object(), "expected a bridge response object");
    require(response.at("version").is_number_integer() &&
                response.at("version") == 1,
            "unsupported bridge version");
    if (response.contains("diagnostics")) {
      const auto &diagnostics = response.at("diagnostics");
      require(diagnostics.is_array() && !diagnostics.empty(),
              "invalid bridge diagnostics");
      for (const auto &d : diagnostics)
        require(d.at("code").is_string() && d.at("message").is_string() &&
                    d.at("severity").is_string(),
                "invalid bridge diagnostic");
      throw DiagnosticsFailure(diagnostics);
    }
    if (response.contains("error"))
      bridge_error("Python bridge: " + response.at("error").get<std::string>(),
                   span, code);
  } catch (const DiagnosticsFailure &) {
    throw;
  } catch (const std::exception &e) {
    bridge_error("invalid bridge response: " + std::string(e.what()), span,
                 code);
  }
}
Json parse_bridge_json(const std::string &text) {
  auto bounded_depth = [](int depth, Json::parse_event_t, Json &) {
    if (depth > 128)
      throw std::invalid_argument("bridge response nesting exceeds limit");
    return true;
  };
  return Json::parse(text, bounded_depth);
}
Json python_request(const Json &payload, const fs::path &python,
                    const Json &span, std::chrono::milliseconds timeout,
                    const std::string &code, std::stop_token stop) {
  check_cancelled(stop);
  auto fail = [&](const std::string &message, const Json &where) {
    bridge_error(message, where, code);
  };
  auto request = payload.dump();
  if (request.size() > 64 * 1024 * 1024)
    fail("Python bridge request exceeds 64 MiB limit", span);
  if (timeout.count() <= 0)
    fail("bridge timeout must be positive", span);
  // A full-duplex socket allows nonblocking writes without process-wide SIGPIPE
  // changes, and avoids pipe deadlocks when a plugin writes a large response.
  int sockets[2];
  if (socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, sockets))
    fail(std::strerror(errno), span);
  Descriptor parent{sockets[0]}, child_socket{sockets[1]};
  // Keep both endpoints clear of stdio before installing the child's actions.
  for (auto *descriptor : {&parent, &child_socket}) {
    if (descriptor->fd > STDERR_FILENO)
      continue;
    int replacement = fcntl(descriptor->fd, F_DUPFD_CLOEXEC, STDERR_FILENO + 1);
    if (replacement < 0)
      fail(std::strerror(errno), span);
    descriptor->reset();
    descriptor->fd = replacement;
  }

  posix_spawn_file_actions_t actions;
  int status = posix_spawn_file_actions_init(&actions);
  if (status)
    fail(std::strerror(status), span);
  auto add = [&](int result) {
    if (!status)
      status = result;
  };
  add(posix_spawn_file_actions_adddup2(&actions, child_socket.fd,
                                       STDIN_FILENO));
  add(posix_spawn_file_actions_adddup2(&actions, child_socket.fd,
                                       STDOUT_FILENO));
  add(posix_spawn_file_actions_addclose(&actions, parent.fd));
  add(posix_spawn_file_actions_addclose(&actions, child_socket.fd));
  posix_spawnattr_t attributes;
  const auto attribute_status = posix_spawnattr_init(&attributes);
  if (attribute_status) {
    posix_spawn_file_actions_destroy(&actions);
    fail(std::strerror(attribute_status), span);
  }
  add(posix_spawnattr_setflags(&attributes, POSIX_SPAWN_SETPGROUP));
  add(posix_spawnattr_setpgroup(&attributes, 0));
  auto executable = python.string();
  char option[] = "-m", module[] = "noscheme.native_bridge";
  char *argv[] = {executable.data(), option, module, nullptr};
  Child child;
  pid_t spawned = -1;
  if (!status)
    status = posix_spawnp(&spawned, executable.c_str(), &actions, &attributes,
                          argv, environ);
  if (!status)
    child.pid = spawned;
  posix_spawnattr_destroy(&attributes);
  posix_spawn_file_actions_destroy(&actions);
  if (status)
    fail("cannot start Python bridge: " + std::string(std::strerror(status)),
         span);
  child_socket.reset();
  auto deadline = std::chrono::steady_clock::now() + timeout;
  std::size_t sent = 0;
  std::string response;
  bool eof = false, closed_write = false;
  while (true) {
    check_cancelled(stop);
    auto remaining = std::chrono::duration_cast<std::chrono::milliseconds>(
                         deadline - std::chrono::steady_clock::now())
                         .count();
    if (remaining <= 0)
      fail("Python bridge timed out", span);
    if (eof) {
      auto waited = waitpid(child.pid, &status, WNOHANG);
      if (waited == child.pid) {
        child.pid = -1;
        break;
      }
      if (waited < 0 && errno != EINTR)
        fail(std::strerror(errno), span);
      poll(nullptr, 0, static_cast<int>(std::min<std::int64_t>(remaining, 10)));
      continue;
    }
    pollfd descriptor{
        parent.fd,
        static_cast<short>(POLLIN | (sent < request.size() ? POLLOUT : 0)), 0};
    auto result = poll(&descriptor, 1,
                       static_cast<int>(std::min<std::int64_t>(remaining, 50)));
    if (result < 0) {
      if (errno == EINTR)
        continue;
      fail(std::strerror(errno), span);
    }
    if (descriptor.revents & POLLOUT) {
      auto count = send(parent.fd, request.data() + sent, request.size() - sent,
                        MSG_NOSIGNAL | MSG_DONTWAIT);
      if (count < 0 && (errno == EPIPE || errno == ECONNRESET)) {
        eof = true;
        continue;
      }
      if (count < 0 && errno != EAGAIN && errno != EINTR)
        fail("cannot write bridge request", span);
      if (count > 0)
        sent += static_cast<std::size_t>(count);
    }
    if (sent == request.size() && !closed_write) {
      shutdown(parent.fd, SHUT_WR);
      closed_write = true;
    }
    if (descriptor.revents & (POLLIN | POLLHUP | POLLERR)) {
      char buffer[8192];
      auto count = recv(parent.fd, buffer, sizeof(buffer), MSG_DONTWAIT);
      if (!count)
        eof = true;
      else if (count > 0) {
        response.append(buffer, static_cast<std::size_t>(count));
        if (response.size() > response_limit)
          fail("bridge response exceeds 16 MiB limit", span);
      } else if (errno == ECONNRESET) {
        // A child exiting without consuming stdin can reset its socket. Wait
        // for the exit status (or deadline) instead of losing that diagnosis.
        eof = true;
      } else if (errno != EAGAIN && errno != EINTR)
        fail("cannot read bridge response", span);
    }
  }
  if (!WIFEXITED(status) || WEXITSTATUS(status) != 0)
    fail("Python bridge exited unsuccessfully", span);
  try {
    return parse_bridge_json(response);
  } catch (const DiagnosticsFailure &) {
    throw;
  } catch (const std::exception &e) {
    bridge_error("cannot decode bridge response: " + std::string(e.what()),
                 span, code);
  }
}
} // namespace noscheme
