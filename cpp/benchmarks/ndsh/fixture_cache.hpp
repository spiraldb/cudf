/*
 * SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * @file
 * @brief Reuse one scale factor's NDS-H fixtures across serial benchmark states.
 * Replacing a scale releases its files and reference data before generating the next fixture.
 */

#pragma once

#include <charconv>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <map>
#include <stdexcept>
#include <string_view>
#include <system_error>

namespace ndsh {

struct local_fixture_options {
  std::int32_t vortex_block_rows = 16 << 20;
};

inline local_fixture_options read_local_fixture_options()
{
  local_fixture_options options;
  if (auto const value = std::getenv("NDSH_VORTEX_BLOCK_ROWS")) {
    auto const text = std::string_view{value};
    if (text.empty() || text.find_first_not_of("0123456789") != std::string_view::npos) {
      throw std::invalid_argument("NDSH_VORTEX_BLOCK_ROWS must be a positive decimal row count");
    }
    auto const parsed =
      std::from_chars(text.data(), text.data() + text.size(), options.vortex_block_rows);
    if (parsed.ec != std::errc{} || parsed.ptr != text.data() + text.size() ||
        options.vortex_block_rows <= 0) {
      throw std::invalid_argument("NDSH_VORTEX_BLOCK_ROWS must fit a positive cudf::size_type");
    }
  }
  return options;
}

inline local_fixture_options const& local_fixture_config()
{
  auto const requested = read_local_fixture_options();
  // The scale-only cache is safe only while every fixture uses one process-wide configuration.
  static auto const fixed = [requested] {
    std::fprintf(stderr,
                 "[ndsh fixture] NDSH_VORTEX_BLOCK_ROWS=%d\n",
                 static_cast<int>(requested.vortex_block_rows));
    return requested;
  }();
  if (requested.vortex_block_rows != fixed.vortex_block_rows) {
    throw std::logic_error(
      "NDSH fixture options changed; use a separate process per configuration");
  }
  return fixed;
}

// Retain one scale per fixture type across serial benchmark states. The returned reference is
// invalidated by the next request for a different scale, even if replacement construction fails.
template <typename Files>
Files const& local_fixture(double scale_factor)
{
  (void)local_fixture_config();
  static std::map<double, Files> fixtures;
  if (!fixtures.contains(scale_factor)) {
    fixtures.clear();
    fixtures.try_emplace(scale_factor, scale_factor);
  }
  return fixtures.at(scale_factor);
}

}  // namespace ndsh
