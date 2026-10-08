#include "noscheme/source.hpp"
#include <algorithm>
namespace noscheme {
Source::Source(std::string input, std::string path)
    : text(std::move(input)), filename(std::move(path)) {
  characters.resize(text.size() + 1);
  std::size_t count = 0;
  for (std::size_t i = 0; i < text.size();) {
    offsets.push_back(i);
    auto c = static_cast<unsigned char>(text[i]);
    std::size_t length = c < 0x80                 ? 1
                         : c >= 0xc2 && c <= 0xdf ? 2
                         : c >= 0xe0 && c <= 0xef ? 3
                         : c >= 0xf0 && c <= 0xf4 ? 4
                                                  : 0;
    if (!length || i + length > text.size())
      throw Failure("syntax.encoding", "invalid UTF-8", i, i + 1);
    unsigned scalar = c & (length == 1   ? 0x7f
                           : length == 2 ? 0x1f
                           : length == 3 ? 0xf
                                         : 7);
    for (std::size_t j = 1; j < length; ++j) {
      auto next = static_cast<unsigned char>(text[i + j]);
      if ((next & 0xc0) != 0x80)
        throw Failure("syntax.encoding", "invalid UTF-8", i, i + length);
      scalar = (scalar << 6) | (next & 0x3f);
    }
    if ((length == 3 && scalar < 0x800) || (length == 4 && scalar < 0x10000) ||
        (scalar >= 0xd800 && scalar <= 0xdfff) || scalar > 0x10ffff)
      throw Failure("syntax.encoding", "invalid UTF-8", i, i + length);
    for (std::size_t j = 0; j < length; ++j)
      characters[i + j] = count;
    if (c == '\n')
      lines.push_back(i + 1);
    i += length;
    characters[i] = ++count;
  }
  offsets.push_back(text.size());
}
Json Source::span(std::size_t start, std::size_t end) const {
  start = std::min(start, text.size());
  end = std::min(end, text.size());
  auto line =
      std::upper_bound(lines.begin(), lines.end(), start) - lines.begin();
  auto last = std::upper_bound(lines.begin(), lines.end(), end) - lines.begin();
  return object(
      "filename", filename, "start_pos", start, "end_pos", end, "line", line,
      "column", characters[start] - characters[lines[line - 1]] + 1, "end_line",
      last, "end_column", characters[end] - characters[lines[last - 1]] + 1);
}
Json Source::diagnostic(const Failure &e) const {
  return object("code", e.code, "severity", "error", "message", e.what(),
                "span", span(e.start, e.end), "related", Json::array(),
                "instance_path", nullptr, "configuration", nullptr,
                "application", nullptr);
}
} // namespace noscheme
