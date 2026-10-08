#pragma once
#include <exception>
#include <stop_token>
namespace noscheme {
struct Cancelled : std::exception {
  const char *what() const noexcept override { return "analysis cancelled"; }
};
inline void check_cancelled(std::stop_token stop) {
  if (stop.stop_requested())
    throw Cancelled();
}
} // namespace noscheme
