"""Reports screen: Daily Sales / Monthly Sales / Profit & Loss / Expiry Stock,
each viewable on-screen with Export to PDF / Export to Excel buttons."""
from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDateEdit,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from logic import report_export, reports


def _fill_table(table: QTableWidget, headers: list[str], rows: list[list]):
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            table.setItem(r, c, QTableWidgetItem(str(value)))


class _ReportTab(QWidget):
    """Shared scaffolding: a table + Export PDF / Export Excel buttons."""

    def __init__(self):
        super().__init__()
        self._headers: list[str] = []
        self._rows: list[list] = []
        self._title = "Report"

        self.layout_ = QVBoxLayout()
        self.controls_layout = QHBoxLayout()
        self.layout_.addLayout(self.controls_layout)

        self.summary_label = QLabel("")
        self.summary_label.setProperty("heading", True)
        self.layout_.addWidget(self.summary_label)

        self.table = QTableWidget(0, 0)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.layout_.addWidget(self.table)

        export_row = QHBoxLayout()
        pdf_btn = QPushButton("📄  Export to PDF")
        pdf_btn.clicked.connect(lambda: self._export("pdf"))
        excel_btn = QPushButton("📊  Export to Excel")
        excel_btn.clicked.connect(lambda: self._export("excel"))
        export_row.addWidget(pdf_btn)
        export_row.addWidget(excel_btn)
        export_row.addStretch()
        self.layout_.addLayout(export_row)

        self.setLayout(self.layout_)

    def _set_data(self, title: str, headers: list[str], rows: list[list], summary: str = ""):
        self._title = title
        self._headers = headers
        self._rows = rows
        self.summary_label.setText(summary)
        _fill_table(self.table, headers, rows)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)

    def _export(self, kind: str):
        if not self._rows:
            QMessageBox.information(self, "Nothing to export", "There is no data in this report to export.")
            return
        default_name = self._title.replace(" ", "_")
        ext = "pdf" if kind == "pdf" else "xlsx"
        file_path, _ = QFileDialog.getSaveFileName(
            self, f"Export {self._title}", f"{default_name}.{ext}",
            "PDF Files (*.pdf)" if kind == "pdf" else "Excel Files (*.xlsx)",
        )
        if not file_path:
            return
        try:
            if kind == "pdf":
                report_export.export_table_to_pdf(self._title, self._headers, self._rows, file_path)
            else:
                report_export.export_table_to_excel(self._title, self._headers, self._rows, file_path)
            QMessageBox.information(self, "Export complete", f"Report exported to:\n{file_path}")
        except Exception as e:
            QMessageBox.critical(self, "Export failed", str(e))


class DailySalesTab(_ReportTab):
    def __init__(self):
        super().__init__()
        self.date_input = QDateEdit()
        self.date_input.setCalendarPopup(True)
        self.date_input.setDisplayFormat("yyyy-MM-dd")
        self.date_input.setDate(QDate.currentDate())
        self.controls_layout.addWidget(QLabel("Date:"))
        self.controls_layout.addWidget(self.date_input)
        refresh_btn = QPushButton("🔄  Refresh")
        refresh_btn.clicked.connect(self.refresh)
        self.controls_layout.addWidget(refresh_btn)
        self.controls_layout.addStretch()
        self.refresh()

    def refresh(self):
        day = self.date_input.date().toString("yyyy-MM-dd")
        data = reports.daily_sales_report(day)
        headers = ["Invoice No.", "Time", "Customer", "Cashier", "Total", "Discount", "Tax"]
        rows = [
            [s["invoice_no"], s["date"], s["customer_name"], s["cashier_name"],
             f"{s['total_amount']:.2f}", f"{s['discount']:.2f}", f"{s['tax']:.2f}"]
            for s in data["sales"]
        ]
        t = data["totals"]
        summary = (
            f"Daily Sales — {day}   |   {t['count']} sale(s)   |   Total: {t['total_amount']:.2f}   "
            f"Discount: {t['total_discount']:.2f}   Tax: {t['total_tax']:.2f}"
        )
        self._set_data(f"Daily Sales Report {day}", headers, rows, summary)


