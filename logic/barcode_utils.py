"""Barcode generation for medicines that don't have a manufacturer barcode.

A barcode scanner is just a keyboard emulator: it "types" whatever text is
encoded in the barcode it reads, then sends an Enter key press -- exactly
the same as a person typing that text into the Billing search box and
hitting Enter (see ui.billing_view._handle_scan_or_enter). This means a
barcode we generate ourselves scans and works exactly like a manufacturer's
barcode; the only requirement is that the *value* encoded is unique per
medicine, which generate_unique_barcode() guarantees by checking the DB.

Code128 is used because (unlike EAN-13) it can encode any alphanumeric
string without a fixed length or checksum digit, so our own "PHxxxxxxxx"
codes work directly.
"""
import io
import random
from pathlib import Path

import barcode as barcode_lib
from barcode.writer import ImageWriter
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as pdf_canvas

from database.db_manager import APP_DIR, get_connection

LABEL_DIR = APP_DIR / "labels"


def generate_unique_barcode() -> str:
    """Generate an internal barcode value guaranteed not to collide with an
    existing medicine's barcode."""
    conn = get_connection()
    try:
        while True:
            code = f"PH{random.randint(10_000_000, 99_999_999)}"
            exists = conn.execute("SELECT 1 FROM medicines WHERE barcode = ?", (code,)).fetchone()
            if not exists:
                return code
    finally:
        conn.close()


def render_barcode_png(code: str) -> io.BytesIO:
    """Render `code` as a Code128 barcode into an in-memory PNG."""
    buffer = io.BytesIO()
    code128 = barcode_lib.get("code128", code, writer=ImageWriter())
    code128.write(buffer, options={"write_text": True, "quiet_zone": 2, "module_height": 10})
    buffer.seek(0)
    return buffer


def generate_label_pdf(medicine_name: str, code: str, sale_price: float) -> Path:
    """Build a small printable shelf/product label (name, price, barcode)
    sized to fit common label printers, and return the file path."""
    LABEL_DIR.mkdir(parents=True, exist_ok=True)
    file_path = LABEL_DIR / f"label_{code}.pdf"

    image = ImageReader(render_barcode_png(code))

    label_w, label_h = 70 * mm, 35 * mm
    c = pdf_canvas.Canvas(str(file_path), pagesize=(label_w, label_h))

    c.setFont("Helvetica-Bold", 9)
    c.drawCentredString(label_w / 2, label_h - 8 * mm, medicine_name[:32])
    c.setFont("Helvetica", 8)
    c.drawCentredString(label_w / 2, label_h - 13 * mm, f"Price: {sale_price:.2f}")
    c.drawImage(
        image, 5 * mm, 2 * mm, width=60 * mm, height=16 * mm, preserveAspectRatio=True, anchor="c"
    )
    c.save()
    return file_path
