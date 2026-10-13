"""Generates a printable PDF invoice for a completed sale using reportlab."""
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A5
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

from database.db_manager import APP_DIR

INVOICE_DIR = APP_DIR / "invoices"


def generate_invoice_pdf(
    receipt: dict,
    customer_name: str = "Walk-in Customer",
    cashier_name: str = "",
    shop_name: str = "SOLONA Pharmacy",
) -> Path:
    """Build a PDF for the given checkout receipt dict (see logic.sales.checkout)
    and return the path it was written to."""
    INVOICE_DIR.mkdir(parents=True, exist_ok=True)
    file_path = INVOICE_DIR / f"{receipt['invoice_no']}.pdf"

    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(file_path), pagesize=A5, topMargin=14 * mm, bottomMargin=14 * mm)
    elements = []

    elements.append(Paragraph(f"<b>{shop_name}</b>", styles["Title"]))
    elements.append(Paragraph(f"Invoice: {receipt['invoice_no']}", styles["Normal"]))
    elements.append(Paragraph(f"Date: {receipt['date']}", styles["Normal"]))
    elements.append(Paragraph(f"Cashier: {cashier_name}", styles["Normal"]))
    elements.append(Paragraph(f"Customer: {customer_name}", styles["Normal"]))
    if receipt.get("doctor_name"):
        elements.append(Paragraph(f"Doctor: {receipt['doctor_name']}", styles["Normal"]))
    elements.append(Spacer(1, 10 * mm))

    data = [["Medicine", "Qty", "Unit Price", "Subtotal"]]
    for item in receipt["items"]:
        data.append([item.name, str(item.quantity), f"{item.unit_price:.2f}", f"{item.subtotal:.2f}"])

    table = Table(data, colWidths=[70 * mm, 20 * mm, 30 * mm, 30 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1565C0")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ]
        )
    )
    elements.append(table)
    elements.append(Spacer(1, 6 * mm))

    payment_labels = {
        "cash": "Cash", "card": "Card", "easypaisa": "EasyPaisa",
        "jazzcash": "JazzCash", "bank": "Bank Transfer", "udhaar": "Udhaar (Credit)",
    }
    totals = [
        ["Subtotal", f"{receipt['subtotal']:.2f}"],
        ["Discount", f"{receipt['discount']:.2f}"],
        ["Tax", f"{receipt['tax']:.2f}"],
        ["Total", f"{receipt['total']:.2f}"],
        ["Payment Method", payment_labels.get(receipt.get("payment_method"), "Cash")],
        ["Amount Paid", f"{receipt.get('amount_paid', receipt['total']):.2f}"],
    ]
    if receipt.get("credit_amount", 0) > 0.005:
        totals.append(["Udhaar (Added to Balance)", f"{receipt['credit_amount']:.2f}"])
    totals_table = Table(totals, colWidths=[120 * mm, 30 * mm])
    totals_table.setStyle(
        TableStyle(
            [
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("FONTNAME", (0, 3), (-1, 3), "Helvetica-Bold"),
                ("LINEABOVE", (0, 3), (-1, 3), 0.75, colors.black),
            ]
        )
    )
    elements.append(totals_table)
    elements.append(Spacer(1, 10 * mm))
    elements.append(Paragraph("Thank you for your purchase!", styles["Normal"]))

    doc.build(elements)
    return file_path
