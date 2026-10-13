"""Inventory business logic: CRUD, search/filter, low-stock and expiry alerts."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import cached_property
import json
import uuid
from typing import Optional

from database.db_manager import get_connection
from logic.sync import enqueue_event, utc_now


def _cloud_actor_id(conn, actor_id):
    if actor_id is None:
        return None
    row = conn.execute("SELECT cloud_uuid FROM users WHERE id=?", (actor_id,)).fetchone()
    return row["cloud_uuid"] if row else None


@dataclass
class Medicine:
    id: int
    name: str
    generic_name: str
    manufacturer: str
    category: str
    barcode: str
    batch_no: str
    expiry_date: str  # ISO YYYY-MM-DD
    quantity: int  # always in the smallest sold unit (e.g. tablets)
    units_per_pack: int  # e.g. 10 tablets/strip -- a Billing quick-add convenience, not a second unit of account
    purchase_price: float
    sale_price: float
    supplier_id: Optional[int]
    supplier_name: Optional[str]
    low_stock_threshold: int
    is_controlled_substance: bool
    is_active: bool

    @property
    def is_low_stock(self) -> bool:
        return self.quantity <= self.low_stock_threshold

    @cached_property
    def days_to_expiry(self) -> Optional[int]:
        # Cached because this parses expiry_date with strptime, and with large
        # inventories this property is read multiple times per medicine while
        # rendering a single table (row coloring, alert counts, etc.) --
        # re-parsing every time made large inventories visibly slow to load.
        if not self.expiry_date:
            return None
        try:
            exp = datetime.strptime(self.expiry_date, "%Y-%m-%d").date()
        except ValueError:
            return None
        return (exp - date.today()).days

    @property
    def is_expired(self) -> bool:
        d = self.days_to_expiry
        return d is not None and d < 0


def _row_to_medicine(row) -> Medicine:
    return Medicine(
        id=row["id"],
        name=row["name"],
        generic_name=row["generic_name"] or "",
        manufacturer=row["manufacturer"] or "",
        category=row["category"] or "",
        barcode=row["barcode"] or "",
        batch_no=row["batch_no"] or "",
        expiry_date=row["expiry_date"] or "",
        quantity=row["quantity"],
        units_per_pack=row["units_per_pack"],
        purchase_price=row["purchase_price"],
        sale_price=row["sale_price"],
        supplier_id=row["supplier_id"],
        supplier_name=row["supplier_name"] if "supplier_name" in row.keys() else None,
        low_stock_threshold=row["low_stock_threshold"],
        is_controlled_substance=bool(row["is_controlled_substance"]),
        is_active=bool(row["is_active"]),
    )


_SELECT_BASE = """
    SELECT m.*, s.name AS supplier_name
    FROM medicines m
    LEFT JOIN suppliers s ON s.id = m.supplier_id
    WHERE m.is_active = 1
