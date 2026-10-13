"""Supplier management screen: add/edit/delete suppliers, record purchase
orders (receive new stock into an existing medicine's batch/expiry/quantity),
and track/pay down accounts payable (the mirror of customer udhaar)."""
from PySide6.QtGui import QColor
from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateEdit,
    QDialog,
    QDoubleSpinBox,
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

from logic import auth, inventory, suppliers
from logic.sales import PAYMENT_METHOD_LABELS

PAYABLE_COLOR = QColor("#FFF3CD")


class SupplierEditDialog(QDialog):
    def __init__(self, parent=None, supplier: suppliers.Supplier = None):
        super().__init__(parent)
        self.supplier = supplier
        self.setWindowTitle("Edit Supplier" if supplier else "Add Supplier")
        self.setMinimumWidth(360)

        layout = QFormLayout()
        self.name_input = QLineEdit()
        self.contact_input = QLineEdit()
        self.address_input = QLineEdit()
        if supplier:
            self.name_input.setText(supplier.name)
            self.contact_input.setText(supplier.contact)
            self.address_input.setText(supplier.address)
        layout.addRow("Name *", self.name_input)
        layout.addRow("Contact", self.contact_input)
        layout.addRow("Address", self.address_input)

        buttons = QHBoxLayout()
        save_btn = QPushButton("💾  Save")
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
            if self.supplier is None:
                suppliers.add_supplier(self.name_input.text(), self.contact_input.text(), self.address_input.text())
            else:
                suppliers.update_supplier(
                    self.supplier.id, self.name_input.text(), self.contact_input.text(), self.address_input.text()
                )
            self.accept()
        except ValueError as e:
            QMessageBox.warning(self, "Cannot save", str(e))
        except Exception as e:
            QMessageBox.critical(self, "Cannot save", f"An unexpected error occurred:\n{e}")


