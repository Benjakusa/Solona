"""Customer business logic, including udhaar (credit) balance tracking."""
from dataclasses import dataclass
from typing import Optional

from database.db_manager import get_connection


@dataclass
class Customer:
    id: int
    name: str
    phone: str
    credit_balance: float  # amount this customer currently owes the shop

    @property
    def has_credit(self) -> bool:
        return self.credit_balance > 0.005  # guard against float dust


def _row_to_customer(row) -> Customer:
    return Customer(
        id=row["id"],
        name=row["name"],
        phone=row["phone"] or "",
        credit_balance=row["credit_balance"],
    )


def list_customers(search: str = "") -> list[Customer]:
    conn = get_connection()
    try:
        if search:
            rows = conn.execute(
                "SELECT * FROM customers WHERE name LIKE ? OR phone LIKE ? ORDER BY name",
                (f"%{search}%", f"%{search}%"),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM customers ORDER BY name").fetchall()
        return [_row_to_customer(r) for r in rows]
    finally:
        conn.close()


def list_customers_with_debt() -> list[Customer]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM customers WHERE credit_balance > 0.005 ORDER BY credit_balance DESC"
        ).fetchall()
        return [_row_to_customer(r) for r in rows]
    finally:
        conn.close()


def get_customer(customer_id: int) -> Optional[Customer]:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
        return _row_to_customer(row) if row else None
    finally:
        conn.close()


def add_customer(name: str, phone: str) -> Customer:
    if not name or not name.strip():
        raise ValueError("Customer name is required.")
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO customers (name, phone) VALUES (?, ?)", (name.strip(), phone.strip())
        )
        conn.commit()
        new_id = cur.lastrowid
    finally:
        conn.close()
    return get_customer(new_id)


def update_customer(customer_id: int, name: str, phone: str) -> Customer:
    if not name or not name.strip():
        raise ValueError("Customer name is required.")
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE customers SET name=?, phone=? WHERE id=?", (name.strip(), phone.strip(), customer_id)
        )
        conn.commit()
    finally:
        conn.close()
    return get_customer(customer_id)


def delete_customer(customer_id: int) -> None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT credit_balance FROM customers WHERE id=?", (customer_id,)).fetchone()
        if row and row["credit_balance"] > 0.005:
            raise ValueError(
                f"Cannot delete: this customer still owes {row['credit_balance']:.2f} in udhaar. "
                "Record their payment first."
            )
        # customer_payments.customer_id is ON DELETE CASCADE, so deleting a
        # customer who has ever made an udhaar payment -- even one who has
        # since paid off their whole balance -- would otherwise silently
        # erase that payment history along with them, despite the delete
        # confirmation only warning about sales being unlinked.
        has_payments = conn.execute(
            "SELECT COUNT(*) AS c FROM customer_payments WHERE customer_id=?", (customer_id,)
        ).fetchone()["c"]
        if has_payments:
            raise ValueError(
                "Cannot delete: this customer has recorded udhaar payments, and deleting "
                "them would permanently erase that payment history."
            )
        conn.execute("UPDATE sales SET customer_id = NULL WHERE customer_id = ?", (customer_id,))
        conn.execute("DELETE FROM customers WHERE id=?", (customer_id,))
        conn.commit()
    finally:
        conn.close()


def sales_history(customer_id: int) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, invoice_no, date, total_amount, amount_paid, payment_method "
            "FROM sales WHERE customer_id=? ORDER BY date DESC",
            (customer_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def payment_history(customer_id: int) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, amount, payment_method, note, date FROM customer_payments "
            "WHERE customer_id=? ORDER BY date DESC",
            (customer_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def record_payment(customer_id: int, amount: float, payment_method: str, recorded_by: int, note: str = "") -> None:
    """Record a customer paying down their udhaar balance."""
    if amount <= 0:
        raise ValueError("Payment amount must be positive.")
    conn = get_connection()
    try:
        row = conn.execute("SELECT credit_balance FROM customers WHERE id=?", (customer_id,)).fetchone()
        if row is None:
            raise ValueError("Customer not found.")
        if amount > row["credit_balance"] + 0.005:
            raise ValueError(
                f"Payment ({amount:.2f}) is more than the outstanding balance ({row['credit_balance']:.2f})."
            )
        conn.execute(
            "INSERT INTO customer_payments (customer_id, amount, payment_method, note, recorded_by) "
            "VALUES (?, ?, ?, ?, ?)",
            (customer_id, amount, payment_method, note.strip(), recorded_by),
        )
        conn.execute(
            "UPDATE customers SET credit_balance = credit_balance - ? WHERE id=?",
            (amount, customer_id),
        )
        conn.commit()
    finally:
        conn.close()
