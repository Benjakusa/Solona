"""Reporting business logic: daily/monthly sales, profit/loss, expiry-wise stock.

Profit/loss note: sale_items stores the sale price at time of sale but not a
historical cost price, so profit is computed using each medicine's *current*
purchase_price. This matches the fixed schema in the spec; if precise historical
costing is ever needed, sale_items would need its own cost_price column.
"""
from datetime import date, timedelta

from database.db_manager import get_connection
from logic.refunds import line_net_cents, refund_for_return


def daily_sales_report(report_date: str) -> dict:
    """report_date: 'YYYY-MM-DD'."""
    conn = get_connection()
    try:
        sales = conn.execute(
            """
            SELECT s.id, s.invoice_no, s.date,
                   MAX(0,s.total_amount-COALESCE((SELECT SUM(r.refund_amount) FROM sale_returns r WHERE r.sale_id=s.id),0)) AS total_amount,
                   s.discount, s.tax,
                   COALESCE(c.name, 'Walk-in') AS customer_name, u.username AS cashier_name
            FROM sales s
            LEFT JOIN customers c ON c.id = s.customer_id
            LEFT JOIN users u ON u.id = s.cashier_id
            WHERE date(s.date) = ?
            ORDER BY s.date
            """,
            (report_date,),
        ).fetchall()
        rows = [dict(r) for r in sales]
        totals = {
            "count": len(rows),
            "total_amount": round(sum(r["total_amount"] for r in rows), 2),
            "total_discount": round(sum(r["discount"] for r in rows), 2),
            "total_tax": round(sum(r["tax"] for r in rows), 2),
        }
        returns = conn.execute(
            "SELECT COUNT(*) AS count,COALESCE(SUM(refund_amount),0) AS amount "
            "FROM sale_returns WHERE date(date)=?", (report_date,),
        ).fetchone()
        return {"date": report_date, "sales": rows, "totals": totals,
                "returns": {"count": returns["count"], "amount": round(returns["amount"], 2)},
                "cash_drawer": _cash_drawer_summary(conn, report_date, report_date)}
    finally:
        conn.close()


