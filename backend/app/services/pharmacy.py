from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.models import (
    Customer, Medicine, MedicineBatch, Payment, Purchase, PurchaseItem, Sale,
    SaleItem, SaleReturn, Shop, StockMovement, Supplier,
)
from app.schemas import (
    CustomerCreate, MedicineCreate, PaymentCreate, PurchaseCreate, SaleCreate,
    SupplierCreate,
)
from app.services.audit import record_audit

CENT = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def add_medicine(db: Session, tenant_id: UUID, actor_id: UUID, payload: MedicineCreate) -> Medicine:
    medicine = Medicine(tenant_id=tenant_id, **payload.model_dump())
    db.add(medicine)
    db.flush()
    record_audit(db, tenant_id=tenant_id, actor_id=actor_id, action="medicine.created", resource_type="medicine", resource_id=medicine.id)
    return medicine


def add_supplier(db: Session, tenant_id: UUID, actor_id: UUID, payload: SupplierCreate) -> Supplier:
    supplier = Supplier(tenant_id=tenant_id, **payload.model_dump())
    db.add(supplier)
    db.flush()
    record_audit(db, tenant_id=tenant_id, actor_id=actor_id, action="supplier.created", resource_type="supplier", resource_id=supplier.id)
    return supplier


def add_customer(db: Session, tenant_id: UUID, actor_id: UUID, payload: CustomerCreate) -> Customer:
    customer = Customer(tenant_id=tenant_id, **payload.model_dump())
    db.add(customer)
    db.flush()
    record_audit(db, tenant_id=tenant_id, actor_id=actor_id, action="customer.created", resource_type="customer", resource_id=customer.id)
    return customer


def receive_purchase(db: Session, tenant_id: UUID, shop_id: UUID, actor_id: UUID, payload: PurchaseCreate) -> Purchase:
    if len({(line.medicine_id, line.batch_no) for line in payload.items}) != len(payload.items):
        raise ApiError(422, "duplicate_batch_line", "Each medicine/batch must appear only once in a purchase.")
    if db.scalar(select(Purchase.id).where(Purchase.tenant_id == tenant_id, Purchase.transaction_id == payload.transaction_id)):
        raise ApiError(409, "duplicate_transaction", "This purchase transaction has already been recorded.")
    supplier = db.scalar(select(Supplier).where(Supplier.tenant_id == tenant_id, Supplier.id == payload.supplier_id, Supplier.is_active.is_(True)).with_for_update())
    if supplier is None:
        raise ApiError(404, "supplier_not_found", "Supplier not found.")

    purchase = Purchase(tenant_id=tenant_id, shop_id=shop_id, supplier_id=payload.supplier_id, transaction_id=payload.transaction_id, reference=payload.reference)
    db.add(purchase)
    db.flush()
    purchase_total = Decimal("0.00")
    for line in sorted(payload.items, key=lambda item: (str(item.medicine_id), item.batch_no)):
        medicine = db.scalar(select(Medicine).where(Medicine.tenant_id == tenant_id, Medicine.id == line.medicine_id, Medicine.is_active.is_(True)))
        if medicine is None:
            raise ApiError(404, "medicine_not_found", "A medicine in this purchase was not found.")
        batch = db.scalar(
            select(MedicineBatch).where(
                MedicineBatch.tenant_id == tenant_id,
                MedicineBatch.shop_id == shop_id,
                MedicineBatch.medicine_id == line.medicine_id,
                MedicineBatch.batch_no == line.batch_no,
            ).with_for_update()
        )
        if batch and batch.expiry_date != line.expiry_date:
            raise ApiError(409, "batch_expiry_mismatch", "The recorded expiry for this batch does not match.")
        if batch:
            previous_qty = batch.quantity
            new_qty = previous_qty + line.quantity
            batch.purchase_cost = money((batch.purchase_cost * previous_qty + line.purchase_cost * line.quantity) / new_qty)
            batch.selling_price = line.selling_price
            batch.quantity = new_qty
            batch.supplier_id = payload.supplier_id
        else:
            batch = MedicineBatch(tenant_id=tenant_id, shop_id=shop_id, medicine_id=line.medicine_id, batch_no=line.batch_no, expiry_date=line.expiry_date, quantity=line.quantity, purchase_cost=line.purchase_cost, selling_price=line.selling_price, supplier_id=payload.supplier_id)
            db.add(batch)
            db.flush()
        db.add(PurchaseItem(tenant_id=tenant_id, purchase_id=purchase.id, shop_id=shop_id, medicine_id=line.medicine_id, batch_id=batch.id, batch_no=line.batch_no, expiry_date=line.expiry_date, quantity=line.quantity, purchase_cost=line.purchase_cost, selling_price=line.selling_price))
        db.add(StockMovement(tenant_id=tenant_id, shop_id=shop_id, batch_id=batch.id, transaction_id=payload.transaction_id, movement_type="purchase_receipt", quantity_delta=line.quantity, actor_id=actor_id))
        purchase_total += line.purchase_cost * line.quantity

    purchase.total_amount = money(purchase_total)
    purchase.amount_paid = purchase.total_amount if payload.amount_paid is None else money(payload.amount_paid)
    if purchase.amount_paid > purchase.total_amount:
        raise ApiError(422, "overpayment", "Payment cannot exceed the purchase total.")
    payable_added = purchase.total_amount - purchase.amount_paid
    supplier.payable_balance += payable_added
    purchase.status = "paid" if purchase.amount_paid == purchase.total_amount else "partially_paid"
    if purchase.amount_paid > 0:
        db.add(Payment(tenant_id=tenant_id, shop_id=shop_id, purchase_id=purchase.id, transaction_id=payload.transaction_id, amount=purchase.amount_paid, currency="KES", method=payload.payment_method))
    record_audit(db, tenant_id=tenant_id, actor_id=actor_id, action="purchase.received", resource_type="purchase", resource_id=purchase.id, shop_id=shop_id, transaction_id=payload.transaction_id, details={"line_count": len(payload.items)})
    db.flush()
    return purchase