class MonthlySalesTab(_ReportTab):
    def __init__(self):
        super().__init__()
        today = date.today()
        self.year_input = QSpinBox()
        self.year_input.setRange(2000, 2100)
        self.year_input.setValue(today.year)
        self.month_input = QSpinBox()
        self.month_input.setRange(1, 12)
        self.month_input.setValue(today.month)
        self.controls_layout.addWidget(QLabel("Year:"))
        self.controls_layout.addWidget(self.year_input)
        self.controls_layout.addWidget(QLabel("Month:"))
        self.controls_layout.addWidget(self.month_input)
        refresh_btn = QPushButton("🔄  Refresh")
        refresh_btn.clicked.connect(self.refresh)
        self.controls_layout.addWidget(refresh_btn)
        self.controls_layout.addStretch()
        self.refresh()

    def refresh(self):
        year, month = self.year_input.value(), self.month_input.value()
        data = reports.monthly_sales_report(year, month)
        headers = ["Day", "Sales Count", "Total", "Discount", "Tax"]
        rows = [
            [d["day"], d["count"], f"{d['total']:.2f}", f"{d['discount']:.2f}", f"{d['tax']:.2f}"]
            for d in data["daily_breakdown"]
        ]
        t = data["totals"]
        summary = (
            f"Monthly Sales — {data['month']}   |   {t['count']} sale(s)   |   Total: {t['total_amount']:.2f}"
        )
        self._set_data(f"Monthly Sales Report {data['month']}", headers, rows, summary)


class ProfitLossTab(_ReportTab):
    def __init__(self):
        super().__init__()
        self.start_input = QDateEdit()
        self.start_input.setCalendarPopup(True)
        self.start_input.setDisplayFormat("yyyy-MM-dd")
        self.start_input.setDate(QDate.currentDate().addDays(-30))
        self.end_input = QDateEdit()
        self.end_input.setCalendarPopup(True)
        self.end_input.setDisplayFormat("yyyy-MM-dd")
        self.end_input.setDate(QDate.currentDate())
        self.controls_layout.addWidget(QLabel("From:"))
        self.controls_layout.addWidget(self.start_input)
        self.controls_layout.addWidget(QLabel("To:"))
        self.controls_layout.addWidget(self.end_input)
        refresh_btn = QPushButton("🔄  Refresh")
        refresh_btn.clicked.connect(self.refresh)
        self.controls_layout.addWidget(refresh_btn)
        self.controls_layout.addStretch()
        self.refresh()

    def refresh(self):
        start = self.start_input.date().toString("yyyy-MM-dd")
        end = self.end_input.date().toString("yyyy-MM-dd")
        data = reports.profit_loss_summary(start, end)
        headers = ["Medicine", "Qty Sold", "Revenue", "Cost", "Profit"]
        rows = [
            [b["medicine"], b["quantity"], f"{b['revenue']:.2f}", f"{b['cost']:.2f}", f"{b['profit']:.2f}"]
            for b in data["breakdown"]
        ]
        summary = (
            f"Profit & Loss — {start} to {end}   |   Revenue: {data['revenue']:.2f}   "
            f"Cost: {data['cost']:.2f}   Profit: {data['profit']:.2f}"
        )
        self._set_data(f"Profit and Loss {start}_to_{end}", headers, rows, summary)


class ExpiryStockTab(_ReportTab):
    def __init__(self):
        super().__init__()
        self.days_input = QSpinBox()
        self.days_input.setRange(1, 3650)
        self.days_input.setValue(90)
        self.controls_layout.addWidget(QLabel("Within (days):"))
        self.controls_layout.addWidget(self.days_input)
        refresh_btn = QPushButton("🔄  Refresh")
        refresh_btn.clicked.connect(self.refresh)
        self.controls_layout.addWidget(refresh_btn)
        self.controls_layout.addStretch()
        self.refresh()

    def refresh(self):
        within_days = self.days_input.value()
        data = reports.expiry_stock_report(within_days)
        headers = ["Medicine", "Batch No.", "Expiry Date", "Qty", "Supplier", "Days to Expiry"]
        rows = [
            [d["name"], d["batch_no"], d["expiry_date"], d["quantity"], d["supplier_name"] or "", d["days_to_expiry"]]
            for d in data
        ]
        summary = f"Expiry-wise Stock — within {within_days} days   |   {len(data)} medicine(s)"
        self._set_data(f"Expiry Stock Report {within_days}d", headers, rows, summary)


