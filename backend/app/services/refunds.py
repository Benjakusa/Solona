"""Cent-exact sale discount/tax allocation; kept in parity with desktop logic."""
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP


def _cents(value: object) -> int:
    amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int(amount * 100)


def _allocate(total: int, weights: list[int]) -> list[int]:
    if not weights:
        return []
    normalized = [max(0, int(weight)) for weight in weights]
    denominator = sum(normalized)
    if denominator == 0:
        normalized = [1] * len(weights)
        denominator = len(weights)
    exact = [Decimal(total) * weight / denominator for weight in normalized]
    allocated = [int(value.to_integral_value(rounding=ROUND_FLOOR)) for value in exact]
    remainder = total - sum(allocated)
    order = sorted(range(len(weights)), key=lambda index: (-(exact[index] - allocated[index]), index))
    for index in order[:remainder]:
        allocated[index] += 1
    return allocated


def refund_cents(lines: list[dict], discount: object, tax: object,
                 line_id, quantity: int, already_returned: int) -> int:
    """Calculate this return's share, preserving exact sale-level cents."""
    gross = [_cents(line["subtotal"]) for line in lines]
    discounts = _allocate(_cents(discount), gross)
    taxable = [max(0, value - cut) for value, cut in zip(gross, discounts)]
    taxes = _allocate(_cents(tax), taxable if sum(taxable) else gross)
    net_by_id = {
        line["id"]: value - cut + added_tax
        for line, value, cut, added_tax in zip(lines, gross, discounts, taxes)
    }
    line = next(value for value in lines if value["id"] == line_id)
    net = net_by_id[line_id]
    line_qty = int(line["quantity"])
    def prefix(count: int) -> int:
        base, extra = divmod(net, line_qty)
        return base * count + min(count, extra)
    return prefix(already_returned + quantity) - prefix(already_returned)