def record_sale(db: Session, tenant_id: UUID, shop_id: UUID, actor_id: UUID, payload: SaleCreate) -> Sale:
    if db.scalar(select(Sale.id).where(Sale.tenant_id == tenant_id, Sale.transaction_id == payload.transaction_id)):
        raise ApiError(409, "duplicate_transaction", "This sale transaction has already been recorded.")
    if db.scalar(select(Sale.id).where(Sale.tenant_id == tenant_id, Sale.shop_id == shop_id, Sale.invoice_no == payload.invoice_no)):
        raise ApiError(409, "duplicate_invoice", "This invoice number has already been used in the shop.")
    if len({line.batch_id for line in payload.items}) != len(payload.items):
        raise ApiError(422, "duplicate_batch_line", "Each stock batch must appear only once in a sale.")

    customer = None
    if payload.customer_id:
        customer = db.scalar(select(Customer).where(Customer.tenant_id == tenant_id, Customer.id == payload.customer_id, Customer.is_active.is_(True)).with_for_update())
        if customer is None:
            raise ApiError(404, "customer_not_found", "Customer not found.")

    lines = sorted(payload.items, key=lambda item: str(item.batch_id))
    batches: dict[UUID, MedicineBatch] = {}
    shop = db.get(Shop, shop_id)
    today = datetime.now(ZoneInfo(shop.timezone)).date()
    for line in lines:
        batch = db.scalar(
            select(MedicineBatch).where(
                MedicineBatch.tenant_id == tenant_id,
                MedicineBatch.shop_id == shop_id,
                MedicineBatch.id == line.batch_id,
            ).with_for_update()
        )
        if batch is None:
            raise ApiError(404, "batch_not_found", "A requested stock batch was not found in this shop.")
        if batch.expiry_date < today:
            raise ApiError(409, "batch_expired", "Expired stock cannot be sold.")
        if batch.quantity < line.quantity:
            raise ApiError(409, "insufficient_stock", "A requested batch does not have enough stock.")
        batches[line.batch_id] = batch

    total = money(sum((batches[line.batch_id].selling_price * line.quantity for line in lines), Decimal("0.00")))
    if payload.amount_paid > total:
        raise ApiError(422, "overpayment", "Payment cannot exceed the sale total.")
    if payload.amount_paid < total and customer is None:
        raise ApiError(422, "customer_required_for_credit", "Select a customer before recording an unpaid balance.")
    if customer is not None:
        customer.credit_balance += total - payload.amount_paid
    sale = Sale(tenant_id=tenant_id, shop_id=shop_id, transaction_id=payload.transaction_id, cashier_id=actor_id, customer_id=payload.customer_id, invoice_no=payload.invoice_no, currency=payload.currency, total_amount=total, amount_paid=payload.amount_paid, status="paid" if payload.amount_paid == total else "partially_paid")
    db.add(sale)
    db.flush()
    for line in lines:
        batch = batches[line.batch_id]
        batch.quantity -= line.quantity
        db.add(SaleItem(tenant_id=tenant_id, sale_id=sale.id, shop_id=shop_id, batch_id=batch.id, quantity=line.quantity, unit_price=batch.selling_price, unit_cost=batch.purchase_cost, subtotal=money(batch.selling_price * line.quantity)))
        db.add(StockMovement(tenant_id=tenant_id, shop_id=shop_id, batch_id=batch.id, transaction_id=payload.transaction_id, movement_type="sale", quantity_delta=-line.quantity, actor_id=actor_id))
    if payload.amount_paid > 0:
        db.add(Payment(tenant_id=tenant_id, shop_id=shop_id, sale_id=sale.id, transaction_id=payload.transaction_id, amount=payload.amount_paid, currency=payload.currency, method=payload.payment_method))
    record_audit(db, tenant_id=tenant_id, actor_id=actor_id, action="sale.created", resource_type="sale", resource_id=sale.id, shop_id=shop_id, transaction_id=payload.transaction_id, details={"line_count": len(lines), "total": str(total)})
    db.flush()
    return sale