class DeadStockTab(_ReportTab):
    def __init__(self):
        super().__init__()
        self.days_input = QSpinBox()
        self.days_input.setRange(1, 3650)
        self.days_input.setValue(90)
        self.controls_layout.addWidget(QLabel("No sales in (days):"))
        self.controls_layout.addWidget(self.days_input)
        refresh_btn = QPushButton("🔄  Refresh")
        refresh_btn.clicked.connect(self.refresh)
        self.controls_layout.addWidget(refresh_btn)
        self.controls_layout.addStretch()
        self.refresh()

    def refresh(self):
        days = self.days_input.value()
        data = reports.dead_stock_report(days)
        headers = ["Medicine", "Category", "Qty In Stock", "Expiry Date", "Last Sold"]
        rows = [
            [d["name"], d["category"] or "", d["quantity"], d["expiry_date"] or "", d["last_sold"] or "Never"]
            for d in data
        ]
        summary = f"Dead Stock — no sales in {days} days   |   {len(data)} medicine(s) tying up stock"
        self._set_data(f"Dead Stock Report {days}d", headers, rows, summary)


class BestSellersTab(_ReportTab):
    def __init__(self):
        super().__init__()
        self.start_input = QDateEdit()
        self.start_input.setCalendarPopup(True)
        self.start_input.setDisplayFormat("yyyy-MM-dd")
        self.start_input.setDate(QDate.currentDate().addDays(-30))
        self.end_input = QDateEdit()
        self.end_input.setCalendarPopup(True)
        self.end_input.setDisplayFormat("yyyy-MM-dd")
        self.end_input.setDate(QDate.currentDate())
        self.controls_layout.addWidget(QLabel("From:"))
        self.controls_layout.addWidget(self.start_input)
        self.controls_layout.addWidget(QLabel("To:"))
        self.controls_layout.addWidget(self.end_input)
        refresh_btn = QPushButton("🔄  Refresh")
        refresh_btn.clicked.connect(self.refresh)
        self.controls_layout.addWidget(refresh_btn)
        self.controls_layout.addStretch()
        self.refresh()

    def refresh(self):
        start = self.start_input.date().toString("yyyy-MM-dd")
        end = self.end_input.date().toString("yyyy-MM-dd")
        data = reports.best_sellers_report(start, end, limit=50)
        headers = ["Medicine", "Category", "Qty Sold", "Revenue", "Sales Count"]
        rows = [
            [d["name"], d["category"] or "", d["quantity_sold"], f"{d['revenue']:.2f}", d["sale_count"]]
            for d in data
        ]
        summary = f"Best Sellers — {start} to {end}   |   Top {len(data)} medicine(s) by quantity sold"
        self._set_data(f"Best Sellers {start}_to_{end}", headers, rows, summary)


class ControlledSubstancesTab(_ReportTab):
    def __init__(self):
        super().__init__()
        self.start_input = QDateEdit()
        self.start_input.setCalendarPopup(True)
        self.start_input.setDisplayFormat("yyyy-MM-dd")
        self.start_input.setDate(QDate.currentDate().addDays(-30))
        self.end_input = QDateEdit()
        self.end_input.setCalendarPopup(True)
        self.end_input.setDisplayFormat("yyyy-MM-dd")
        self.end_input.setDate(QDate.currentDate())
        self.controls_layout.addWidget(QLabel("From:"))
        self.controls_layout.addWidget(self.start_input)
        self.controls_layout.addWidget(QLabel("To:"))
        self.controls_layout.addWidget(self.end_input)
        refresh_btn = QPushButton("🔄  Refresh")
        refresh_btn.clicked.connect(self.refresh)
        self.controls_layout.addWidget(refresh_btn)
        self.controls_layout.addStretch()
        self.refresh()

    def refresh(self):
        start = self.start_input.date().toString("yyyy-MM-dd")
        end = self.end_input.date().toString("yyyy-MM-dd")
        data = reports.controlled_substances_report(start, end)
        headers = ["Date", "Invoice No.", "Medicine", "Batch No.", "Qty", "Customer", "Phone", "Cashier", "Doctor"]
        rows = [
            [d["date"], d["invoice_no"], d["medicine_name"], d["batch_no"] or "", d["quantity"],
             d["customer_name"], d["customer_phone"], d["cashier_name"], d["doctor_name"] or ""]
            for d in data
        ]
        summary = (
            f"Controlled Substances Register — {start} to {end}   |   {len(data)} entries "
            "(DRAP SRO 808(I)/2001 documentation)"
        )
        self._set_data(f"Controlled Substances Register {start}_to_{end}", headers, rows, summary)