"""


def list_medicines(search: str = "", category: str = "", supplier_id: Optional[int] = None) -> list[Medicine]:
    """Search/filter medicines by name/generic name/manufacturer, category, and/or supplier."""
    query = _SELECT_BASE
    params: list = []

    if search:
        query += " AND (m.name LIKE ? OR m.generic_name LIKE ? OR m.barcode LIKE ? OR m.manufacturer LIKE ?)"
        like = f"%{search}%"
        params.extend([like, like, like, like])
    if category:
        query += " AND m.category = ?"
        params.append(category)
    if supplier_id:
        query += " AND m.supplier_id = ?"
        params.append(supplier_id)

    query += " ORDER BY m.name"

    conn = get_connection()
    try:
        rows = conn.execute(query, params).fetchall()
        return [_row_to_medicine(r) for r in rows]
    finally:
        conn.close()


def get_medicine(medicine_id: int) -> Optional[Medicine]:
    conn = get_connection()
    try:
        row = conn.execute(_SELECT_BASE + " AND m.id = ?", (medicine_id,)).fetchone()
        return _row_to_medicine(row) if row else None
    finally:
        conn.close()


def get_medicine_by_barcode(barcode: str) -> Optional[Medicine]:
    """Exact-match lookup for a scanned barcode (used by the Billing screen)."""
    barcode = barcode.strip()
    if not barcode:
        return None
    conn = get_connection()
    try:
        row = conn.execute(_SELECT_BASE + " AND m.barcode = ?", (barcode,)).fetchone()
        return _row_to_medicine(row) if row else None
    finally:
        conn.close()


def list_categories() -> list[str]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT DISTINCT category FROM medicines "
            "WHERE category IS NOT NULL AND category != '' AND is_active = 1 "
            "ORDER BY category"
        ).fetchall()
        return [r["category"] for r in rows]
    finally:
        conn.close()


def _validate(name: str, quantity, purchase_price, sale_price, expiry_date: str):
    if not name or not name.strip():
        raise ValueError("Medicine name is required.")
    if quantity is None or quantity < 0:
        raise ValueError("Quantity cannot be negative.")
    if purchase_price is None or purchase_price < 0:
        raise ValueError("Purchase price cannot be negative.")
    if sale_price is None or sale_price < 0:
        raise ValueError("Sale price cannot be negative.")
    if expiry_date:
        try:
            datetime.strptime(expiry_date, "%Y-%m-%d")
        except ValueError:
            raise ValueError("Expiry date must be in YYYY-MM-DD format.")


def _check_barcode_unique(conn, barcode: str, exclude_medicine_id: Optional[int] = None):
    barcode = barcode.strip()
    if not barcode:
        return
    query = "SELECT id FROM medicines WHERE barcode = ? AND is_active = 1"
    params = [barcode]
    if exclude_medicine_id is not None:
        query += " AND id != ?"
        params.append(exclude_medicine_id)
    existing = conn.execute(query, params).fetchone()
    if existing:
        raise ValueError(f"Barcode '{barcode}' is already assigned to another medicine.")


def add_medicine(
    name: str,
    generic_name: str,
    category: str,
    batch_no: str,
    expiry_date: str,
    quantity: int,
    purchase_price: float,
    sale_price: float,
    supplier_id: Optional[int],
    low_stock_threshold: int = 10,
    barcode: str = "",
    is_controlled_substance: bool = False,
    manufacturer: str = "",
    units_per_pack: int = 1,
    actor_id: Optional[int] = None,
) -> Medicine:
    _validate(name, quantity, purchase_price, sale_price, expiry_date)
    units_per_pack = max(1, units_per_pack)

    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _check_barcode_unique(conn, barcode)
        cur = conn.execute(
            """
            INSERT INTO medicines
                (name, generic_name, manufacturer, category, barcode, batch_no, expiry_date, quantity,
                 units_per_pack, purchase_price, sale_price, supplier_id, low_stock_threshold,
                 is_controlled_substance)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name.strip(), generic_name.strip(), manufacturer.strip(), category.strip(),
                barcode.strip() or None, batch_no.strip(), expiry_date or None, quantity,
                units_per_pack, purchase_price, sale_price, supplier_id, low_stock_threshold,
                int(is_controlled_substance),
            ),
        )
        new_id = cur.lastrowid
        batch_id = str(uuid.uuid4())
        batch_no = batch_no.strip() or f"OPENING-{new_id}"
        conn.execute(
            "INSERT INTO medicine_batches(id,medicine_id,cloud_uuid,batch_no,expiry_date,quantity,purchase_price,sale_price,source) "
            "VALUES(?,?,?,?,?,?,?,?,'catalogue_opening')",
            (batch_id, new_id, batch_id, batch_no, expiry_date or None, quantity, purchase_price, sale_price),
        )
        transaction_id = str(uuid.uuid4())
        if quantity:
            conn.execute(
                "INSERT INTO stock_movements(id,transaction_id,medicine_id,batch_id,movement_type,quantity_delta,occurred_at,details) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), transaction_id, new_id, batch_id, "opening", quantity, utc_now(), "{}"),
            )
        medicine_cloud_id = str(uuid.uuid4())
        conn.execute("UPDATE medicines SET cloud_uuid=? WHERE id=?", (medicine_cloud_id, new_id))
        enqueue_event(conn, "medicine.created", str(new_id),
                      {"medicine_id": new_id, "cloud_medicine_id": medicine_cloud_id,
                       "cloud_batch_id": batch_id, "name": name.strip(), "generic_name": generic_name.strip(),
                       "cashier_cloud_id": _cloud_actor_id(conn, actor_id),
                       "sku": barcode.strip() or f"local-{new_id}",
                       "manufacturer": manufacturer.strip(), "category": category.strip(), "barcode": barcode.strip(),
                       "batch_id": batch_id, "batch_no": batch_no, "expiry_date": expiry_date or None,
                       "quantity": quantity, "purchase_cost": purchase_price, "selling_price": sale_price},
                      transaction_id=transaction_id)
        conn.commit()
    finally:
        conn.close()
    return get_medicine(new_id)


