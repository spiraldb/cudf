/*
 * SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * @file q05_query.hpp
 * @brief Shared Q5 execution for benchmarks and integration tests, computing revenue by
 * nation for Asian suppliers with caller-supplied input and result handling.
 */

#pragma once

#include "utilities.hpp"

#include <cudf/ast/expressions.hpp>
#include <cudf/binaryop.hpp>
#include <cudf/column/column.hpp>
#include <cudf/scalar/scalar.hpp>
#include <cudf/utilities/default_stream.hpp>
#include <cudf/utilities/memory_resource.hpp>

#include <algorithm>
#include <iterator>
#include <map>
#include <memory>
#include <string>
#include <vector>

namespace ndsh::q5 {

inline std::map<std::string, std::vector<std::string>> const q5_projections{
  {"orders", {"o_custkey", "o_orderkey", "o_orderdate"}},
  {"customer", {"c_custkey", "c_nationkey"}},
  {"lineitem", {"l_orderkey", "l_suppkey", "l_extendedprice", "l_discount"}},
  {"supplier", {"s_suppkey", "s_nationkey"}},
  {"nation", {"n_nationkey", "n_regionkey", "n_name"}},
  {"region", {"r_regionkey", "r_name"}}};

/**
 * @brief Calculate the revenue column
 *
 * @param extendedprice The extended price column
 * @param discount The discount column
 * @param stream The CUDA stream used for device memory operations and kernel launches.
 * @param mr Device memory resource used to allocate the returned column's device memory.
 */
[[nodiscard]] inline std::unique_ptr<cudf::column> calculate_revenue(
  cudf::column_view const& extendedprice,
  cudf::column_view const& discount,
  cuda::stream_ref stream           = cudf::get_default_stream(),
  rmm::device_async_resource_ref mr = cudf::get_current_device_resource_ref())
{
  auto const one = cudf::numeric_scalar<double>(1);
  auto const one_minus_discount =
    cudf::binary_operation(one, discount, cudf::binary_operator::SUB, discount.type(), stream, mr);
  auto const revenue_type = cudf::data_type{cudf::type_id::FLOAT64};
  auto revenue            = cudf::binary_operation(extendedprice,
                                        one_minus_discount->view(),
                                        cudf::binary_operator::MUL,
                                        revenue_type,
                                        stream,
                                        mr);
  return revenue;
}

[[nodiscard]] inline cudf::size_type q5_column(std::string const& table, std::string const& name)
{
  auto const& columns = q5_projections.at(table);
  return std::distance(columns.begin(), std::find(columns.begin(), columns.end(), name));
}

/**
 * Q5's table filters, shared by read pushdown and exact filtering. Column references index the
 * table's projection. AST nodes reference each other and the owned scalars, so instances stay
 * in place.
 */
struct q5_filters {
  q5_filters()                             = default;
  q5_filters(q5_filters const&)            = delete;
  q5_filters& operator=(q5_filters const&) = delete;

  /// The filter for `table`, or null when Q5 does not filter it.
  [[nodiscard]] cudf::ast::expression const* for_table(std::string const& table) const
  {
    if (table == "orders") { return orders.get(); }
    if (table == "region") { return region.get(); }
    return nullptr;
  }

  // o_orderdate >= date '1994-01-01' and o_orderdate < date '1995-01-01'
  cudf::ast::column_reference const orderdate{q5_column("orders", "o_orderdate")};
  cudf::timestamp_scalar<cudf::timestamp_D> orderdate_lower{days_since_epoch(1994, 1, 1), true};
  cudf::timestamp_scalar<cudf::timestamp_D> orderdate_upper{days_since_epoch(1995, 1, 1), true};
  cudf::ast::literal const orderdate_lower_literal{orderdate_lower};
  cudf::ast::literal const orderdate_upper_literal{orderdate_upper};
  cudf::ast::operation const orderdate_from{
    cudf::ast::ast_operator::GREATER_EQUAL, orderdate, orderdate_lower_literal};
  cudf::ast::operation const orderdate_until{
    cudf::ast::ast_operator::LESS, orderdate, orderdate_upper_literal};
  std::unique_ptr<cudf::ast::operation> const orders = std::make_unique<cudf::ast::operation>(
    cudf::ast::ast_operator::LOGICAL_AND, orderdate_from, orderdate_until);

  // r_name = 'ASIA'
  cudf::ast::column_reference const r_name{q5_column("region", "r_name")};
  cudf::string_scalar asia{"ASIA"};
  cudf::ast::literal const asia_literal{asia};
  std::unique_ptr<cudf::ast::operation> const region =
    std::make_unique<cudf::ast::operation>(cudf::ast::ast_operator::EQUAL, r_name, asia_literal);
};

/**
 * read returns owning projected tables, applying supplied predicates if filter_predicates is false.
 * Otherwise filtering happens here. consume receives the result owner by reference and may
 * move it out; its return value is forwarded. This helper adds no final stream synchronization.
 */
template <typename Read, typename Consume>
auto execute_q5(Read&& read, bool filter_predicates, Consume&& consume)
{
  q5_filters const filters;
  auto const& orders_cols = q5_projections.at("orders");
  auto const& region_cols = q5_projections.at("region");
  auto const& orders_pred = filters.orders;
  auto const& region_pred = filters.region;

  std::unique_ptr<cudf::ast::operation> const no_predicate;
  auto const customer = read("customer", q5_projections.at("customer"), no_predicate);
  auto orders         = read("orders", orders_cols, orders_pred);
  auto const lineitem = read("lineitem", q5_projections.at("lineitem"), no_predicate);
  auto const supplier = read("supplier", q5_projections.at("supplier"), no_predicate);
  auto const nation   = read("nation", q5_projections.at("nation"), no_predicate);
  auto region         = read("region", region_cols, region_pred);
  if (filter_predicates) {
    orders = apply_filter(orders, *orders_pred);
    region = apply_filter(region, *region_pred);
  }

  // Perform the joins
  auto const join_a = apply_inner_join(region, nation, {"r_regionkey"}, {"n_regionkey"});
  auto const join_b = apply_inner_join(join_a, customer, {"n_nationkey"}, {"c_nationkey"});
  auto const join_c = apply_inner_join(join_b, orders, {"c_custkey"}, {"o_custkey"});
  auto const join_d = apply_inner_join(join_c, lineitem, {"o_orderkey"}, {"l_orderkey"});
  auto joined_table =
    apply_inner_join(supplier, join_d, {"s_suppkey", "s_nationkey"}, {"l_suppkey", "n_nationkey"});

  // Calculate and append the `revenue` column
  auto revenue =
    calculate_revenue(joined_table->column("l_extendedprice"), joined_table->column("l_discount"));
  (*joined_table).append(revenue, "revenue");

  // Perform the groupby operation
  auto const groupedby_table =
    apply_groupby(joined_table,
                  groupby_context_t{{"n_name"},
                                    {
                                      {"revenue", {{cudf::aggregation::Kind::SUM, "revenue"}}},
                                    }});

  // Perform the order by operation
  auto orderedby_table = apply_orderby(groupedby_table, {"revenue"}, {cudf::order::DESCENDING});
  return consume(orderedby_table);
}

}  // namespace ndsh::q5
