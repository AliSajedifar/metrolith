"""Small, deterministic Python half of the network-free tutorial fixture."""


def classify_order(total: float, item_count: int) -> str:
    if item_count <= 0:
        return "empty"
    if total >= 100 and item_count >= 3:
        return "large"
    if total >= 25:
        return "medium"
    return "small"


def summarize_orders(orders: list[dict[str, float]]) -> dict[str, float]:
    total = sum(float(order.get("total", 0)) for order in orders)
    return {"count": len(orders), "total": total}