def update_medicine(
    medicine_id: int,
    name: str,
    generic_name: str,
    category: str,
    batch_no: str,
    expiry_date: str,
    quantity: int,
    purchase_price: float,
    sale_price: float,
    supplier_id: Optional[int],
    low_stock_threshold: int,
    barcode: str = "",
    is_controlled_substance: bool = False,
    manufacturer: str = "",
    units_per_pack: int = 1,
    actor_id: Optional[int] = None,
) -> Medicine:
    _validate(name, quantity, purchase_price, sale_price, expiry_date)
    units_per_pack = max(1, units_per_pack)

    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _check_barcode_unique(conn, barcode, exclude_medicine_id=medicine_id)
        old = conn.execute("SELECT quantity FROM medicines WHERE id=?", (medicine_id,)).fetchone()
        if old is None:
            raise ValueError("Medicine not found.")
        delta = quantity - old["quantity"]
        transaction_id = str(uuid.uuid4())
        movements = []
        if delta > 0:
            batch_id = str(uuid.uuid4())
            adjustment_batch = f"ADJUSTMENT-{transaction_id[:8]}"
            conn.execute(
                "INSERT INTO medicine_batches(id,medicine_id,cloud_uuid,batch_no,expiry_date,quantity,purchase_price,sale_price,source) "
                "VALUES(?,?,?, ?,NULL,?,?,?,'adjustment')",
                (batch_id, medicine_id, batch_id, adjustment_batch, delta, purchase_price, sale_price),
            )
            movements.append({"batch_id": batch_id, "cloud_batch_id": batch_id, "delta": delta})
        elif delta < 0:
            remaining = -delta
            lots = conn.execute(
                "SELECT id,cloud_uuid,quantity FROM medicine_batches WHERE medicine_id=? AND quantity>0 "
                "ORDER BY CASE WHEN expiry_date IS NULL OR expiry_date='' THEN 1 ELSE 0 END,expiry_date,created_at,id",
                (medicine_id,),
            ).fetchall()
            for lot in lots:
                amount = min(remaining, lot["quantity"])
                if not amount:
                    continue
                conn.execute("UPDATE medicine_batches SET quantity=quantity-? WHERE id=?", (amount, lot["id"]))
                movements.append({"batch_id": lot["id"], "cloud_batch_id": lot["cloud_uuid"] or lot["id"], "delta": -amount})
                remaining -= amount
                if not remaining:
                    break
            if remaining:
                raise ValueError("Batch stock is inconsistent; medicine update was not saved.")
        # Catalog price edits apply to future sales. Completed sale_items retain
        # their own unit price and cost snapshots.
        conn.execute("UPDATE medicine_batches SET sale_price=? WHERE medicine_id=? AND quantity>0", (sale_price, medicine_id))
        conn.execute(
            """
            UPDATE medicines
            SET name=?, generic_name=?, manufacturer=?, category=?, barcode=?, batch_no=?, expiry_date=?,
                quantity=?, units_per_pack=?, purchase_price=?, sale_price=?, supplier_id=?,
                low_stock_threshold=?, is_controlled_substance=?, updated_at=datetime('now', 'localtime')
            WHERE id=?
            """,
            (
                name.strip(), generic_name.strip(), manufacturer.strip(), category.strip(),
                barcode.strip() or None, batch_no.strip(), expiry_date or None, quantity,
                units_per_pack, purchase_price, sale_price,
                supplier_id, low_stock_threshold, int(is_controlled_substance), medicine_id,
            ),
        )
        for movement in movements:
            conn.execute(
                "INSERT INTO stock_movements(id,transaction_id,medicine_id,batch_id,movement_type,quantity_delta,occurred_at,details) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), transaction_id, medicine_id, movement["batch_id"], "adjustment",
                 movement["delta"], utc_now(), '{"reason":"medicine editor quantity change"}'),
            )
        cloud_id = conn.execute("SELECT cloud_uuid FROM medicines WHERE id=?", (medicine_id,)).fetchone()[0]
        enqueue_event(conn, "medicine.updated", str(medicine_id),
                      {"medicine_id": medicine_id, "cloud_medicine_id": cloud_id,
                       "cashier_cloud_id": _cloud_actor_id(conn, actor_id),
                       "name": name.strip(), "generic_name": generic_name.strip(),
                       "manufacturer": manufacturer.strip(), "category": category.strip(), "barcode": barcode.strip(),
                       "quantity": quantity, "sale_price": sale_price, "movements": movements},
                      transaction_id=transaction_id)
        conn.commit()
    finally:
        conn.close()
    return get_medicine(medicine_id)


