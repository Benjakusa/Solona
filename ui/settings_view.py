"""Settings screen (admin only): configurable alert thresholds + user management,
matching the 'Settings' item in the sidebar spec."""
from ui.icons import icon

from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
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
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from logic import audit, auth, settings
from ui.users_view import UsersView


class GeneralSettingsTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QFormLayout()

        self.low_stock_input = QSpinBox()
        self.low_stock_input.setRange(0, 100_000)
        self.low_stock_input.setMaximumWidth(160)
        self.low_stock_input.setValue(settings.get_low_stock_threshold())
        layout.addRow("Default low-stock threshold (units)", self.low_stock_input)

        self.expiry_days_input = QSpinBox()
        self.expiry_days_input.setRange(1, 3650)
        self.expiry_days_input.setMaximumWidth(160)
        self.expiry_days_input.setValue(settings.get_expiry_alert_days())
        layout.addRow("Expiry alert window (days)", self.expiry_days_input)

        self.shop_name_input = QLineEdit()
        self.shop_name_input.setText(settings.get_shop_name())
        layout.addRow("Shop name (shown on receipts)", self.shop_name_input)

        self.receipt_format_input = QComboBox()
        for value, label in settings.RECEIPT_FORMATS.items():
            self.receipt_format_input.addItem(label, value)
        idx = self.receipt_format_input.findData(settings.get_receipt_format())
        self.receipt_format_input.setCurrentIndex(idx if idx >= 0 else 0)
        self.receipt_format_input.setMaximumWidth(220)
        layout.addRow("Receipt/invoice format", self.receipt_format_input)

        save_btn = QPushButton("Save Settings")

        save_btn.setIcon(icon(save_btn, "save"))
        save_btn.setProperty("success", True)
        save_btn.clicked.connect(self._save)
        save_row = QHBoxLayout()
        save_row.addWidget(save_btn)
        save_row.addStretch()
        layout.addRow(save_row)

        note = QLabel(
            "The low-stock threshold applies to newly added medicines by default; "
            "each medicine's own threshold can still be adjusted individually in Inventory."
        )
        note.setProperty("subheading", True)
        note.setWordWrap(True)
        layout.addRow(note)

        self.setLayout(layout)

    def _save(self):
        settings.set_setting("low_stock_threshold", str(self.low_stock_input.value()))
        settings.set_setting("expiry_alert_days", str(self.expiry_days_input.value()))
        settings.set_setting("shop_name", self.shop_name_input.text().strip() or "SOLONA Pharmacy")
        settings.set_setting("receipt_format", self.receipt_format_input.currentData())
        QMessageBox.information(self, "Saved", "Settings saved successfully.")


class RecoveryPinTab(QWidget):
    """Lets the shop owner set/change the PIN required by the login screen's
    'Forgot admin password?' button, so resetting admin access needs more
    than just physical access to the PC (e.g. a cashier at the till)."""

    def __init__(self):
        super().__init__()
        layout = QFormLayout()

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        layout.addRow(self.status_label)

        self.new_pin_input = QLineEdit()
        self.new_pin_input.setEchoMode(QLineEdit.Password)
        self.new_pin_input.setMaximumWidth(220)
        layout.addRow("New Recovery PIN", self.new_pin_input)

        self.confirm_pin_input = QLineEdit()
        self.confirm_pin_input.setEchoMode(QLineEdit.Password)
        self.confirm_pin_input.setMaximumWidth(220)
        layout.addRow("Confirm PIN", self.confirm_pin_input)

        save_btn = QPushButton("Set Recovery PIN")

        save_btn.setIcon(icon(save_btn, "save"))
        save_btn.setProperty("success", True)
        save_btn.clicked.connect(self._save)
        save_row = QHBoxLayout()
        save_row.addWidget(save_btn)
        save_row.addStretch()
        layout.addRow(save_row)

        note = QLabel(
            "Set a recovery passphrase of at least 12 characters. It is required before the login screen can reset the administrator password. "
            "Keep it separate from your login password. There is no developer master PIN; "
            "if the PIN is lost, use your documented business recovery process and a verified database backup."
        )
        note.setProperty("subheading", True)
        note.setWordWrap(True)
        layout.addRow(note)

        self.setLayout(layout)
        self._refresh_status()

    def _refresh_status(self):
        if auth.has_recovery_pin():
            self.status_label.setText("  A Recovery PIN is currently set.")
        else:
            self.status_label.setText(
                "No Recovery PIN is set. Password recovery is disabled until the shop owner configures one."
            )

    def _save(self):
        pin = self.new_pin_input.text()
        if pin != self.confirm_pin_input.text():
            QMessageBox.warning(self, "PIN Mismatch", "The two PINs entered do not match.")
            return
        try:
            auth.set_recovery_pin(pin)
        except ValueError as e:
            QMessageBox.warning(self, "Cannot Set PIN", str(e))
            return
        self.new_pin_input.clear()
        self.confirm_pin_input.clear()
        self._refresh_status()
        QMessageBox.information(self, "Saved", "Recovery PIN updated successfully.")


class AuditLogTab(QWidget):
    """Accountability trail: who deleted/changed what, and when -- for a
    shop with more than one staff member using the till."""

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout()

        header = QHBoxLayout()
        header.addWidget(QLabel("Recent activity (most recent first)"))
        header.addStretch()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.setIcon(icon(refresh_btn, "refresh"))
        refresh_btn.clicked.connect(self.refresh)
        header.addWidget(refresh_btn)
        layout.addLayout(header)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Timestamp", "User", "Action", "Details"])
        self.table.setColumnWidth(1, 120)
        self.table.setColumnWidth(2, 160)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table)

        self.setLayout(layout)
        self.refresh()

    def refresh(self):
        entries = audit.list_entries()
        self.table.setRowCount(len(entries))
        for row, e in enumerate(entries):
            self.table.setItem(row, 0, QTableWidgetItem(e.timestamp))
            self.table.setItem(row, 1, QTableWidgetItem(e.username))
            self.table.setItem(row, 2, QTableWidgetItem(e.action))
            self.table.setItem(row, 3, QTableWidgetItem(e.details))


class SettingsView(QWidget):
    def __init__(self, current_user: auth.User):
        super().__init__()
        layout = QVBoxLayout()
        title = QLabel("Settings")
        title.setProperty("heading", True)
        layout.addWidget(title)

        tabs = QTabWidget()
        tabs.addTab(GeneralSettingsTab(), "General")
        tabs.addTab(RecoveryPinTab(), "Recovery PIN")
        tabs.addTab(UsersView(current_user), "User Management")
        tabs.addTab(AuditLogTab(), "Audit Log")
        layout.addWidget(tabs)

        self.tabs = tabs
        # Switching sub-tabs WITHIN Settings (e.g. General -> User Management)
        # without leaving the Settings page must also show live data.
        tabs.currentChanged.connect(lambda _index: self.refresh())
        self.setLayout(layout)

    def refresh(self):
        """Called whenever this screen (or one of its sub-tabs) becomes visible; refreshes just the
        currently-open sub-tab."""
        current = self.tabs.currentWidget()
        if current is not None and hasattr(current, "refresh"):
            current.refresh()
