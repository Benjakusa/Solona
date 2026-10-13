"""Billing / POS business logic: build a cart, compute totals, commit a sale
(insert sale + sale_items, deduct stock, track udhaar/credit) as one atomic
transaction, and process returns/refunds against a past sale."""
import sqlite3
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from database.db_manager import get_connection
from logic import inventory
from logic.refunds import refund_for_return
from logic.sync import enqueue_event, utc_now

VALID_PAYMENT_METHODS = ("cash", "card", "easypaisa", "jazzcash", "bank", "udhaar")

PAYMENT_METHOD_LABELS = {
    "cash": "💵 Cash",
    "card": "💳 Card",
    "easypaisa": "📱 EasyPaisa",
    "jazzcash": "📱 JazzCash",
    "bank": "🏦 Bank Transfer",
    "udhaar": "📒 Udhaar (Credit)",
}


@dataclass
class CartItem:
    medicine_id: int
    name: str
    unit_price: float
    quantity: int
    available_stock: int

    @property
    def subtotal(self) -> float:
        return round(self.unit_price * self.quantity, 2)


@dataclass
class Cart:
    items: list = field(default_factory=list)
    discount: float = 0.0
    tax_percent: float = 0.0

    def add(self, medicine: inventory.Medicine, quantity: int):
        if quantity <= 0:
            raise ValueError("Quantity must be positive.")
        available_stock = inventory.sellable_quantity(medicine.id)
        for item in self.items:
            if item.medicine_id == medicine.id:
                new_qty = item.quantity + quantity
                if new_qty > available_stock:
                    raise ValueError(
                        f"Only {available_stock} sellable unit(s) of '{medicine.name}' are available."
                    )
                item.quantity = new_qty
                item.available_stock = available_stock
                return
        if quantity > available_stock:
            raise ValueError(f"Only {available_stock} sellable unit(s) of '{medicine.name}' are available.")
        self.items.append(
            CartItem(
                medicine_id=medicine.id,
                name=medicine.name,
                unit_price=medicine.sale_price,
                quantity=quantity,
                available_stock=available_stock,
            )
        )

    def update_quantity(self, medicine_id: int, quantity: int):
        for item in self.items:
            if item.medicine_id == medicine_id:
                if quantity <= 0:
                    self.remove(medicine_id)
                    return
                if quantity > item.available_stock:
                    raise ValueError(f"Only {item.available_stock} unit(s) of '{item.name}' in stock.")
                item.quantity = quantity
                return

    def remove(self, medicine_id: int):
        self.items = [i for i in self.items if i.medicine_id != medicine_id]

    def clear(self):
        self.items = []
        self.discount = 0.0
        self.tax_percent = 0.0

    @property
    def subtotal(self) -> float:
        return round(sum(i.subtotal for i in self.items), 2)

    @property
    def tax_amount(self) -> float:
        return round((self.subtotal - self.discount) * (self.tax_percent / 100), 2)

    @property
    def total(self) -> float:
        return round(self.subtotal - self.discount + self.tax_amount, 2)


def _generate_invoice_no(conn) -> str:
    today = datetime.now().strftime("%Y%m%d")
    identity = conn.execute("SELECT device_id FROM sync_identity WHERE singleton=1").fetchone()
    device_id = identity["device_id"] if identity else "LOCAL"
    terminal = device_id.replace("-", "")[:6].upper()
    prefix = f"INV-{today}-{terminal}-"
    existing = conn.execute("SELECT invoice_no FROM sales WHERE invoice_no LIKE ?", (f"{prefix}%",)).fetchall()
    max_existing = 0
    for row in existing:
        try:
            max_existing = max(max_existing, int(row["invoice_no"].rsplit("-", 1)[1]))
        except (IndexError, ValueError):
            continue
    conn.execute(
        "INSERT INTO invoice_counters(device_id,business_date,last_value) VALUES(?,?,?) "
        "ON CONFLICT(device_id,business_date) DO NOTHING",
        (device_id, today, max_existing),
    )
    conn.execute(
        "UPDATE invoice_counters SET last_value=MAX(last_value,?)+1 "
        "WHERE device_id=? AND business_date=?",
        (max_existing, device_id, today),
    )
    seq = conn.execute(
        "SELECT last_value FROM invoice_counters WHERE device_id=? AND business_date=?",
        (device_id, today),
    ).fetchone()[0]
    return f"{prefix}{seq:04d}"


