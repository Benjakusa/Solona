"""Inventory management screen: searchable/filterable table with add/edit/delete.

Cashiers get a read-only view (per spec: cashiers can view stock but not
edit/delete); admins get full CRUD.
"""
import os

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
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

from logic import audit, auth, barcode_utils, inventory, settings, suppliers

LOW_STOCK_COLOR = QColor("#FFF3CD")
EXPIRING_COLOR = QColor("#F8D7DA")


class MedicineEditDialog(QDialog):
    def __init__(self, parent=None, medicine: inventory.Medicine = None):
        super().__init__(parent)
        self.medicine = medicine
        self.setWindowTitle("Edit Medicine" if medicine else "Add Medicine")
        self.setMinimumWidth(420)
        self._build_ui()

    def _build_ui(self):
        layout = QFormLayout()

        self.name_input = QLineEdit()
        self.generic_input = QLineEdit()
        self.manufacturer_input = QLineEdit()
        self.manufacturer_input.setPlaceholderText("e.g. GSK, Getz Pharma (optional)")
        self.category_input = QLineEdit()
        self.barcode_input = QLineEdit()
        self.barcode_input.setPlaceholderText("Optional")
        self.batch_input = QLineEdit()

        self.expiry_input = QDateEdit()
        self.expiry_input.setCalendarPopup(True)
        self.expiry_input.setDisplayFormat("yyyy-MM-dd")
        self.expiry_input.setDate(QDate.currentDate())

        self.quantity_input = QSpinBox()
        self.quantity_input.setRange(0, 1_000_000)

        self.units_per_pack_input = QSpinBox()
        self.units_per_pack_input.setRange(1, 10_000)
        self.units_per_pack_input.setValue(1)
        self.units_per_pack_input.setToolTip(
            "How many of this medicine come in one strip/box/pack -- e.g. 10 for a "
            "strip of 10 tablets. Quantity above is always counted in this smallest "
            "unit; this just lets Billing quick-add a whole pack at once."
        )

        self.purchase_price_input = QDoubleSpinBox()
        self.purchase_price_input.setRange(0, 1_000_000)
        self.purchase_price_input.setDecimals(2)

        self.sale_price_input = QDoubleSpinBox()
        self.sale_price_input.setRange(0, 1_000_000)
        self.sale_price_input.setDecimals(2)

        self.threshold_input = QSpinBox()
        self.threshold_input.setRange(0, 100_000)
        self.threshold_input.setValue(settings.get_low_stock_threshold())

        self.supplier_input = QComboBox()
        self.supplier_input.addItem("(None)", None)
        for s in suppliers.list_suppliers():
            self.supplier_input.addItem(s.name, s.id)

        self.controlled_substance_input = QCheckBox(
            "Narcotic/psychotropic -- requires DRAP register documentation"
        )

        if self.medicine:
            self.name_input.setText(self.medicine.name)
            self.generic_input.setText(self.medicine.generic_name)
            self.manufacturer_input.setText(self.medicine.manufacturer)
            self.category_input.setText(self.medicine.category)
            self.barcode_input.setText(self.medicine.barcode)
            self.batch_input.setText(self.medicine.batch_no)
            if self.medicine.expiry_date:
                self.expiry_input.setDate(QDate.fromString(self.medicine.expiry_date, "yyyy-MM-dd"))
            self.quantity_input.setValue(self.medicine.quantity)
            self.units_per_pack_input.setValue(self.medicine.units_per_pack)
            self.purchase_price_input.setValue(self.medicine.purchase_price)
            self.sale_price_input.setValue(self.medicine.sale_price)
            self.threshold_input.setValue(self.medicine.low_stock_threshold)
            self.controlled_substance_input.setChecked(self.medicine.is_controlled_substance)
            if self.medicine.supplier_id:
                idx = self.supplier_input.findData(self.medicine.supplier_id)
                if idx >= 0:
                    self.supplier_input.setCurrentIndex(idx)

        layout.addRow("Name *", self.name_input)
        layout.addRow("Generic name", self.generic_input)
        layout.addRow("Manufacturer", self.manufacturer_input)
        layout.addRow("Category", self.category_input)
        layout.addRow("", self.controlled_substance_input)

        barcode_row = QHBoxLayout()
        barcode_row.addWidget(self.barcode_input)
        generate_btn = QPushButton("🏷️  Generate")
        generate_btn.setProperty("compact", True)
        generate_btn.clicked.connect(self._generate_barcode)
        barcode_row.addWidget(generate_btn)
        print_label_btn = QPushButton("🖨️  Print Label")
        print_label_btn.setProperty("compact", True)
        print_label_btn.clicked.connect(self._print_label)
        barcode_row.addWidget(print_label_btn)
        layout.addRow("Barcode", barcode_row)

        layout.addRow("Batch No.", self.batch_input)
        layout.addRow("Expiry date", self.expiry_input)
        layout.addRow("Quantity *", self.quantity_input)
        layout.addRow("Units per pack (tablets/strip)", self.units_per_pack_input)
        layout.addRow("Purchase price *", self.purchase_price_input)
        layout.addRow("Sale price *", self.sale_price_input)
        layout.addRow("Low-stock threshold", self.threshold_input)
        layout.addRow("Supplier", self.supplier_input)

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
            expiry_str = self.expiry_input.date().toString("yyyy-MM-dd")
            supplier_id = self.supplier_input.currentData()
            if self.medicine is None:
                inventory.add_medicine(
                    name=self.name_input.text(),
                    generic_name=self.generic_input.text(),
                    manufacturer=self.manufacturer_input.text(),
                    category=self.category_input.text(),
                    batch_no=self.batch_input.text(),
                    expiry_date=expiry_str,
                    quantity=self.quantity_input.value(),
                    units_per_pack=self.units_per_pack_input.value(),
                    purchase_price=self.purchase_price_input.value(),
                    sale_price=self.sale_price_input.value(),
                    supplier_id=supplier_id,
                    low_stock_threshold=self.threshold_input.value(),
                    barcode=self.barcode_input.text(),
                    is_controlled_substance=self.controlled_substance_input.isChecked(),
                    actor_id=self.parent().current_user.id,
                )
            else:
                inventory.update_medicine(
                    medicine_id=self.medicine.id,
                    name=self.name_input.text(),
                    generic_name=self.generic_input.text(),
                    manufacturer=self.manufacturer_input.text(),
                    category=self.category_input.text(),
                    batch_no=self.batch_input.text(),
                    expiry_date=expiry_str,
                    quantity=self.quantity_input.value(),
                    units_per_pack=self.units_per_pack_input.value(),
                    purchase_price=self.purchase_price_input.value(),
                    sale_price=self.sale_price_input.value(),
                    supplier_id=supplier_id,
                    low_stock_threshold=self.threshold_input.value(),
                    barcode=self.barcode_input.text(),
                    is_controlled_substance=self.controlled_substance_input.isChecked(),
                    actor_id=self.parent().current_user.id,
                )
            self.accept()
        except ValueError as e:
            QMessageBox.warning(self, "Cannot save", str(e))
        except Exception as e:
            QMessageBox.critical(self, "Cannot save", f"An unexpected error occurred:\n{e}")

    def _generate_barcode(self):
        self.barcode_input.setText(barcode_utils.generate_unique_barcode())

    def _print_label(self):
        code = self.barcode_input.text().strip()
        if not code:
            QMessageBox.information(
                self, "No barcode yet",
                "Generate a barcode (or type/scan one) before printing a label.",
            )
            return
        name = self.name_input.text().strip() or "Unnamed medicine"
        try:
            label_path = barcode_utils.generate_label_pdf(name, code, self.sale_price_input.value())
        except Exception as e:
            QMessageBox.critical(self, "Could not create label", str(e))
            return

        try:
            os.startfile(str(label_path))
        except Exception:
            pass  # opening the PDF viewer is a convenience, not required
        QMessageBox.information(self, "Label ready", f"Label saved to:\n{label_path}")