def record_replayed_sale(db: Session, tenant_id: UUID, shop_id: UUID, actor_id: UUID,
                         transaction_id: UUID, payload: dict) -> Sale:
    """Apply one authenticated offline sale inside the caller's DB transaction.

    The sync inbox owns event idempotency; an already-created sale without the
    matching inbox event is a conflict, never a successful duplicate. Stock,
    financial, movement and audit writes remain in this same transaction.
    """
    existing = db.scalar(select(Sale).where(Sale.tenant_id == tenant_id, Sale.transaction_id == transaction_id))
    if existing:
        raise ApiError(409, "offline_transaction_conflict", "This sale transaction already exists without a matching inbox event.")
    invoice_no = str(payload.get("invoice_no", ""))
    if not invoice_no or db.scalar(select(Sale.id).where(
        Sale.tenant_id == tenant_id, Sale.shop_id == shop_id, Sale.invoice_no == invoice_no,
    )):
        raise ApiError(409, "offline_invoice_conflict", "This offline invoice cannot be reconciled automatically.")
    lines = payload.get("lines", [])
    if not lines:
        raise ApiError(422, "offline_sale_invalid", "The offline sale has no items.")
    customer_id = UUID(payload["customer_cloud_id"]) if payload.get("customer_cloud_id") else None
    customer = None
    if customer_id:
        customer = db.scalar(select(Customer).where(
            Customer.tenant_id == tenant_id, Customer.id == customer_id, Customer.is_active.is_(True),
        ).with_for_update())
        if customer is None:
            raise ApiError(409, "offline_customer_unavailable", "The offline customer cannot be reconciled.")
    parsed = []
    seen_batches = set()
    seen_item_ids = set()
    for line in sorted(lines, key=lambda value: str(value.get("cloud_batch_id", ""))):
        try:
            batch_id = UUID(line["cloud_batch_id"])
            medicine_id = UUID(line["cloud_medicine_id"])
            quantity = int(line["quantity"])
            unit_price = money(Decimal(str(line["unit_price"])))
            unit_cost = money(Decimal(str(line["unit_cost"])))
            sale_item_id = UUID(line["cloud_sale_item_id"])
            line_subtotal = money(Decimal(str(line["subtotal"])))
        except (ValueError, KeyError, TypeError):
            raise ApiError(422, "offline_sale_invalid", "The offline sale item is invalid.") from None
        if (quantity <= 0 or unit_price < 0 or unit_cost < 0 or line_subtotal != money(unit_price * quantity)
                or batch_id in seen_batches or sale_item_id in seen_item_ids):
            raise ApiError(422, "offline_sale_invalid", "The offline sale item is invalid.")
        seen_batches.add(batch_id)
        seen_item_ids.add(sale_item_id)
        batch = db.scalar(select(MedicineBatch).where(
            MedicineBatch.tenant_id == tenant_id, MedicineBatch.shop_id == shop_id,
            MedicineBatch.id == batch_id, MedicineBatch.medicine_id == medicine_id,
        ).with_for_update())
        if batch is None:
            raise ApiError(409, "offline_batch_unavailable", "An offline batch cannot be reconciled.")
        if batch.quantity < quantity:
            raise ApiError(409, "offline_stock_conflict", "Shop stock changed while this terminal was offline.")
        parsed.append((batch, medicine_id, quantity, unit_price, unit_cost, sale_item_id, line_subtotal))

    subtotal = money(sum((line_total for *_, line_total in parsed), Decimal("0.00")))
    try:
        discount = money(Decimal(str(payload.get("discount", 0))))
        tax = money(Decimal(str(payload.get("tax", 0))))
        total = money(Decimal(str(payload["total_amount"])))
        amount_paid = money(Decimal(str(payload.get("amount_paid", 0))))
    except (ValueError, KeyError, TypeError, ArithmeticError):
        raise ApiError(422, "offline_amount_conflict", "The offline sale totals are invalid.") from None
    calculated_total = money(subtotal - discount + tax)
    credit_amount = money(Decimal(str(payload.get("credit_amount", total - amount_paid))))
    if (discount < 0 or discount > subtotal or tax < 0 or total != calculated_total
            or amount_paid < 0 or amount_paid > total or credit_amount != money(total - amount_paid)):
        raise ApiError(409, "offline_amount_conflict", "The offline sale totals do not match its item snapshots.")
    if amount_paid < total and customer is None:
        raise ApiError(409, "offline_credit_customer_missing", "The offline credit sale has no matching customer.")
    method = payload.get("payment_method", "cash")
    method = {"bank": "bank_transfer", "udhaar": "credit", "easypaisa": "other", "jazzcash": "other"}.get(method, method)
    if method not in {"cash", "card", "bank_transfer", "credit", "other"}:
        raise ApiError(409, "offline_payment_method_invalid", "The offline payment method cannot be reconciled.")
    payment_id = None
    if amount_paid > 0:
        try:
            payment_id = UUID(payload["payment_id"])
        except (ValueError, KeyError, TypeError):
            raise ApiError(422, "offline_sale_invalid", "The offline payment identifier is invalid.") from None
    sale = Sale(id=transaction_id, tenant_id=tenant_id, shop_id=shop_id, transaction_id=transaction_id,
                cashier_id=actor_id, customer_id=customer_id, invoice_no=invoice_no,
                currency="KES", total_amount=total, amount_paid=amount_paid,
                discount_amount=discount, tax_amount=tax,
                status="paid" if amount_paid == total else "partially_paid")
    db.add(sale)
    db.flush()
    if customer is not None:
        customer.credit_balance += total - amount_paid
    for batch, medicine_id, quantity, price, cost, sale_item_id, line_subtotal in parsed:
        batch.quantity -= quantity
        db.add(SaleItem(id=sale_item_id, tenant_id=tenant_id, sale_id=sale.id, shop_id=shop_id,
                        batch_id=batch.id, quantity=quantity, unit_price=price,
                        unit_cost=cost, subtotal=line_subtotal))
        db.add(StockMovement(tenant_id=tenant_id, shop_id=shop_id, batch_id=batch.id,
                             transaction_id=transaction_id, movement_type="sale",
                             quantity_delta=-quantity, actor_id=actor_id))
    if amount_paid > 0:
        db.add(Payment(id=payment_id, tenant_id=tenant_id, shop_id=shop_id, sale_id=sale.id,
                       transaction_id=payment_id, amount=amount_paid,
                       currency="KES", method=method))
    record_audit(db, tenant_id=tenant_id, actor_id=actor_id, action="sale.reconciled_offline",
                 resource_type="sale", resource_id=sale.id, shop_id=shop_id,
                 transaction_id=transaction_id, details={"line_count": len(parsed), "total": str(total),
                                                          "discount": str(discount), "tax": str(tax),
                                                          "amount_paid": str(amount_paid)})
    db.flush()
    return sale