def checkout(
    cart: Cart,
    cashier_id: int,
    customer_id: int = None,
    payment_method: str = "cash",
    amount_paid: float = None,
    doctor_name: str = "",
) -> dict:
    """Commit the cart as a sale: creates sales + sale_items rows, deducts
    stock, and (if `amount_paid` is less than the total) adds the remainder
    to the selected customer's udhaar balance -- all inside one transaction.
    Returns a receipt dict for printing.

    `amount_paid=None` means "paid in full" (the whole total), except when
    payment_method is "udhaar", where it means "nothing paid now" (0) --
    matching what a cashier picking that method from a dropdown expects.
    """
    if not cart.items:
        raise ValueError("Cannot checkout an empty cart.")
    if cart.discount < 0:
        raise ValueError("Discount cannot be negative.")
    if cart.discount > cart.subtotal:
        raise ValueError("Discount cannot exceed the subtotal.")
    if payment_method not in VALID_PAYMENT_METHODS:
        raise ValueError(f"Unknown payment method '{payment_method}'.")

    total = cart.total
    if amount_paid is None:
        amount_paid = 0.0 if payment_method == "udhaar" else total
    if amount_paid < 0:
        raise ValueError("Amount paid cannot be negative.")
    if amount_paid > total + 0.005:
        raise ValueError("Amount paid cannot be more than the sale total.")

    credit_amount = round(total - amount_paid, 2)
    if credit_amount > 0.005 and customer_id is None:
        raise ValueError(
            "A walk-in sale must be paid in full -- select a customer to extend udhaar (credit)."
        )

    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        # Re-check stock against the live DB right before committing, in case
        # another sale happened concurrently.
        for item in cart.items:
            row = conn.execute(
                "SELECT quantity, name FROM medicines WHERE id=? AND is_active=1", (item.medicine_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"Medicine '{item.name}' no longer exists.")
            if row["quantity"] < item.quantity:
                raise ValueError(
                    f"Only {row['quantity']} unit(s) of '{row['name']}' available now."
                )

        # Two checkouts landing in the same instant (e.g. the app open in two
        # windows at once) could both compute the same next invoice number;
        # retry with a freshly regenerated one rather than losing the sale.
        max_attempts = 5
        for attempt in range(max_attempts):
            invoice_no = _generate_invoice_no(conn)
            try:
                transaction_id = str(uuid.uuid4())
                identity = conn.execute("SELECT * FROM sync_identity WHERE singleton=1").fetchone()
                cur = conn.execute(
                    "INSERT INTO sales (invoice_no, customer_id, cashier_id, total_amount, discount, "
                    "tax, payment_method, amount_paid, doctor_name,transaction_id,shop_id,device_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?,?,?,?)",
                    (
                        invoice_no, customer_id, cashier_id, total, cart.discount,
                        cart.tax_amount, payment_method, amount_paid, doctor_name.strip() or None,
                        transaction_id, identity["shop_id"], identity["device_id"],
                    ),
                )
                sale_id = cur.lastrowid
                break
            except sqlite3.IntegrityError:
                if attempt == max_attempts - 1:
                    raise
                continue

        sale_lines = []
        for item in cart.items:
            medicine_cloud_id = conn.execute(
                "SELECT cloud_uuid FROM medicines WHERE id=?", (item.medicine_id,)
            ).fetchone()["cloud_uuid"]
            medicine_cloud_id = conn.execute(
                "SELECT cloud_uuid FROM medicines WHERE id=?", (item.medicine_id,)
            ).fetchone()["cloud_uuid"]
            lots = conn.execute(
                "SELECT * FROM medicine_batches WHERE medicine_id=? AND quantity>0 "
                "AND expiry_date IS NOT NULL AND expiry_date!='' "
                "AND expiry_date>=date('now','localtime') "
                "ORDER BY expiry_date,created_at,id",
                (item.medicine_id,),
            ).fetchall()
            lot_total = sum(lot["quantity"] for lot in lots)
            if lot_total < item.quantity:
                raise ValueError(
                    f"Batch inventory for '{item.name}' is inconsistent; refresh inventory before selling."
                )
            left = item.quantity
            for lot in lots:
                if not left:
                    break
                allocated = min(left, lot["quantity"])
                subtotal = round(item.unit_price * allocated, 2)
                cloud_item_id = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO sale_items (sale_id, medicine_id, quantity, unit_price, subtotal,batch_id,unit_cost,cloud_uuid) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (sale_id, item.medicine_id, allocated, item.unit_price, subtotal, lot["id"], lot["purchase_price"], cloud_item_id),
                )
                conn.execute(
                    "UPDATE medicine_batches SET quantity=quantity-? WHERE id=? AND quantity>=?",
                    (allocated, lot["id"], allocated),
                )
                conn.execute(
                    "INSERT INTO stock_movements(id,transaction_id,medicine_id,batch_id,movement_type,quantity_delta,actor_id,occurred_at,details) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), transaction_id, item.medicine_id, lot["id"], "sale", -allocated,
                     cashier_id, utc_now(), json.dumps({"invoice_no": invoice_no})),
                )
                sale_lines.append({
                    "medicine_id": item.medicine_id, "name": item.name, "batch_id": lot["id"],
                    "cloud_medicine_id": medicine_cloud_id,
                    "cloud_batch_id": lot["cloud_uuid"] or lot["id"],
                    "cloud_sale_item_id": cloud_item_id,
                    "cloud_medicine_id": medicine_cloud_id,
                    "cloud_batch_id": lot["cloud_uuid"] or lot["id"],
                    "batch_no": lot["batch_no"], "expiry_date": lot["expiry_date"],
                    "quantity": allocated, "unit_price": item.unit_price,
                    "unit_cost": lot["purchase_price"], "subtotal": subtotal,
                })
                left -= allocated
            conn.execute(
                "UPDATE medicines SET quantity = quantity - ?, updated_at=datetime('now','localtime') "
                "WHERE id = ?",
                (item.quantity, item.medicine_id),
            )

        if credit_amount > 0.005:
            conn.execute(
                "UPDATE customers SET credit_balance = credit_balance + ? WHERE id = ?",
                (credit_amount, customer_id),
            )

        payment_id = str(uuid.uuid4())
        occurred_at = utc_now()
        cashier_row = conn.execute("SELECT cloud_uuid,username,full_name FROM users WHERE id=?", (cashier_id,)).fetchone()
        cashier_cloud_id = cashier_row["cloud_uuid"]
        customer_cloud_id = None
        if customer_id is not None:
            customer_cloud_id = conn.execute("SELECT cloud_uuid FROM customers WHERE id=?", (customer_id,)).fetchone()[0]
        cashier_cloud_id = conn.execute("SELECT cloud_uuid FROM users WHERE id=?", (cashier_id,)).fetchone()[0]
        customer_cloud_id = None
        if customer_id is not None:
            customer_cloud_id = conn.execute("SELECT cloud_uuid FROM customers WHERE id=?", (customer_id,)).fetchone()[0]
        conn.execute(
            "INSERT INTO local_payments(id,transaction_id,sale_id,amount,method,status,occurred_at) VALUES(?,?,?,?,?,'recorded',?)",
            (payment_id, str(uuid.uuid4()), sale_id, amount_paid, payment_method, occurred_at),
        )
        enqueue_event(conn, "sale.created", transaction_id,
                      {"sale_id": sale_id, "invoice_no": invoice_no, "customer_id": customer_id,
                       "cashier_id": cashier_id, "total_amount": total, "discount": cart.discount,
                       "cashier_cloud_id": cashier_cloud_id, "customer_cloud_id": customer_cloud_id,
                       "cashier_name": cashier_row["full_name"] or cashier_row["username"],
                       "cashier_cloud_id": cashier_cloud_id, "customer_cloud_id": customer_cloud_id,
                       "tax": cart.tax_amount, "payment_method": payment_method, "amount_paid": amount_paid,
                       "credit_amount": credit_amount, "payment_id": payment_id,
                       "doctor_name": doctor_name.strip() or None,
                       "lines": sale_lines, "status": "pending"}, transaction_id=transaction_id)

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "invoice_no": invoice_no,
        "sale_id": sale_id,
        "items": list(cart.items),
        "subtotal": cart.subtotal,
        "discount": cart.discount,
        "tax": cart.tax_amount,
        "total": total,
        "customer_id": customer_id,
        "cashier_id": cashier_id,
        "payment_method": payment_method,
        "amount_paid": amount_paid,
        "credit_amount": credit_amount,
        "doctor_name": doctor_name.strip(),
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def get_sale_with_items(sale_id: int) -> dict:
    conn = get_connection()
    try:
        sale = conn.execute(
            """
            SELECT s.*, c.name AS customer_name, u.username AS cashier_name
            FROM sales s
            LEFT JOIN customers c ON c.id = s.customer_id
            LEFT JOIN users u ON u.id = s.cashier_id
            WHERE s.id = ?
            """,
            (sale_id,),
        ).fetchone()
        if sale is None:
            return None
        items = conn.execute(
            """
            SELECT si.*, m.name AS medicine_name
            FROM sale_items si
            JOIN medicines m ON m.id = si.medicine_id
            WHERE si.sale_id = ?
            """,
            (sale_id,),
        ).fetchall()
        return {"sale": dict(sale), "items": [dict(i) for i in items]}
    finally:
        conn.close()