class InventoryView(QWidget):
    def __init__(self, current_user: auth.User):
        super().__init__()
        self.current_user = current_user
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout()

        header = QHBoxLayout()
        title = QLabel("Inventory Management")
        title.setProperty("heading", True)
        header.addWidget(title)
        header.addStretch()
        if self.current_user.is_admin:
            add_btn = QPushButton("➕  Add Medicine")
            add_btn.setProperty("success", True)
            add_btn.clicked.connect(self._add_medicine)
            header.addWidget(add_btn)
        layout.addLayout(header)

        filters = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍  Search by name or generic name...")
        self.search_input.textChanged.connect(self.refresh)
        filters.addWidget(self.search_input)

        self.category_filter = QComboBox()
        self.category_filter.addItem("All categories", "")
        self.category_filter.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.category_filter)

        self.supplier_filter = QComboBox()
        self.supplier_filter.addItem("All suppliers", None)
        self.supplier_filter.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.supplier_filter)

        layout.addLayout(filters)

        self.alert_label = QLabel("")
        self.alert_label.setProperty("warning", True)
        self.alert_label.setWordWrap(True)
        layout.addWidget(self.alert_label)

        columns = ["Name", "Generic", "Manufacturer", "Category", "Barcode", "Batch", "Expiry", "Qty", "Sale", "Supplier"]
        if self.current_user.is_admin:
            columns.insert(8, "Purchase")  # cost price -- admin only, hidden from cashiers
            columns.append("Actions")
        self.table = QTableWidget(0, len(columns))
        self.table.setHorizontalHeaderLabels(columns)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        if self.current_user.is_admin:
            self.table.horizontalHeader().setSectionResizeMode(len(columns) - 1, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table)

        self.setLayout(layout)

    def _reload_filter_options(self):
        current_cat = self.category_filter.currentData()
        self.category_filter.blockSignals(True)
        self.category_filter.clear()
        self.category_filter.addItem("All categories", "")
        for cat in inventory.list_categories():
            self.category_filter.addItem(cat, cat)
        idx = self.category_filter.findData(current_cat)
        self.category_filter.setCurrentIndex(idx if idx >= 0 else 0)
        self.category_filter.blockSignals(False)

        current_sup = self.supplier_filter.currentData()
        self.supplier_filter.blockSignals(True)
        self.supplier_filter.clear()
        self.supplier_filter.addItem("All suppliers", None)
        for s in suppliers.list_suppliers():
            self.supplier_filter.addItem(s.name, s.id)
        idx = self.supplier_filter.findData(current_sup)
        self.supplier_filter.setCurrentIndex(idx if idx >= 0 else 0)
        self.supplier_filter.blockSignals(False)

    def refresh(self):
        # Populating hundreds/thousands of rows one at a time triggers a
        # relayout/repaint on every insert unless updates are suspended --
        # this alone roughly halves load time for a large inventory.
        self.table.setUpdatesEnabled(False)
        try:
            self._refresh_impl()
        finally:
            self.table.setUpdatesEnabled(True)

    def _refresh_impl(self):
        self._reload_filter_options()
        medicines = inventory.list_medicines(
            search=self.search_input.text().strip(),
            category=self.category_filter.currentData() or "",
            supplier_id=self.supplier_filter.currentData(),
        )

        low_stock_count = sum(1 for m in medicines if m.is_low_stock)
        expiring_count = sum(1 for m in medicines if m.days_to_expiry is not None and m.days_to_expiry <= 30)
        alerts = []
        if low_stock_count:
            alerts.append(f"{low_stock_count} medicine(s) low on stock")
        if expiring_count:
            alerts.append(f"{expiring_count} medicine(s) expiring within 30 days")
        self.alert_label.setText(" | ".join(alerts))

        self.table.setRowCount(len(medicines))
        for row, m in enumerate(medicines):
            display_name = f"🔒 {m.name}" if m.is_controlled_substance else m.name
            qty_display = f"{m.quantity} (×{m.units_per_pack})" if m.units_per_pack > 1 else str(m.quantity)
            values = [
                display_name, m.generic_name, m.manufacturer, m.category, m.barcode, m.batch_no,
                m.expiry_date, qty_display,
            ]
            if self.current_user.is_admin:
                values.append(f"{m.purchase_price:.2f}")  # cost price -- admin only
            values.append(f"{m.sale_price:.2f}")
            values.append(m.supplier_name or "")
            row_color = None
            if m.is_expired or (m.days_to_expiry is not None and m.days_to_expiry <= 30):
                row_color = EXPIRING_COLOR
            elif m.is_low_stock:
                row_color = LOW_STOCK_COLOR

            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if row_color:
                    item.setBackground(row_color)
                self.table.setItem(row, col, item)

            if self.current_user.is_admin:
                actions = QWidget()
                actions_layout = QHBoxLayout()
                actions_layout.setContentsMargins(4, 2, 4, 2)
                actions_layout.setSpacing(6)
                edit_btn = QPushButton("✏️  Edit")
                edit_btn.setProperty("compact", True)
                edit_btn.clicked.connect(lambda _, med=m: self._edit_medicine(med))
                delete_btn = QPushButton("🗑️  Delete")
                delete_btn.setProperty("compact", True)
                delete_btn.setProperty("danger", True)
                delete_btn.clicked.connect(lambda _, med=m: self._delete_medicine(med))
                actions_layout.addWidget(edit_btn)
                actions_layout.addWidget(delete_btn)
                actions.setLayout(actions_layout)
                self.table.setCellWidget(row, len(values), actions)

    def _add_medicine(self):
        dialog = MedicineEditDialog(self)
        if dialog.exec() == QDialog.Accepted:
            self.refresh()

    def _edit_medicine(self, medicine: inventory.Medicine):
        dialog = MedicineEditDialog(self, medicine=medicine)
        if dialog.exec() == QDialog.Accepted:
            self.refresh()

    def _delete_medicine(self, medicine: inventory.Medicine):
        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Are you sure you want to delete '{medicine.name}'?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        inventory.delete_medicine(medicine.id, actor_id=self.current_user.id)
        audit.log(self.current_user.id, self.current_user.username, "delete_medicine",
                   f"deleted medicine '{medicine.name}' (batch {medicine.batch_no})")
        self.refresh()
