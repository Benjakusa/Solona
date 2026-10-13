"""Main application window: sidebar navigation + stacked pages.

Cashiers see Dashboard / Billing / Inventory (view-only) / Customers / Reports.
Admins additionally see Suppliers, Backup/Restore, and Settings (user management).
"""
import threading

from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import QTimer

from logic import auth, backup
from ui.backup_view import BackupView
from ui.billing_view import BillingView
from ui.customers_view import CustomersView
from ui.dashboard_view import DashboardView
from ui.inventory_view import InventoryView
from ui.reports_view import ReportsView
from ui.returns_view import ReturnsView
from ui.settings_view import SettingsView
from ui.suppliers_view import SuppliersView


class MainWindow(QMainWindow):
    def __init__(self, user: auth.User):
        super().__init__()
        self.user = user
        self.setWindowTitle("SOLONA Pharmacy")
        self.resize(1200, 750)
        # Below this, the sidebar and page content start cramming into each
        # other -- shrink-to-broken isn't a state a non-technical user should
        # be able to reach by just dragging a window edge.
        self.setMinimumSize(1100, 650)

        self.nav_buttons = []
        self._build_ui()
        if self.user.is_admin:
            conflict_button = QPushButton("Review sync conflicts")
            conflict_button.clicked.connect(self._show_sync_conflicts)
            self.statusBar().addPermanentWidget(conflict_button)
        self.sync_status_timer = QTimer(self)
        self.sync_status_timer.timeout.connect(self._refresh_sync_status)
        self.sync_status_timer.start(5000)
        self._sync_lock = threading.Lock()
        self._refresh_sync_status()
        backup.run_daily_auto_backup_if_needed()

    def _build_ui(self):
        central = QWidget()
        root_layout = QHBoxLayout()
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # --- Sidebar ---
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(220)
        sidebar_layout = QVBoxLayout()
        sidebar_layout.setContentsMargins(0, 20, 0, 20)
        sidebar_layout.setSpacing(2)

        app_title = QLabel("  Pharmacy MS")
        app_title.setProperty("heading", True)
        sidebar_layout.addWidget(app_title)

        user_label = QLabel(f"  {self.user.username} ({self.user.role})")
        user_label.setProperty("subheading", True)
        sidebar_layout.addWidget(user_label)
        sidebar_layout.addSpacing(16)

        self.stack = QStackedWidget()
        self.pages_by_name = {}

        pages = [("Dashboard", DashboardView(self.user, navigate_callback=self._navigate_to))]
        pages.append(("Billing", BillingView(self.user)))
        pages.append(("Returns", ReturnsView(self.user)))
        pages.append(("Inventory", InventoryView(self.user)))
        pages.append(("Customers", CustomersView(self.user)))
        if self.user.is_admin:
            pages.append(("Suppliers", SuppliersView(self.user)))
        pages.append(("Reports", ReportsView()))
        if self.user.is_admin:
            pages.append(("Backup / Restore", BackupView(self.user)))
            pages.append(("Settings", SettingsView(self.user)))

        nav_icons = {
            "Dashboard": "🏠",
            "Billing": "🧾",
            "Returns": "↩️",
            "Inventory": "💊",
            "Customers": "👥",
            "Suppliers": "🚚",
            "Reports": "📊",
            "Backup / Restore": "🗄️",
            "Settings": "⚙️",
        }

        for name, widget in pages:
            self.stack.addWidget(widget)
            self.pages_by_name[name] = widget
            icon = nav_icons.get(name, "")
            btn = QPushButton(f"{icon}  {name}" if icon else name)
            btn.setProperty("flat", True)
            btn.setCheckable(True)
            btn.clicked.connect(lambda _, w=widget, b=None: self._switch_page(w))
            self.nav_buttons.append(btn)
            sidebar_layout.addWidget(btn)

        sidebar_layout.addStretch()

        logout_btn = QPushButton("🚪  Logout")
        logout_btn.setProperty("danger", True)
        logout_btn.clicked.connect(self._logout)
        sidebar_layout.addWidget(logout_btn)

        sidebar.setLayout(sidebar_layout)

        root_layout.addWidget(sidebar)

        content_wrapper = QWidget()
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(24, 24, 24, 24)
        content_layout.addWidget(self.stack)
        content_wrapper.setLayout(content_layout)
        root_layout.addWidget(content_wrapper)

        central.setLayout(root_layout)
        self.setCentralWidget(central)
        self.statusBar().setStyleSheet("padding: 5px 10px;")

        if self.nav_buttons:
            self.nav_buttons[0].setChecked(True)
            self.nav_buttons[0].setProperty("active", True)

    def _switch_page(self, widget):
        self.stack.setCurrentWidget(widget)
        # Every page keeps its own state alive in the background (tabs are
        # never destroyed, just hidden), so without this a customer/medicine
        # added, edited or deleted on one screen would keep looking
        # unchanged on every other screen until its own manual Refresh
        # button was clicked -- easy to mistake for "delete/update doesn't
        # work" when it actually did, just not visibly yet.
        refresh = getattr(widget, "refresh", None)
        if callable(refresh):
            refresh()
        for btn in self.nav_buttons:
            is_active = self.stack.widget(self.nav_buttons.index(btn)) is widget
            btn.setChecked(is_active)
            btn.setProperty("active", is_active)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _navigate_to(self, name: str):
        widget = self.pages_by_name.get(name)
        if widget:
            self._switch_page(widget)

    def _logout(self):
        from ui.login_window import LoginWindow

        self.sync_status_timer.stop()
        self.login_window = LoginWindow()
        self.login_window.show()
        self.close()

    def closeEvent(self, event):
        self.sync_status_timer.stop()
        backup.run_auto_backup_on_exit()
        super().closeEvent(event)

    def _refresh_sync_status(self):
        """Report local durability and coordination honestly; never imply that
        a standalone terminal has shop-wide stock authority."""
        from database.db_manager import get_connection

        conn = get_connection()
        try:
            identity = conn.execute("SELECT * FROM sync_identity WHERE singleton=1").fetchone()
            pending = conn.execute(
                "SELECT COUNT(*) FROM sync_outbox WHERE status IN ('pending','failed','inflight')"
            ).fetchone()[0]
            conflicts = conn.execute("SELECT COUNT(*) FROM sync_conflicts WHERE status='open'").fetchone()[0]
            last_sync = identity["last_successful_sync"] or "Never"
            error = identity["last_error"]
            tenant = identity["tenant_name"] or "Local workspace"
            shop = identity["shop_name"] or "Not linked"
            device = identity["device_name"] or "This computer"
            mode = identity["coordination_mode"]
            if mode == "standalone":
                network_state = "STANDALONE — STOCK MAY BE INCONSISTENT WITH OTHER TERMINALS"
            elif mode == "lan_client":
                network_state = "SHOP SERVER CONFIGURED — availability not verified"
            elif mode == "cloud_client":
                network_state = "CLOUD SYNC CONFIGURED — connectivity checked every few seconds"
            else:
                network_state = "SHOP SERVER MODE"
            status = (
                f"{network_state}  |  {tenant} / {shop}  |  {device}  |  "
                f"Pending sync: {pending}  |  Conflicts: {conflicts}  |  Last successful sync: {last_sync}"
            )
            if error:
                status += f"  |  Sync error: {error}"
            self.statusBar().showMessage(status)
            if mode in {"lan_client", "cloud_client"} and self._sync_lock.acquire(blocking=False):
                worker = threading.Thread(target=self._sync_once, daemon=True, name="pharmacy-sync")
                worker.start()
        except Exception:
            self.statusBar().showMessage("LOCAL DATABASE STATUS UNAVAILABLE — check the database before continuing")
        finally:
            conn.close()

    def _sync_once(self):
        try:
            from database.db_manager import get_connection
            from logic.sync import sync_cloud_once, sync_lan_once

            conn = get_connection()
            try:
                mode = conn.execute("SELECT coordination_mode FROM sync_identity WHERE singleton=1").fetchone()[0]
            finally:
                conn.close()
            (sync_cloud_once if mode == "cloud_client" else sync_lan_once)()
        finally:
            self._sync_lock.release()

    def _show_sync_conflicts(self):
        dialog = SyncConflictsDialog(self)
        dialog.exec()
        self._refresh_sync_status()


class SyncConflictsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Synchronization conflicts")
        self.resize(760, 480)
        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Created", "Type", "Event"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.itemSelectionChanged.connect(self._show_selected)
        layout.addWidget(self.table)
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        layout.addWidget(self.details)
        self.note = QLineEdit()
        self.note.setPlaceholderText("Required: how the discrepancy was reconciled")
        layout.addWidget(self.note)
        resolve = QPushButton("Mark reconciled")
        resolve.clicked.connect(self._resolve_selected)
        layout.addWidget(resolve)
        self._load()

    def _load(self):
        from database.db_manager import get_connection

        conn = get_connection()
        try:
            self.rows = conn.execute(
                "SELECT conflict_id,event_id,conflict_type,details,created_at FROM sync_conflicts "
                "WHERE status='open' ORDER BY created_at DESC"
            ).fetchall()
        finally:
            conn.close()
        self.table.setRowCount(len(self.rows))
        for i, row in enumerate(self.rows):
            for col, value in enumerate((row["created_at"], row["conflict_type"], row["event_id"] or "")):
                self.table.setItem(i, col, QTableWidgetItem(str(value)))

    def _show_selected(self):
        index = self.table.currentRow()
        if 0 <= index < len(self.rows):
            self.details.setPlainText(self.rows[index]["details"])

    def _resolve_selected(self):
        from datetime import datetime, timezone
        from database.db_manager import get_connection
        from PySide6.QtWidgets import QMessageBox

        index = self.table.currentRow()
        note = self.note.text().strip()
        if not (0 <= index < len(self.rows)) or len(note) < 8:
            QMessageBox.warning(self, "Reconciliation note required", "Select a conflict and explain the stock or transaction correction (at least 8 characters).")
            return
        conn = get_connection()
        try:
            conn.execute("UPDATE sync_conflicts SET status='resolved',resolved_at=?,resolution_note=? WHERE conflict_id=? AND status='open'",
                         (datetime.now(timezone.utc).isoformat(), note, self.rows[index]["conflict_id"]))
            conn.commit()
        finally:
            conn.close()
        self.note.clear()
        self._load()