def record_replayed_return(db: Session, tenant_id: UUID, shop_id: UUID, actor_id: UUID,
                           transaction_id: UUID, payload: dict) -> SaleReturn:
    """Apply an offline return as an append-only compensation in the caller transaction."""
    if db.scalar(select(SaleReturn.id).where(
        SaleReturn.tenant_id == tenant_id, SaleReturn.transaction_id == transaction_id,
    )):
        raise ApiError(409, "offline_return_conflict", "This return transaction already exists without its matching inbox event.")
    try:
        sale_transaction_id = UUID(str(payload["sale_transaction_id"]))
        sale_item_id = UUID(str(payload["cloud_sale_item_id"]))
        batch_id = UUID(str(payload["cloud_batch_id"]))
        raw_quantity = payload["quantity"]
        if isinstance(raw_quantity, bool):
            raise ValueError("boolean quantity")
        quantity = int(raw_quantity)
        reported_refund = money(Decimal(str(payload["refund_amount"])))
        reported_credit_reduction = money(Decimal(str(payload["credit_reduction"])))
        reason = str(payload["reason"]).strip()
    except (ValueError, KeyError, TypeError, ArithmeticError):
        raise ApiError(422, "offline_return_invalid", "The offline return data is invalid.") from None
    if quantity <= 0 or not reason or len(reason) > 300:
        raise ApiError(422, "offline_return_invalid", "The offline return quantity or reason is invalid.")

    sale = db.scalar(select(Sale).where(
        Sale.tenant_id == tenant_id, Sale.shop_id == shop_id,
        Sale.transaction_id == sale_transaction_id,
    ).with_for_update())
    if sale is None:
        raise ApiError(409, "offline_return_sale_missing", "The original sale is not present in the cloud ledger.")
    item = db.scalar(select(SaleItem).where(
        SaleItem.tenant_id == tenant_id, SaleItem.shop_id == shop_id,
        SaleItem.sale_id == sale.id, SaleItem.id == sale_item_id,
    ).with_for_update())
    if item is None or item.batch_id != batch_id:
        raise ApiError(409, "offline_return_item_mismatch", "The returned sale line or batch does not match the original sale.")
    if item.returned_qty + quantity > item.quantity:
        raise ApiError(409, "offline_return_quantity_conflict", "The return exceeds the unreturned quantity on the original sale line.")
    expected_refund = money(item.unit_price * quantity)
    if reported_refund != expected_refund:
        raise ApiError(409, "offline_return_amount_conflict", "The refund does not match the original sale price.")

    customer = None
    if sale.customer_id is not None:
        customer = db.scalar(select(Customer).where(
            Customer.tenant_id == tenant_id, Customer.id == sale.customer_id,
        ).with_for_update())
        if customer is None:
            raise ApiError(409, "offline_return_customer_missing", "The original sale customer is unavailable.")
    outstanding = money(sale.total_amount - sale.amount_paid)
    expected_credit_reduction = min(expected_refund, outstanding) if customer else Decimal("0.00")
    expected_credit_reduction = money(expected_credit_reduction)
    if reported_credit_reduction != expected_credit_reduction:
        raise ApiError(409, "offline_return_credit_conflict", "The return credit adjustment does not match the outstanding sale balance.")
    expected_cash_refund = money(expected_refund - expected_credit_reduction)

    batch = db.scalar(select(MedicineBatch).where(
        MedicineBatch.tenant_id == tenant_id, MedicineBatch.shop_id == shop_id,
        MedicineBatch.id == batch_id,
    ).with_for_update())
    if batch is None:
        raise ApiError(409, "offline_return_batch_missing", "The returned batch is unavailable in the original shop.")

    item.returned_qty += quantity
    batch.quantity += quantity
    if customer:
        if customer.credit_balance < expected_credit_reduction:
            raise ApiError(409, "offline_return_credit_balance_conflict", "The customer credit balance does not cover this return adjustment.")
        customer.credit_balance -= expected_credit_reduction
        sale.amount_paid += expected_credit_reduction
    return_record = SaleReturn(
        tenant_id=tenant_id, shop_id=shop_id,
        transaction_id=transaction_id, sale_id=sale.id, sale_item_id=item.id,
        batch_id=batch.id, quantity=quantity, refund_amount=expected_refund,
        credit_reduction=expected_credit_reduction, cash_refund=expected_cash_refund,
        reason=reason, actor_id=actor_id,
    )
    db.add(return_record)
    db.add(StockMovement(
        tenant_id=tenant_id, shop_id=shop_id, batch_id=batch.id,
        transaction_id=transaction_id, movement_type="return",
        quantity_delta=quantity, actor_id=actor_id,
    ))
    sale_items = db.scalars(select(SaleItem).where(SaleItem.sale_id == sale.id)).all()
    if sale_items and all(sale_item.returned_qty == sale_item.quantity for sale_item in sale_items):
        sale.status = "refunded"
    record_audit(
        db, tenant_id=tenant_id, actor_id=actor_id, action="sale.returned",
        resource_type="sale_return", resource_id=return_record.id, shop_id=shop_id,
        transaction_id=transaction_id,
        details={"sale_id": str(sale.id), "sale_item_id": str(item.id), "quantity": quantity,
                 "refund_amount": str(expected_refund), "credit_reduction": str(expected_credit_reduction),
                 "cash_refund": str(expected_cash_refund), "reason": reason},
    )
    db.flush()
    return return_record


