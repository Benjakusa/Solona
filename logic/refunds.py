"""Cent-exact sale-level discount/tax allocation for line refunds."""
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
from typing import Sequence


def to_cents(value: object) -> int:
    amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int(amount * 100)


def allocate_cents(total: int, weights: Sequence[int]) -> list[int]:
    if not weights:
        return []
    normalized = [max(0, int(weight)) for weight in weights]
    weight_sum = sum(normalized)
    if weight_sum == 0:
        normalized = [1] * len(weights)
        weight_sum = len(weights)
    exact = [Decimal(total) * weight / weight_sum for weight in normalized]
    allocated = [int(value.to_integral_value(rounding=ROUND_FLOOR)) for value in exact]
    remainder = total - sum(allocated)
    order = sorted(range(len(weights)), key=lambda index: (-(exact[index] - allocated[index]), index))
    for index in order[:remainder]:
        allocated[index] += 1
    return allocated


def line_net_cents(lines: Sequence[dict], discount: object, tax: object) -> dict[int, int]:
    """Allocate a sale's discount and tax to its gross line totals."""
    gross = [to_cents(line["subtotal"]) for line in lines]
    discount_shares = allocate_cents(to_cents(discount), gross)
    taxable = [max(0, value - cut) for value, cut in zip(gross, discount_shares)]
    tax_shares = allocate_cents(to_cents(tax), taxable if sum(taxable) else gross)
    return {
        int(line["id"]): amount - cut + added_tax
        for line, amount, cut, added_tax in zip(lines, gross, discount_shares, tax_shares)
    }


def _unit_prefix_cents(line_total: int, line_quantity: int, count: int) -> int:
    base, extra = divmod(line_total, line_quantity)
    return base * count + min(count, extra)


def refund_for_return(
    lines: Sequence[dict], discount: object, tax: object,
    line_id: int, quantity: int, already_returned: int,
) -> int:
    line = next(line for line in lines if int(line["id"]) == line_id)
    net = line_net_cents(lines, discount, tax)[line_id]
    return (
        _unit_prefix_cents(net, int(line["quantity"]), already_returned + quantity)
        - _unit_prefix_cents(net, int(line["quantity"]), already_returned)
    )
