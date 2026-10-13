"""Supplier business logic: basic CRUD, purchase-order stock intake, and
accounts payable (the mirror image of customer udhaar -- what the shop
currently owes each supplier, and payments made against it)."""
from dataclasses import dataclass
import json
import uuid
from typing import Optional

from database.db_manager import get_connection
from logic.sync import enqueue_event, utc_now


@dataclass
class Supplier:
    id: int
    name: str
    contact: str
    address: str
    payable_balance: float  # amount the shop currently owes this supplier

    @property
    def has_payable(self) -> bool:
        return self.payable_balance > 0.005  # guard against float dust


def _row_to_supplier(row) -> Supplier:
    return Supplier(
        id=row["id"], name=row["name"], contact=row["contact"] or "", address=row["address"] or "",
        payable_balance=row["payable_balance"],
    )


def list_suppliers() -> list[Supplier]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM suppliers ORDER BY name").fetchall()
        return [_row_to_supplier(r) for r in rows]
    finally:
        conn.close()


def list_suppliers_with_payable() -> list[Supplier]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM suppliers WHERE payable_balance > 0.005 ORDER BY payable_balance DESC"
        ).fetchall()
        return [_row_to_supplier(r) for r in rows]
    finally:
        conn.close()


def get_supplier(supplier_id: int) -> Optional[Supplier]:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM suppliers WHERE id=?", (supplier_id,)).fetchone()
        return _row_to_supplier(row) if row else None
    finally:
        conn.close()


def add_supplier(name: str, contact: str, address: str) -> Supplier:
    if not name or not name.strip():
        raise ValueError("Supplier name is required.")
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO suppliers (name, contact, address) VALUES (?, ?, ?)",
            (name.strip(), contact.strip(), address.strip()),
        )
        conn.commit()
        new_id = cur.lastrowid
    finally:
        conn.close()
    return get_supplier(new_id)


def update_supplier(supplier_id: int, name: str, contact: str, address: str) -> Supplier:
    if not name or not name.strip():
        raise ValueError("Supplier name is required.")
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE suppliers SET name=?, contact=?, address=? WHERE id=?",
            (name.strip(), contact.strip(), address.strip(), supplier_id),
        )
        conn.commit()
    finally:
        conn.close()
    return get_supplier(supplier_id)


def delete_supplier(supplier_id: int) -> None:
    conn = get_connection()
    try:
        in_use = conn.execute(
            "SELECT COUNT(*) AS c FROM medicines WHERE supplier_id=? AND is_active=1", (supplier_id,)
        ).fetchone()["c"]
        if in_use:
            raise ValueError(
                f"Cannot delete: {in_use} medicine(s) still reference this supplier."
            )
        row = conn.execute("SELECT payable_balance FROM suppliers WHERE id=?", (supplier_id,)).fetchone()
        if row and row["payable_balance"] > 0.005:
            raise ValueError(
                f"Cannot delete: the shop still owes {row['payable_balance']:.2f} to this supplier. "
                "Record the payment first."
            )
        # supplier_payments.supplier_id has no ON DELETE CASCADE on purpose
        # (see the customer-payment-history bug fix) -- block deletion here
        # instead of letting that history be destroyed.
        has_payments = conn.execute(
            "SELECT COUNT(*) AS c FROM supplier_payments WHERE supplier_id=?", (supplier_id,)
        ).fetchone()["c"]
        if has_payments:
            raise ValueError(
                "Cannot delete: this supplier has recorded payments, and deleting "
                "them would permanently erase that payment history."
            )
        conn.execute("DELETE FROM suppliers WHERE id=?", (supplier_id,))
        conn.commit()
    finally:
        conn.close()