def monthly_sales_report(year: int, month: int) -> dict:
    month_str = f"{year:04d}-{month:02d}"
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT date(s.date) AS day, COUNT(*) AS count,
                   SUM(MAX(0,s.total_amount-COALESCE((SELECT SUM(r.refund_amount) FROM sale_returns r WHERE r.sale_id=s.id),0))) AS total,
                   SUM(s.discount) AS discount, SUM(s.tax) AS tax
            FROM sales s
            WHERE strftime('%Y-%m', date) = ?
            GROUP BY date(date)
            ORDER BY day
            """,
            (month_str,),
        ).fetchall()
        daily_breakdown = [dict(r) for r in rows]
        totals = {
            "count": sum(r["count"] for r in daily_breakdown),
            "total_amount": round(sum(r["total"] or 0 for r in daily_breakdown), 2),
            "total_discount": round(sum(r["discount"] or 0 for r in daily_breakdown), 2),
            "total_tax": round(sum(r["tax"] or 0 for r in daily_breakdown), 2),
        }
        returns = conn.execute(
            "SELECT COUNT(*) AS count,COALESCE(SUM(refund_amount),0) AS amount "
            "FROM sale_returns WHERE strftime('%Y-%m',date)=?", (month_str,),
        ).fetchone()
        start = f"{month_str}-01"
        end = (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)).isoformat()
        return {"month": month_str, "daily_breakdown": daily_breakdown, "totals": totals,
                "returns": {"count": returns["count"], "amount": round(returns["amount"], 2)},
                "cash_drawer": _cash_drawer_summary(conn, start, end)}
    finally:
        conn.close()


def profit_loss_summary(start_date: str, end_date: str) -> dict:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT s.id AS sale_id,s.discount,s.tax,si.id,si.quantity,si.returned_qty,si.subtotal,"
            "si.unit_cost,m.name FROM sale_items si JOIN sales s ON s.id=si.sale_id "
            "JOIN medicines m ON m.id=si.medicine_id "
            "WHERE date(s.date) BETWEEN ? AND ? ORDER BY s.id,si.id",
            (start_date, end_date),
        ).fetchall()

        grouped: dict[int, list[dict]] = {}
        for row in rows:
            grouped.setdefault(row["sale_id"], []).append(dict(row))
        revenue_cents = 0
        cost_cents = 0
        per_medicine: dict[str, dict] = {}
        for sale_lines in grouped.values():
            line_snapshots = [{"id": row["id"], "quantity": row["quantity"], "subtotal": row["subtotal"]}
                              for row in sale_lines]
            line_totals = line_net_cents(line_snapshots, sale_lines[0]["discount"], sale_lines[0]["tax"])
            for row in sale_lines:
                returned = refund_for_return(line_snapshots, sale_lines[0]["discount"],
                                             sale_lines[0]["tax"], row["id"],
                                             row["returned_qty"], 0)
                remaining_cents = max(0, line_totals[row["id"]] - returned)
                remaining_qty = row["quantity"] - row["returned_qty"]
                item_cost_cents = round(row["unit_cost"] * 100) * remaining_qty
                revenue_cents += remaining_cents
                cost_cents += item_cost_cents
                entry = per_medicine.setdefault(row["name"], {"quantity": 0, "revenue_cents": 0, "cost_cents": 0})
                entry["quantity"] += remaining_qty
                entry["revenue_cents"] += remaining_cents
                entry["cost_cents"] += item_cost_cents

        revenue = revenue_cents / 100
        cost = cost_cents / 100
        profit = round(revenue - cost, 2)

        breakdown = [
            {"medicine": name, "quantity": v["quantity"], "revenue": v["revenue_cents"] / 100,
             "cost": v["cost_cents"] / 100,
             "profit": (v["revenue_cents"] - v["cost_cents"]) / 100}
            for name, v in sorted(per_medicine.items())
        ]

        returns = conn.execute(
            "SELECT COUNT(*) AS count,COALESCE(SUM(r.refund_amount),0) AS amount "
            "FROM sale_returns r JOIN sales s ON s.id=r.sale_id WHERE date(s.date) BETWEEN ? AND ?",
            (start_date, end_date),
        ).fetchone()

        return {
            "start_date": start_date,
            "end_date": end_date,
            "revenue": revenue,
            "cost": cost,
            "profit": profit,
            "breakdown": breakdown,
            "returns": {"count": returns["count"], "amount": round(returns["amount"], 2)},
        }
    finally:
        conn.close()


def _cash_drawer_summary(conn, start_date: str, end_date: str) -> dict[str, float]:
    methods: dict[str, int] = {}
    payments = conn.execute(
        "SELECT method,status,COALESCE(SUM(amount),0) AS amount FROM local_payments "
        "WHERE date(occurred_at) BETWEEN ? AND ? GROUP BY method,status",
        (start_date, end_date),
    ).fetchall()
    for row in payments:
        cents = round(row["amount"] * 100)
        methods[row["method"]] = methods.get(row["method"], 0) + (cents if row["status"] != "refund" else -cents)
    customer_payments = conn.execute(
        "SELECT payment_method,COALESCE(SUM(amount),0) AS amount FROM customer_payments "
        "WHERE date(date) BETWEEN ? AND ? GROUP BY payment_method",
        (start_date, end_date),
    ).fetchall()
    for row in customer_payments:
        methods[row["payment_method"]] = methods.get(row["payment_method"], 0) + round(row["amount"] * 100)
    return {method: cents / 100 for method, cents in methods.items()}


def sales_trend(days: int = 7) -> list[dict]:
    """Total revenue per day for the last `days` days (including days with
    zero sales), oldest first -- for the Dashboard chart."""
    start = (date.today() - timedelta(days=days - 1)).isoformat()
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT date(date) AS day, SUM(total_amount) AS total FROM sales "
            "WHERE date(date) >= ? GROUP BY date(date)",
            (start,),
        ).fetchall()
        by_day = {r["day"]: r["total"] for r in rows}
    finally:
        conn.close()

    result = []
    for i in range(days):
        day = (date.today() - timedelta(days=days - 1 - i)).isoformat()
        result.append({"day": day, "total": round(by_day.get(day, 0) or 0, 2)})
    return result


def dead_stock_report(days: int = 90) -> list[dict]:
    """Medicines still in stock that haven't sold at all in the last `days`
    days (or ever) -- helps clear out slow-moving inventory before it
    expires unsold."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT m.id, m.name, m.category, m.quantity, m.expiry_date,
                   (SELECT MAX(s.date) FROM sale_items si
                    JOIN sales s ON s.id = si.sale_id
                    WHERE si.medicine_id = m.id) AS last_sold
            FROM medicines m
            WHERE m.is_active = 1 AND m.quantity > 0
              AND NOT EXISTS (
                  SELECT 1 FROM sale_items si
                  JOIN sales s ON s.id = si.sale_id
                  WHERE si.medicine_id = m.id AND date(s.date) >= ?
              )
            ORDER BY last_sold IS NOT NULL, last_sold ASC, m.name
            """,
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def best_sellers_report(start_date: str, end_date: str, limit: int = 20) -> list[dict]:
    """Top-selling medicines by quantity within a date range."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT m.name, m.category, SUM(si.quantity) AS quantity_sold,
                   SUM(si.subtotal) AS revenue, COUNT(DISTINCT si.sale_id) AS sale_count
            FROM sale_items si
            JOIN sales s ON s.id = si.sale_id
            JOIN medicines m ON m.id = si.medicine_id
            WHERE date(s.date) BETWEEN ? AND ?
            GROUP BY si.medicine_id
            ORDER BY quantity_sold DESC
            LIMIT ?
            """,
            (start_date, end_date, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def controlled_substances_report(start_date: str, end_date: str) -> list[dict]:
    """Register-style report of every sale of a narcotic/psychotropic
    medicine, for DRAP (SRO 808(I)/2001) documentation requirements."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT s.date, s.invoice_no, m.name AS medicine_name, m.batch_no,
                   si.quantity, si.unit_price, si.subtotal,
                   COALESCE(c.name, 'Walk-in') AS customer_name, COALESCE(c.phone, '') AS customer_phone,
                   u.username AS cashier_name, s.doctor_name
            FROM sale_items si
            JOIN sales s ON s.id = si.sale_id
            JOIN medicines m ON m.id = si.medicine_id
            LEFT JOIN customers c ON c.id = s.customer_id
            LEFT JOIN users u ON u.id = s.cashier_id
            WHERE m.is_controlled_substance = 1 AND date(s.date) BETWEEN ? AND ?
            ORDER BY s.date
            """,
            (start_date, end_date),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def customer_credit_report() -> list[dict]:
    """Every customer with an outstanding udhaar balance, most-owed first."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, name, phone, credit_balance FROM customers "
            "WHERE credit_balance > 0.005 ORDER BY credit_balance DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def supplier_payable_report() -> list[dict]:
    """Every supplier the shop currently owes money to, most-owed first --
    the mirror of customer_credit_report()."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, name, contact, payable_balance FROM suppliers "
            "WHERE payable_balance > 0.005 ORDER BY payable_balance DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def expiry_stock_report(within_days: int = 90) -> list[dict]:
    cutoff = (date.today() + timedelta(days=within_days)).isoformat()
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT m.name, m.batch_no, m.expiry_date, m.quantity, s.name AS supplier_name,
                   CAST(julianday(m.expiry_date) - julianday('now') AS INTEGER) AS days_to_expiry
            FROM medicines m
            LEFT JOIN suppliers s ON s.id = m.supplier_id
            WHERE m.is_active = 1 AND m.expiry_date IS NOT NULL AND m.expiry_date != ''
              AND m.expiry_date <= ?
            ORDER BY m.expiry_date
            """,
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