def delete_medicine(medicine_id: int, actor_id: Optional[int] = None) -> None:
    """Soft-delete: keeps history (sale_items reference medicines) but hides it
    from active inventory listings."""
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        transaction_id = str(uuid.uuid4())
        conn.execute("UPDATE medicines SET is_active = 0 WHERE id = ?", (medicine_id,))
        enqueue_event(conn, "medicine.deactivated", str(medicine_id),
                      {"medicine_id": medicine_id,
                       "cloud_medicine_id": conn.execute("SELECT cloud_uuid FROM medicines WHERE id=?", (medicine_id,)).fetchone()[0],
                       "cashier_cloud_id": _cloud_actor_id(conn, actor_id)},
                      transaction_id=transaction_id)
        conn.commit()
    finally:
        conn.close()


def adjust_stock(medicine_id: int, delta: int, actor_id: Optional[int] = None) -> None:
    """Increase (positive delta) or decrease (negative delta) stock quantity.
    Used by purchase orders (+) and sales (-). Raises if it would go negative."""
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT quantity FROM medicines WHERE id=?", (medicine_id,)).fetchone()
        if row is None:
            raise ValueError("Medicine not found.")
        new_qty = row["quantity"] + delta
        if new_qty < 0:
            raise ValueError("Not enough stock available.")
        transaction_id = str(uuid.uuid4())
        remaining = abs(delta)
        movements = []
        if delta > 0:
            batch_id = str(uuid.uuid4())
            batch_no = f"ADJUSTMENT-{transaction_id[:8]}"
            conn.execute(
                "INSERT INTO medicine_batches(id,medicine_id,cloud_uuid,batch_no,quantity,source) VALUES(?,?,?,?,?,'adjustment')",
                (batch_id, medicine_id, batch_id, batch_no, delta),
            )
            movements.append({"batch_id": batch_id, "cloud_batch_id": batch_id, "quantity_delta": delta})
        elif delta < 0:
            lots = conn.execute(
                "SELECT id,cloud_uuid,quantity FROM medicine_batches WHERE medicine_id=? AND quantity>0 "
                "ORDER BY CASE WHEN expiry_date IS NULL OR expiry_date='' THEN 1 ELSE 0 END,expiry_date,created_at,id",
                (medicine_id,),
            ).fetchall()
            for lot in lots:
                if not remaining:
                    break
                amount = min(remaining, lot["quantity"])
                conn.execute("UPDATE medicine_batches SET quantity=quantity-? WHERE id=?", (amount, lot["id"]))
                movements.append({"batch_id": lot["id"], "cloud_batch_id": lot["cloud_uuid"] or lot["id"], "quantity_delta": -amount})
                remaining -= amount
            if remaining:
                raise ValueError("Batch inventory is inconsistent; stock adjustment was not saved.")
        for movement in movements:
            conn.execute(
                "INSERT INTO stock_movements(id,transaction_id,medicine_id,batch_id,movement_type,quantity_delta,occurred_at,details) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), transaction_id, medicine_id, movement["batch_id"], "adjustment",
                 movement["quantity_delta"], utc_now(), json.dumps({"reason": "manual adjustment"})),
            )
        conn.execute(
            "UPDATE medicines SET quantity=?, updated_at=datetime('now','localtime') WHERE id=?",
            (new_qty, medicine_id),
        )
        enqueue_event(conn, "stock.adjusted", transaction_id,
                      {"medicine_id": medicine_id,
                       "cloud_medicine_id": conn.execute("SELECT cloud_uuid FROM medicines WHERE id=?", (medicine_id,)).fetchone()[0],
                       "cashier_cloud_id": _cloud_actor_id(conn, actor_id),
                       "delta": delta, "movements": movements},
                      transaction_id=transaction_id)
        conn.commit()
    finally:
        conn.close()


def low_stock_medicines() -> list[Medicine]:
    return [m for m in list_medicines() if m.is_low_stock]


def expiring_medicines(within_days: int = 30) -> list[Medicine]:
    cutoff = (date.today() + timedelta(days=within_days)).isoformat()
    today = date.today().isoformat()
    conn = get_connection()
    try:
        rows = conn.execute(
            _SELECT_BASE + " AND m.expiry_date IS NOT NULL AND m.expiry_date != '' "
            "AND m.expiry_date <= ? ORDER BY m.expiry_date",
            (cutoff,),
        ).fetchall()
        return [_row_to_medicine(r) for r in rows]
    finally:
        conn.close()