class CustomerCreditTab(_ReportTab):
    def __init__(self):
        super().__init__()
        refresh_btn = QPushButton("🔄  Refresh")
        refresh_btn.clicked.connect(self.refresh)
        self.controls_layout.addWidget(refresh_btn)
        self.controls_layout.addStretch()
        self.refresh()

    def refresh(self):
        data = reports.customer_credit_report()
        headers = ["Customer", "Phone", "Outstanding Balance"]
        rows = [[d["name"], d["phone"] or "", f"{d['credit_balance']:.2f}"] for d in data]
        total_owed = sum(d["credit_balance"] for d in data)
        summary = f"Udhaar (Credit) Summary   |   {len(data)} customer(s) owe a total of {total_owed:.2f}"
        self._set_data("Udhaar Credit Summary", headers, rows, summary)


class SupplierPayableTab(_ReportTab):
    """The mirror of the Udhaar Summary tab: what the shop owes suppliers."""

    def __init__(self):
        super().__init__()
        refresh_btn = QPushButton("🔄  Refresh")
        refresh_btn.clicked.connect(self.refresh)
        self.controls_layout.addWidget(refresh_btn)
        self.controls_layout.addStretch()
        self.refresh()

    def refresh(self):
        data = reports.supplier_payable_report()
        headers = ["Supplier", "Contact", "Outstanding Payable"]
        rows = [[d["name"], d["contact"] or "", f"{d['payable_balance']:.2f}"] for d in data]
        total_owed = sum(d["payable_balance"] for d in data)
        summary = f"Supplier Payables   |   Shop owes {len(data)} supplier(s) a total of {total_owed:.2f}"
        self._set_data("Supplier Payables Summary", headers, rows, summary)


class ReportsView(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout()
        title = QLabel("Reports")
        title.setProperty("heading", True)
        layout.addWidget(title)

        tabs = QTabWidget()
        tabs.addTab(DailySalesTab(), "Daily Sales")
        tabs.addTab(MonthlySalesTab(), "Monthly Sales")
        tabs.addTab(ProfitLossTab(), "Profit / Loss")
        tabs.addTab(ExpiryStockTab(), "Expiry Stock")
        tabs.addTab(DeadStockTab(), "Dead Stock")
        tabs.addTab(BestSellersTab(), "Best Sellers")
        tabs.addTab(CustomerCreditTab(), "Udhaar Summary")
        tabs.addTab(SupplierPayableTab(), "Supplier Payables")
        tabs.addTab(ControlledSubstancesTab(), "Controlled Substances")
        layout.addWidget(tabs)

        self.tabs = tabs
        # Switching sub-tabs WITHIN Reports (without ever leaving the Reports
        # page, so MainWindow's own switch-page refresh never fires) must
        # also show live data -- otherwise a tab you land on could still be
        # showing whatever it had at construction time.
        tabs.currentChanged.connect(lambda _index: self.refresh())
        self.setLayout(layout)

    def refresh(self):
        """Called whenever this screen (or one of its sub-tabs) becomes
        visible; refreshes just the currently-open sub-tab (each tab already
        has its own manual Refresh button for expensive re-queries the user
        doesn't want to repeat)."""
        current = self.tabs.currentWidget()
        if current is not None:
            current.refresh()
