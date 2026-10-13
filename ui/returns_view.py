"""Returns / Refunds screen: find a past sale, return specific line items
(full or partial quantity), restocking inventory and reducing the
customer's udhaar balance first if that sale wasn't fully paid."""
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from logic import audit, auth, sales


class ProcessReturnDialog(QDialog):
    def __init__(self, parent, sale_item: dict, max_returnable: int, current_user: auth.User):
        super().__init__(parent)
        self.sale_item = sale_item
        self.current_user = current_user
        self.setWindowTitle(f"Return — {sale_item['medicine_name']}")
        self.setMinimumWidth(360)

        layout = QFormLayout()
        info = QLabel(f"Sold: {sale_item['quantity']}   |   Already returned: {sale_item['returned_qty']}   |   "
                      f"Returnable now: {max_returnable}")
        info.setWordWrap(True)
        layout.addRow(info)

        self.quantity_input = QSpinBox()
        self.quantity_input.setRange(1, max_returnable)
        layout.addRow("Quantity to return *", self.quantity_input)

        self.reason_input = QLineEdit()
        self.reason_input.setPlaceholderText("e.g. customer changed mind, wrong item")
        layout.addRow("Reason", self.reason_input)

        buttons = QHBoxLayout()
        save_btn = QPushButton("↩️  Process Return")
        save_btn.setProperty("success", True)
        save_btn.clicked.connect(self._save)
        cancel_btn = QPushButton("✖️  Cancel")
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(save_btn)
        buttons.addWidget(cancel_btn)
        layout.addRow(buttons)
        self.setLayout(layout)

    def _save(self):
        try:
            self.result_info = sales.process_return(
                self.sale_item["id"], self.quantity_input.value(),
                self.reason_input.text(), self.current_user.id,
            )
            audit.log(
                self.current_user.id, self.current_user.username, "process_return",
                f"returned {self.quantity_input.value()}x '{self.sale_item['medicine_name']}' "
                f"(refund {self.result_info['refund_amount']:.2f}); reason: {self.reason_input.text() or 'n/a'}",
            )
            self.accept()
        except ValueError as e:
            QMessageBox.warning(self, "Cannot process return", str(e))
        except Exception as e:
            QMessageBox.critical(self, "Cannot process return", f"An unexpected error occurred:\n{e}")


class ReturnsView(QWidget):
    def __init__(self, current_user: auth.User):
        super().__init__()
        self.current_user = current_user
        self._current_sale_id = None
        self._build_ui()
        self.refresh_search()

    def refresh(self):
        """Called whenever this screen becomes visible, so a sale made
        elsewhere while this tab sat idle shows up as returnable immediately."""
        self.refresh_search()

    def _build_ui(self):
        layout = QVBoxLayout()
        title = QLabel("Returns / Refunds")
        title.setProperty("heading", True)
        layout.addWidget(title)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍  Search by invoice number...")
        self.search_input.textChanged.connect(self.refresh_search)
        layout.addWidget(self.search_input)

        self.sales_table = QTableWidget(0, 4)
        self.sales_table.setHorizontalHeaderLabels(["Invoice No.", "Date", "Customer", "Total"])
        self.sales_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.sales_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.sales_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.sales_table.verticalHeader().setVisible(False)
        self.sales_table.itemSelectionChanged.connect(self._on_sale_selected)
        self.sales_table.setMaximumHeight(180)
        layout.addWidget(self.sales_table)

        items_label = QLabel("Items in Selected Sale")
        items_label.setProperty("subheading", True)
        layout.addWidget(items_label)

        self.items_table = QTableWidget(0, 5)
        self.items_table.setHorizontalHeaderLabels(["Medicine", "Qty Sold", "Returned", "Unit Price", "Actions"])
        self.items_table.setColumnWidth(1, 80)
        self.items_table.setColumnWidth(2, 80)
        self.items_table.setColumnWidth(3, 90)
        self.items_table.setColumnWidth(4, 130)
        self.items_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.items_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.items_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.items_table.verticalHeader().setVisible(False)
        layout.addWidget(self.items_table)

        self.setLayout(layout)

    def refresh_search(self):
        results = sales.returnable_sales(search=self.search_input.text().strip())
        self.sales_table.setRowCount(len(results))
        self._results = results
        for row, s in enumerate(results):
            self.sales_table.setItem(row, 0, QTableWidgetItem(s["invoice_no"]))
            self.sales_table.setItem(row, 1, QTableWidgetItem(s["date"]))
            self.sales_table.setItem(row, 2, QTableWidgetItem(s["customer_name"]))
            self.sales_table.setItem(row, 3, QTableWidgetItem(f"{s['total_amount']:.2f}"))
        self.items_table.setRowCount(0)
        self._current_sale_id = None

    def _on_sale_selected(self):
        row = self.sales_table.currentRow()
        if row < 0 or row >= len(self._results):
            return
        self._current_sale_id = self._results[row]["id"]
        self._load_items()

    def _load_items(self):
        detail = sales.get_sale_with_items(self._current_sale_id)
        items = detail["items"] if detail else []
        self.items_table.setRowCount(len(items))
        for row, item in enumerate(items):
            self.items_table.setItem(row, 0, QTableWidgetItem(item["medicine_name"]))
            self.items_table.setItem(row, 1, QTableWidgetItem(str(item["quantity"])))
            self.items_table.setItem(row, 2, QTableWidgetItem(str(item["returned_qty"])))
            self.items_table.setItem(row, 3, QTableWidgetItem(f"{item['unit_price']:.2f}"))

            remaining = item["quantity"] - item["returned_qty"]
            if remaining > 0:
                return_btn = QPushButton("↩️  Return")
                return_btn.setProperty("compact", True)
                return_btn.setProperty("danger", True)
                return_btn.clicked.connect(lambda _, it=item, rem=remaining: self._process_return(it, rem))
                self.items_table.setCellWidget(row, 4, return_btn)
            else:
                done_label = QLabel("Fully returned")
                done_label.setProperty("subheading", True)
                self.items_table.setCellWidget(row, 4, done_label)

    def _process_return(self, item: dict, max_returnable: int):
        dialog = ProcessReturnDialog(self, item, max_returnable, self.current_user)
        if dialog.exec() == QDialog.Accepted:
            info = dialog.result_info
            msg = f"Return processed.\n\nRefund amount: {info['refund_amount']:.2f}"
            if info["credit_reduction"] > 0.005:
                msg += f"\nReduced customer's udhaar by: {info['credit_reduction']:.2f}"
            if info["cash_refund"] > 0.005:
                msg += f"\nCash to hand back to customer: {info['cash_refund']:.2f}"
            QMessageBox.information(self, "Return Complete", msg)
            self._load_items()
            self.refresh_search()
