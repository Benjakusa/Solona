"""Narrow-width receipt generation for 58mm/80mm thermal POS printers.

Most USB thermal receipt printers sold in Pakistan install as an ordinary
Windows printer (their own driver exposes a paper size matching the roll
width), so a correctly-sized PDF prints on them just like on any other
printer -- no raw ESC/POS byte protocol needed. ui.billing_view sends this
PDF straight to the default printer via logic.printing.print_document()
(Windows' own shell "print" verb) rather than requiring the user to open
it manually first.
"""
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A5
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from database.db_manager import APP_DIR

RECEIPT_DIR = APP_DIR / "invoices"

# (width, printable-height-per-line-budget) -- height is generous/unbounded
# since thermal rolls print continuously; reportlab just needs *a* page
# height, so this uses a tall placeholder that's trimmed to content anyway.
_WIDTHS_MM = {"58mm": 58, "80mm": 80}

PAYMENT_LABELS = {
    "cash": "Cash", "card": "Card", "easypaisa": "EasyPaisa",
    "jazzcash": "JazzCash", "bank": "Bank Transfer", "udhaar": "Udhaar (Credit)",
}


def generate_thermal_receipt(
    receipt: dict,
    width: str = "58mm",
    customer_name: str = "Walk-in Customer",
    cashier_name: str = "",
    shop_name: str = "SOLONA Pharmacy",
) -> Path:
    """Build a narrow receipt PDF sized for a thermal printer and return its
    path. `width` is "58mm" or "80mm"."""
    RECEIPT_DIR.mkdir(parents=True, exist_ok=True)
    # Width is part of the filename (not just the invoice number) so
    # regenerating the same invoice's receipt at a different roll width --
    # e.g. after changing the shop's printer -- never silently overwrites an
    # earlier one still sitting in this folder.
    file_path = RECEIPT_DIR / f"{receipt['invoice_no']}_thermal_{width}.pdf"

    page_w = _WIDTHS_MM.get(width, 58) * mm
    page_h = 297 * mm  # tall placeholder; content determines actual print length
    margin = 2 * mm
    content_w = page_w - 2 * margin

    styles = getSampleStyleSheet()
    center = styles["Normal"].clone("center")
    center.alignment = 1
    center.fontSize = 8
    small = styles["Normal"].clone("small")
    small.fontSize = 7

    doc = SimpleDocTemplate(
        str(file_path), pagesize=(page_w, page_h),
        leftMargin=margin, rightMargin=margin, topMargin=margin, bottomMargin=margin,
    )
    elements = [
        Paragraph(f"<b>{shop_name}</b>", center),
        Paragraph(f"Invoice: {receipt['invoice_no']}", small),
        Paragraph(f"{receipt['date']}", small),
        Paragraph(f"Cashier: {cashier_name}", small),
        Paragraph(f"Customer: {customer_name}", small),
    ]
    if receipt.get("doctor_name"):
        elements.append(Paragraph(f"Doctor: {receipt['doctor_name']}", small))
    elements.append(Spacer(1, 2 * mm))

    name_w = content_w * 0.5
    qty_w = content_w * 0.15
    price_w = content_w * 0.35
    data = [["Item", "Qty", "Amt"]]
    for item in receipt["items"]:
        data.append([item.name[:22], str(item.quantity), f"{item.subtotal:.2f}"])
    table = Table(data, colWidths=[name_w, qty_w, price_w])
    table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.black),
    ]))
    elements.append(table)
    elements.append(Spacer(1, 1 * mm))

    totals = [
        ["Subtotal", f"{receipt['subtotal']:.2f}"],
        ["Discount", f"{receipt['discount']:.2f}"],
        ["Tax", f"{receipt['tax']:.2f}"],
        ["TOTAL", f"{receipt['total']:.2f}"],
        ["Paid (" + PAYMENT_LABELS.get(receipt.get("payment_method"), "Cash") + ")",
         f"{receipt.get('amount_paid', receipt['total']):.2f}"],
    ]
    if receipt.get("credit_amount", 0) > 0.005:
        totals.append(["Udhaar Added", f"{receipt['credit_amount']:.2f}"])
    totals_table = Table(totals, colWidths=[content_w * 0.65, content_w * 0.35])
    totals_table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("FONTNAME", (0, 3), (-1, 3), "Helvetica-Bold"),
        ("LINEABOVE", (0, 3), (-1, 3), 0.5, colors.black),
    ]))
    elements.append(totals_table)
    elements.append(Spacer(1, 2 * mm))
    elements.append(Paragraph("Thank you!", center))

    doc.build(elements)
    return file_path