def record_purchase_order(
    medicine_id: int,
    quantity: int,
    new_batch_no: str,
    new_expiry_date: str,
    new_purchase_price: Optional[float] = None,
    amount_paid: Optional[float] = None,
    new_sale_price: Optional[float] = None,
    recorded_by: Optional[int] = None,
) -> dict:
    """Receive new stock from a supplier: bump quantity and update batch/expiry,
    and (if the medicine has a supplier assigned) track what's still owed to
    them as accounts payable -- the mirror of customer udhaar.

    Each receipt is recorded against a distinct batch. Legacy medicine-level
    quantity and batch fields remain as compatibility summaries for screens.

    `amount_paid=None` means "paid in full" (the whole cost of this order),
    matching sales.checkout()'s convention.
    """
    if quantity <= 0:
        raise ValueError("Received quantity must be positive.")

    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT quantity, purchase_price, sale_price, supplier_id FROM medicines WHERE id=?", (medicine_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Medicine not found.")

        unit_cost = new_purchase_price if new_purchase_price is not None else row["purchase_price"]
        selling_price = new_sale_price if new_sale_price is not None else row["sale_price"]
        total_cost = round(unit_cost * quantity, 2)
        if amount_paid is None:
            amount_paid = total_cost
        if amount_paid < 0:
            raise ValueError("Amount paid cannot be negative.")
        if amount_paid > total_cost + 0.005:
            raise ValueError("Amount paid cannot be more than the order's total cost.")

        payable_added = round(total_cost - amount_paid, 2)
        if payable_added > 0.005 and row["supplier_id"] is None:
            raise ValueError(
                "This medicine has no supplier assigned, so a partial/credit payment can't be "
                "tracked as payable. Set a supplier on the medicine first, or pay in full."
            )

        batch_no = new_batch_no.strip()
        if not batch_no:
            raise ValueError("Batch number is required for a stock receipt.")
        lot = conn.execute(
            "SELECT * FROM medicine_batches WHERE medicine_id=? AND batch_no=?", (medicine_id, batch_no)
        ).fetchone()
        if lot and (lot["expiry_date"] or "") != (new_expiry_date or ""):
            raise ValueError("This batch number already exists with a different expiry date.")
        if lot:
            previous = lot["quantity"]
            new_lot_qty = previous + quantity
            conn.execute(
                "UPDATE medicine_batches SET quantity=?,purchase_price=?,sale_price=? WHERE id=?",
                (new_lot_qty, round((lot["purchase_price"] * previous + unit_cost * quantity) / new_lot_qty, 2),
                 selling_price, lot["id"]),
            )
            batch_id = lot["id"]
        else:
            batch_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO medicine_batches(id,medicine_id,cloud_uuid,batch_no,expiry_date,quantity,purchase_price,sale_price,source) "
                "VALUES(?,?,?,?,?,?,?,?,'purchase')",
                (batch_id, medicine_id, batch_id, batch_no, new_expiry_date, quantity, unit_cost,
                 selling_price),
            )
        new_qty = row["quantity"] + quantity
        if new_purchase_price is not None:
            conn.execute(
                "UPDATE medicines SET quantity=?, batch_no=?, expiry_date=?, purchase_price=?, "
                "updated_at=datetime('now','localtime') WHERE id=?",
                (new_qty, batch_no, new_expiry_date, new_purchase_price, medicine_id),
            )
        else:
            conn.execute(
                "UPDATE medicines SET quantity=?, batch_no=?, expiry_date=?, "
                "updated_at=datetime('now','localtime') WHERE id=?",
                (new_qty, batch_no, new_expiry_date, medicine_id),
            )
        if new_sale_price is not None:
            if new_sale_price < 0:
                raise ValueError("Selling price cannot be negative.")
            conn.execute("UPDATE medicines SET sale_price=? WHERE id=?", (new_sale_price, medicine_id))
            user = conn.execute("SELECT username FROM users WHERE id=?", (recorded_by,)).fetchone() if recorded_by else None
            conn.execute(
                "INSERT INTO audit_log(user_id,username,action,details) VALUES(?,?,?,?)",
                (recorded_by, user["username"] if user else "(system)",
                 "medicine.sale_price_updated", json.dumps({"medicine_id": medicine_id,
                 "previous_price": row["sale_price"], "new_price": new_sale_price,
                 "purchase_batch": batch_no})),
            )

        transaction_id = str(uuid.uuid4())
        occurred_at = utc_now()
        purchase_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO local_purchases(id,transaction_id,supplier_id,total_amount,amount_paid,status,occurred_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (purchase_id, transaction_id, row["supplier_id"], total_cost, amount_paid,
             "paid" if amount_paid >= total_cost - 0.005 else "partially_paid", occurred_at),
        )
        conn.execute(
            "INSERT INTO local_purchase_items(id,purchase_id,medicine_id,batch_id,quantity,purchase_cost,selling_price) "
            "VALUES(?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), purchase_id, medicine_id, batch_id, quantity, unit_cost,
             selling_price),
        )
        conn.execute(
            "INSERT INTO local_payments(id,transaction_id,purchase_id,amount,method,status,occurred_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), str(uuid.uuid4()), purchase_id, amount_paid, "cash",
             "recorded" if amount_paid > 0 else "unpaid", occurred_at),
        )
        conn.execute(
            "INSERT INTO stock_movements(id,transaction_id,medicine_id,batch_id,movement_type,quantity_delta,actor_id,occurred_at,details) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), transaction_id, medicine_id, batch_id, "purchase_receipt", quantity,
             recorded_by, occurred_at, json.dumps({"supplier_id": row["supplier_id"], "unit_cost": unit_cost})),
        )

        if payable_added > 0.005:
            conn.execute(
                "UPDATE suppliers SET payable_balance = payable_balance + ? WHERE id=?",
                (payable_added, row["supplier_id"]),
            )

        cloud_medicine_id = conn.execute("SELECT cloud_uuid FROM medicines WHERE id=?", (medicine_id,)).fetchone()[0]
        cloud_supplier_id = conn.execute("SELECT cloud_uuid FROM suppliers WHERE id=?", (row["supplier_id"],)).fetchone()[0] if row["supplier_id"] else None
        cloud_batch_id = conn.execute("SELECT cloud_uuid FROM medicine_batches WHERE id=?", (batch_id,)).fetchone()[0]
        cloud_actor = conn.execute("SELECT cloud_uuid FROM users WHERE id=?", (recorded_by,)).fetchone()[0] if recorded_by else None
        enqueue_event(conn, "purchase.received", transaction_id,
                      {"purchase_id": purchase_id, "medicine_id": medicine_id,
                       "cloud_medicine_id": cloud_medicine_id, "cloud_supplier_id": cloud_supplier_id,
                       "cloud_batch_id": cloud_batch_id,
                       "cashier_cloud_id": cloud_actor,
                       "supplier_id": row["supplier_id"], "batch_id": batch_id,
                       "batch_no": batch_no, "expiry_date": new_expiry_date, "quantity": quantity,
                       "purchase_cost": unit_cost, "selling_price": selling_price, "total_cost": total_cost,
                       "update_selling_price": new_sale_price is not None,
                       "amount_paid": amount_paid, "payable_added": max(payable_added, 0.0),
                       "actor_id": recorded_by}, transaction_id=transaction_id)

        conn.commit()
    finally:
        conn.close()

    return {"total_cost": total_cost, "amount_paid": amount_paid, "payable_added": max(payable_added, 0.0)}