class PurchaseOrderDialog(QDialog):
    """Receive new stock from a supplier into an existing medicine, optionally
    on credit (the unpaid part becomes payable to that medicine's supplier)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Record Purchase Order (Receive Stock)")
        self.setMinimumWidth(400)
        self._medicines = inventory.list_medicines()

        layout = QFormLayout()
        self.medicine_input = QComboBox()
        for m in self._medicines:
            self.medicine_input.addItem(f"{m.name} (current stock: {m.quantity})", m.id)
        self.medicine_input.currentIndexChanged.connect(self._on_medicine_changed)

        self.quantity_input = QSpinBox()
        self.quantity_input.setRange(1, 1_000_000)
        self.quantity_input.valueChanged.connect(self._recalculate_total)

        self.batch_input = QLineEdit()

        self.expiry_input = QDateEdit()
        self.expiry_input.setCalendarPopup(True)
        self.expiry_input.setDisplayFormat("yyyy-MM-dd")
        self.expiry_input.setDate(QDate.currentDate())

        self.unit_cost_input = QDoubleSpinBox()
        self.unit_cost_input.setRange(0, 1_000_000)
        self.unit_cost_input.setDecimals(2)
        self.unit_cost_input.setToolTip("Leave as-is to keep the medicine's current purchase price.")
        self.unit_cost_input.valueChanged.connect(self._recalculate_total)

        self.total_cost_label = QLabel("Total cost: 0.00")
        self.total_cost_label.setProperty("heading", True)

        self.amount_paid_input = QDoubleSpinBox()
        self.amount_paid_input.setRange(0, 100_000_000)
        self.amount_paid_input.setDecimals(2)
        self.amount_paid_input.setToolTip(
            "The unpaid remainder is added to this medicine's supplier as payable (accounts payable)."
        )

        layout.addRow("Medicine *", self.medicine_input)
        layout.addRow("Quantity received *", self.quantity_input)
        layout.addRow("New batch no.", self.batch_input)
        layout.addRow("New expiry date", self.expiry_input)
        layout.addRow("Unit cost", self.unit_cost_input)
        layout.addRow(self.total_cost_label)
        layout.addRow("Amount Paid Now", self.amount_paid_input)

        buttons = QHBoxLayout()
        save_btn = QPushButton("📥  Receive Stock")
        save_btn.setProperty("success", True)
        save_btn.clicked.connect(self._save)
        cancel_btn = QPushButton("✖️  Cancel")
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(save_btn)
        buttons.addWidget(cancel_btn)
        layout.addRow(buttons)
        self.setLayout(layout)

        self._on_medicine_changed()

    def _current_medicine(self):
        med_id = self.medicine_input.currentData()
        return next((m for m in self._medicines if m.id == med_id), None)

    def _on_medicine_changed(self):
        """Reset the unit cost to the newly-selected medicine's own purchase
        price -- only when switching medicines, not on every quantity/price
        tweak, otherwise a manually-typed custom unit cost would keep getting
        silently overwritten."""
        med = self._current_medicine()
        if med is None:
            return
        self.unit_cost_input.blockSignals(True)
        self.unit_cost_input.setValue(med.purchase_price)
        self.unit_cost_input.blockSignals(False)
        self._recalculate_total()

    def _recalculate_total(self):
        total = round(self.unit_cost_input.value() * self.quantity_input.value(), 2)
        self.total_cost_label.setText(f"Total cost: {total:.2f}")
        self.amount_paid_input.setMaximum(max(total, 0.0))
        self.amount_paid_input.blockSignals(True)
        self.amount_paid_input.setValue(total)
        self.amount_paid_input.blockSignals(False)

    def _save(self):
        if self.medicine_input.count() == 0:
            QMessageBox.warning(self, "No medicines", "Add a medicine in Inventory first.")
            return
        med = self._current_medicine()
        try:
            self.result_info = suppliers.record_purchase_order(
                medicine_id=self.medicine_input.currentData(),
                quantity=self.quantity_input.value(),
                new_batch_no=self.batch_input.text(),
                new_expiry_date=self.expiry_input.date().toString("yyyy-MM-dd"),
                new_purchase_price=self.unit_cost_input.value() if med else None,
                amount_paid=self.amount_paid_input.value(),
                recorded_by=self.parent().current_user.id,
            )
            self.accept()
        except ValueError as e:
            QMessageBox.warning(self, "Cannot save", str(e))
        except Exception as e:
            QMessageBox.critical(self, "Cannot save", f"An unexpected error occurred:\n{e}")


class SupplierPaymentHistoryDialog(QDialog):
    def __init__(self, parent, supplier: suppliers.Supplier):
        super().__init__(parent)
        self.setWindowTitle(f"Payment History — {supplier.name}")
        self.setMinimumSize(460, 360)
        layout = QVBoxLayout()

        balance_label = QLabel(f"Outstanding payable: {supplier.payable_balance:.2f}")
        balance_label.setProperty("warning" if supplier.has_payable else "subheading", True)
        layout.addWidget(balance_label)

        table = QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(["Date", "Amount", "Method", "Note"])
        table.setColumnWidth(1, 80)
        table.setColumnWidth(2, 100)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.verticalHeader().setVisible(False)

        payments = suppliers.payment_history(supplier.id)
        table.setRowCount(len(payments))
        for row, p in enumerate(payments):
            table.setItem(row, 0, QTableWidgetItem(p["date"]))
            table.setItem(row, 1, QTableWidgetItem(f"{p['amount']:.2f}"))
            table.setItem(row, 2, QTableWidgetItem(PAYMENT_METHOD_LABELS.get(p["payment_method"], p["payment_method"])))
            table.setItem(row, 3, QTableWidgetItem(p["note"] or ""))
        layout.addWidget(table)
        if not payments:
            layout.addWidget(QLabel("No payments recorded to this supplier yet."))

        self.setLayout(layout)


class RecordSupplierPaymentDialog(QDialog):
    """Record the shop paying down some or all of what it owes a supplier."""

    def __init__(self, parent, supplier: suppliers.Supplier, recorded_by: int):
        super().__init__(parent)
        self.supplier = supplier
        self.recorded_by = recorded_by
        self.setWindowTitle(f"Record Payment — {supplier.name}")
        self.setMinimumWidth(360)

        layout = QFormLayout()
        balance_label = QLabel(f"Outstanding payable: {supplier.payable_balance:.2f}")
        balance_label.setProperty("warning", True)
        layout.addRow(balance_label)

        self.amount_input = QDoubleSpinBox()
        self.amount_input.setRange(0.01, supplier.payable_balance)
        self.amount_input.setDecimals(2)
        self.amount_input.setValue(supplier.payable_balance)
        layout.addRow("Amount paid *", self.amount_input)

        self.method_input = QComboBox()
        for value, label in PAYMENT_METHOD_LABELS.items():
            if value != "udhaar":
                self.method_input.addItem(label, value)
        layout.addRow("Payment method", self.method_input)

        self.note_input = QLineEdit()
        layout.addRow("Note (optional)", self.note_input)

        buttons = QHBoxLayout()
        save_btn = QPushButton("💾  Record Payment")
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
            suppliers.record_payment(
                self.supplier.id, self.amount_input.value(), self.method_input.currentData(),
                self.recorded_by, self.note_input.text(),
            )
            self.accept()
        except ValueError as e:
            QMessageBox.warning(self, "Cannot record payment", str(e))
        except Exception as e:
            QMessageBox.critical(self, "Cannot record payment", f"An unexpected error occurred:\n{e}")


class SuppliersView(QWidget):
    def __init__(self, current_user: auth.User):
        super().__init__()
        self.current_user = current_user
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout()

        header = QHBoxLayout()
        title = QLabel("Supplier Management")
        title.setProperty("heading", True)
        header.addWidget(title)
        header.addStretch()
        po_btn = QPushButton("📦  Purchase Order")
        po_btn.clicked.connect(self._record_purchase_order)
        header.addWidget(po_btn)
        add_btn = QPushButton("➕  Add Supplier")
        add_btn.setProperty("success", True)
        add_btn.clicked.connect(self._add_supplier)
        header.addWidget(add_btn)
        layout.addLayout(header)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Name", "Contact", "Address", "Payable Balance", "Actions"])
        self.table.setColumnWidth(0, 180)
        self.table.setColumnWidth(1, 120)
        self.table.setColumnWidth(3, 120)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table)

        self.setLayout(layout)

    def refresh(self):
        self.table.setUpdatesEnabled(False)
        try:
            self._refresh_impl()
        finally:
            self.table.setUpdatesEnabled(True)

    def _refresh_impl(self):
        rows = suppliers.list_suppliers()
        self.table.setRowCount(len(rows))
        for row, s in enumerate(rows):
            self.table.setItem(row, 0, QTableWidgetItem(s.name))
            self.table.setItem(row, 1, QTableWidgetItem(s.contact))
            self.table.setItem(row, 2, QTableWidgetItem(s.address))
            balance_item = QTableWidgetItem(f"{s.payable_balance:.2f}" if s.has_payable else "-")
            if s.has_payable:
                balance_item.setBackground(PAYABLE_COLOR)
            self.table.setItem(row, 3, balance_item)

            actions = QWidget()
            actions_layout = QHBoxLayout()
            actions_layout.setContentsMargins(4, 2, 4, 2)
            actions_layout.setSpacing(6)
            history_btn = QPushButton("📜  History")
            history_btn.setProperty("compact", True)
            history_btn.clicked.connect(lambda _, sup=s: self._show_history(sup))
            actions_layout.addWidget(history_btn)
            if s.has_payable:
                pay_btn = QPushButton("💰  Record Payment")
                pay_btn.setProperty("compact", True)
                pay_btn.setProperty("success", True)
                pay_btn.clicked.connect(lambda _, sup=s: self._record_payment(sup))
                actions_layout.addWidget(pay_btn)
            edit_btn = QPushButton("✏️  Edit")
            edit_btn.setProperty("compact", True)
            edit_btn.clicked.connect(lambda _, sup=s: self._edit_supplier(sup))
            delete_btn = QPushButton("🗑️  Delete")
            delete_btn.setProperty("compact", True)
            delete_btn.setProperty("danger", True)
            delete_btn.clicked.connect(lambda _, sup=s: self._delete_supplier(sup))
            actions_layout.addWidget(edit_btn)
            actions_layout.addWidget(delete_btn)
            actions.setLayout(actions_layout)
            self.table.setCellWidget(row, 4, actions)

    def _add_supplier(self):
        dialog = SupplierEditDialog(self)
        if dialog.exec() == QDialog.Accepted:
            self.refresh()

    def _edit_supplier(self, supplier: suppliers.Supplier):
        dialog = SupplierEditDialog(self, supplier=supplier)
        if dialog.exec() == QDialog.Accepted:
            self.refresh()

    def _show_history(self, supplier: suppliers.Supplier):
        SupplierPaymentHistoryDialog(self, supplier).exec()

    def _record_payment(self, supplier: suppliers.Supplier):
        dialog = RecordSupplierPaymentDialog(self, supplier, self.current_user.id)
        if dialog.exec() == QDialog.Accepted:
            self.refresh()

    def _delete_supplier(self, supplier: suppliers.Supplier):
        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Delete supplier '{supplier.name}'?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            suppliers.delete_supplier(supplier.id)
            self.refresh()
        except ValueError as e:
            QMessageBox.warning(self, "Cannot delete", str(e))

    def _record_purchase_order(self):
        dialog = PurchaseOrderDialog(self)
        if dialog.exec() == QDialog.Accepted:
            info = dialog.result_info
            msg = f"Purchase order recorded and stock updated.\n\nTotal cost: {info['total_cost']:.2f}"
            if info["payable_added"] > 0.005:
                msg += f"\n📒 Added to supplier's payable: {info['payable_added']:.2f}"
            QMessageBox.information(self, "Stock received", msg)
            self.refresh()