def record_sale_payment(db: Session, tenant_id: UUID, shop_id: UUID, sale_id: UUID, actor_id: UUID, payload: PaymentCreate) -> tuple[Payment, Sale]:
    if db.scalar(select(Payment.id).where(Payment.tenant_id == tenant_id, Payment.transaction_id == payload.transaction_id)):
        raise ApiError(409, "duplicate_transaction", "This payment transaction has already been recorded.")
    sale = db.scalar(select(Sale).where(Sale.tenant_id == tenant_id, Sale.shop_id == shop_id, Sale.id == sale_id).with_for_update())
    if sale is None:
        raise ApiError(404, "sale_not_found", "Sale not found.")
    balance = sale.total_amount - sale.amount_paid
    if payload.amount > balance:
        raise ApiError(422, "overpayment", "Payment cannot exceed the outstanding sale balance.")
    if sale.customer_id is not None:
        customer = db.scalar(select(Customer).where(Customer.tenant_id == tenant_id, Customer.id == sale.customer_id).with_for_update())
        if customer is None or customer.credit_balance < payload.amount:
            raise ApiError(409, "credit_balance_mismatch", "The customer balance does not match the sale balance.")
        customer.credit_balance -= payload.amount
    sale.amount_paid += payload.amount
    sale.status = "paid" if sale.amount_paid == sale.total_amount else "partially_paid"
    payment = Payment(tenant_id=tenant_id, shop_id=shop_id, sale_id=sale.id, transaction_id=payload.transaction_id, amount=payload.amount, currency=sale.currency, method=payload.method, provider_reference=payload.provider_reference)
    db.add(payment)
    db.flush()
    record_audit(db, tenant_id=tenant_id, actor_id=actor_id, action="payment.recorded", resource_type="payment", resource_id=payment.id, shop_id=shop_id, transaction_id=payload.transaction_id, details={"sale_id": str(sale.id), "amount": str(payload.amount), "method": payload.method})
    db.flush()
    return payment, sale


