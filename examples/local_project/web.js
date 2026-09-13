/** Small JavaScript half of the network-free tutorial fixture. */
export function visibleOrders(orders, includeArchived = false) {
  return orders.filter((order) => {
    if (includeArchived) return true;
    return order.status !== "archived";
  });
}

export function orderLabel(order) {
  const customer = order.customer || "Anonymous";
  return `${customer}: ${order.status || "unknown"}`;
}