def process_return(sale_item_id: int, quantity: int, reason: str, processed_by: int) -> dict:
    """Return `quantity` units of one sale line item: restocks inventory and
    logs the refund. If the original sale still has an unpaid (udhaar)
    balance, the refund reduces that balance first rather than handing back
    cash that was never actually collected; any amount beyond what's still
    owed is a cash refund. Returns how much of each so the cashier knows
    what to actually hand back."""
    if quantity <= 0:
        raise ValueError("Return quantity must be positive.")
    if not reason or len(reason.strip()) > 300:
        raise ValueError("Enter a return reason of 1 to 300 characters.")

    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        item = conn.execute("SELECT * FROM sale_items WHERE id=?", (sale_item_id,)).fetchone()
        if item is None:
            raise ValueError("Sale item not found.")
        remaining = item["quantity"] - item["returned_qty"]
        if quantity > remaining:
            raise ValueError(f"Only {remaining} unit(s) can still be returned from this line.")

        sale = conn.execute("SELECT * FROM sales WHERE id=?", (item["sale_id"],)).fetchone()
        sale_lines = [dict(row) for row in conn.execute(
            "SELECT id,quantity,subtotal FROM sale_items WHERE sale_id=? ORDER BY id",
            (item["sale_id"],),
        ).fetchall()]
        refund_cents = refund_for_return(
            sale_lines, sale["discount"], sale["tax"], sale_item_id,
            quantity, item["returned_qty"],
        )
        refund_amount = refund_cents / 100

        conn.execute(
            "UPDATE sale_items SET returned_qty = returned_qty + ? WHERE id=?", (quantity, sale_item_id)
        )
        conn.execute(
            "UPDATE medicines SET quantity = quantity + ?, updated_at=datetime('now','localtime') WHERE id=?",
            (quantity, item["medicine_id"]),
        )
        batch_id = item["batch_id"]
        if not batch_id:
            lot = conn.execute(
                "SELECT id FROM medicine_batches WHERE medicine_id=? ORDER BY created_at,id LIMIT 1",
                (item["medicine_id"],),
            ).fetchone()
            batch_id = lot["id"] if lot else None
        if batch_id:
            conn.execute("UPDATE medicine_batches SET quantity=quantity+? WHERE id=?", (quantity, batch_id))
        tx_id = str(uuid.uuid4())
        occurred_at = utc_now()
        cashier_cloud_id = conn.execute("SELECT cloud_uuid FROM users WHERE id=?", (processed_by,)).fetchone()
        batch_cloud_id = conn.execute("SELECT cloud_uuid FROM medicine_batches WHERE id=?", (batch_id,)).fetchone() if batch_id else None
        conn.execute(
            "INSERT INTO stock_movements(id,transaction_id,medicine_id,batch_id,movement_type,quantity_delta,actor_id,occurred_at,details) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), tx_id, item["medicine_id"], batch_id, "return", quantity,
             processed_by, occurred_at, json.dumps({"sale_item_id": sale_item_id, "reason": reason.strip()})),
        )
        conn.execute(
            "INSERT INTO sale_returns (sale_id, sale_item_id, medicine_id, quantity, refund_amount, "
            "reason, processed_by,transaction_id,batch_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (item["sale_id"], sale_item_id, item["medicine_id"], quantity, refund_amount,
             reason.strip(), processed_by, tx_id, batch_id),
        )
        return_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        credit_reduction = 0.0
        if sale["customer_id"] is not None:
            outstanding = round(sale["total_amount"] - sale["amount_paid"], 2)
            if outstanding > 0.005:
                credit_reduction = min(refund_amount, outstanding)
                conn.execute(
                    "UPDATE customers SET credit_balance = credit_balance - ? WHERE id=?",
                    (credit_reduction, sale["customer_id"]),
                )
                conn.execute(
                    "UPDATE sales SET amount_paid = amount_paid + ? WHERE id=?",
                    (credit_reduction, sale["id"]),
                )

        all_items = conn.execute(
            "SELECT quantity, returned_qty FROM sale_items WHERE sale_id=?", (sale["id"],)
        ).fetchall()
        if all(r["quantity"] == r["returned_qty"] for r in all_items):
            conn.execute("UPDATE sales SET is_refunded=1 WHERE id=?", (sale["id"],))

        refund_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO local_payments(id,transaction_id,sale_id,amount,method,status,occurred_at) VALUES(?,?,?,?,?,'refund',?)",
            (refund_id, str(uuid.uuid4()), sale["id"], refund_amount, sale["payment_method"], occurred_at),
        )
        enqueue_event(conn, "sale.returned", tx_id,
                      {"return_id": return_id, "sale_id": sale["id"], "sale_item_id": sale_item_id,
                       "sale_transaction_id": sale["transaction_id"],
                       "cloud_sale_item_id": item["cloud_uuid"],
                       "cloud_batch_id": batch_cloud_id["cloud_uuid"] if batch_cloud_id else None,
                       "cashier_cloud_id": cashier_cloud_id["cloud_uuid"] if cashier_cloud_id else None,
                       "medicine_id": item["medicine_id"], "batch_id": batch_id,
                       "quantity": quantity, "refund_amount": refund_amount,
                       "credit_reduction": credit_reduction, "reason": reason.strip(),
                       "processed_by": processed_by}, transaction_id=tx_id)

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "refund_amount": refund_amount,
        "credit_reduction": credit_reduction,
        "cash_refund": round(refund_amount - credit_reduction, 2),
    }


def returnable_sales(search: str = "", limit: int = 100) -> list[dict]:
    """Recent sales that still have at least one unreturned unit, for the
    Returns screen to search against."""
    conn = get_connection()
    try:
        query = """
            SELECT s.id, s.invoice_no, s.date, s.total_amount,
                   COALESCE(c.name, 'Walk-in') AS customer_name
            FROM sales s
            LEFT JOIN customers c ON c.id = s.customer_id
            WHERE EXISTS (
                SELECT 1 FROM sale_items si WHERE si.sale_id = s.id AND si.quantity > si.returned_qty
            )
        """
        params: list = []
        if search:
            query += " AND s.invoice_no LIKE ?"
            params.append(f"%{search}%")
        query += " ORDER BY s.date DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