def payment_history(supplier_id: int) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, amount, payment_method, note, date FROM supplier_payments "
            "WHERE supplier_id=? ORDER BY date DESC",
            (supplier_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def record_payment(supplier_id: int, amount: float, payment_method: str, recorded_by: int, note: str = "") -> None:
    """Record the shop paying down some or all of what it owes a supplier."""
    if amount <= 0:
        raise ValueError("Payment amount must be positive.")
    conn = get_connection()
    try:
        row = conn.execute("SELECT payable_balance FROM suppliers WHERE id=?", (supplier_id,)).fetchone()
        if row is None:
            raise ValueError("Supplier not found.")
        if amount > row["payable_balance"] + 0.005:
            raise ValueError(
                f"Payment ({amount:.2f}) is more than the outstanding payable ({row['payable_balance']:.2f})."
            )
        conn.execute(
            "INSERT INTO supplier_payments (supplier_id, amount, payment_method, note, recorded_by) "
            "VALUES (?, ?, ?, ?, ?)",
            (supplier_id, amount, payment_method, note.strip(), recorded_by),
        )
        conn.execute(
            "UPDATE suppliers SET payable_balance = payable_balance - ? WHERE id=?",
            (amount, supplier_id),
        )
        conn.commit()
    finally:
        conn.close()