def record_purchase_payment(db: Session, tenant_id: UUID, shop_id: UUID, purchase_id: UUID, actor_id: UUID, payload: PaymentCreate) -> tuple[Payment, Purchase]:
    if db.scalar(select(Payment.id).where(Payment.tenant_id == tenant_id, Payment.transaction_id == payload.transaction_id)):
        raise ApiError(409, "duplicate_transaction", "This payment transaction has already been recorded.")
    purchase = db.scalar(select(Purchase).where(Purchase.tenant_id == tenant_id, Purchase.shop_id == shop_id, Purchase.id == purchase_id).with_for_update())
    if purchase is None:
        raise ApiError(404, "purchase_not_found", "Purchase not found.")
    balance = purchase.total_amount - purchase.amount_paid
    if payload.amount > balance:
        raise ApiError(422, "overpayment", "Payment cannot exceed the outstanding purchase balance.")
    supplier = db.scalar(select(Supplier).where(Supplier.tenant_id == tenant_id, Supplier.id == purchase.supplier_id).with_for_update())
    if supplier is None or supplier.payable_balance < payload.amount:
        raise ApiError(409, "payable_balance_mismatch", "The supplier payable does not match the purchase balance.")
    purchase.amount_paid += payload.amount
    purchase.status = "paid" if purchase.amount_paid == purchase.total_amount else "partially_paid"
    supplier.payable_balance -= payload.amount
    payment = Payment(tenant_id=tenant_id, shop_id=shop_id, purchase_id=purchase.id, transaction_id=payload.transaction_id, amount=payload.amount, currency="KES", method=payload.method, provider_reference=payload.provider_reference)
    db.add(payment)
    db.flush()
    record_audit(db, tenant_id=tenant_id, actor_id=actor_id, action="supplier_payment.recorded", resource_type="payment", resource_id=payment.id, shop_id=shop_id, transaction_id=payload.transaction_id, details={"purchase_id": str(purchase.id), "amount": str(payload.amount), "method": payload.method})
    db.flush()
    return payment, purchase
