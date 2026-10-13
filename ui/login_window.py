"""Login screen. On success, opens the MainWindow with the authenticated user."""
import os
from PySide6.QtCore import Qt
from ui.icons import icon

from PySide6.QtWidgets import (
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from logic import audit, auth


class LoginWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SOLONA Pharmacy - Login")
        # A minimum (not fixed) size: the layout below needs a certain
        # amount of room for all its rows, but isn't locked to it -- a
        # fixed size here previously clipped/hid the bottom of the form
        # every time a new row was added to it.
        self.setMinimumSize(400, 440)
        self.resize(400, 440)
        self.main_window = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(40, 30, 40, 30)
        layout.setSpacing(14)

        title = QLabel("SOLONA Pharmacy")
        title.setProperty("heading", True)
        title.setAlignment(Qt.AlignCenter)
        title.setWordWrap(True)
        layout.addWidget(title)

        subtitle = QLabel("Sign in to continue")
        subtitle.setProperty("subheading", True)
        subtitle.setAlignment(Qt.AlignCenter)
        layout.addWidget(subtitle)

        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("Username")
        layout.addWidget(self.username_input)

        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Password")
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_input.returnPressed.connect(self._attempt_login)
        layout.addWidget(self.password_input)

        self.error_label = QLabel("")
        self.error_label.setProperty("warning", True)
        self.error_label.setAlignment(Qt.AlignCenter)
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

        login_btn = QPushButton("Login")

        login_btn.setIcon(icon(login_btn, "accept"))
        login_btn.setProperty("success", True)
        login_btn.clicked.connect(self._attempt_login)
        layout.addWidget(login_btn)

        online_btn = QPushButton("Sign in online / refresh offline access")

        online_btn.setIcon(icon(online_btn, "info"))
        online_btn.setProperty("flat", True)
        online_btn.clicked.connect(self._attempt_online_login)
        layout.addWidget(online_btn)

        hint = QLabel("First run creates the admin account. Change its password immediately.")
        hint.setProperty("subheading", True)
        hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(hint)

        forgot_btn = QPushButton("Forgot admin password?")
        forgot_btn.setProperty("flat", True)
        forgot_btn.setStyleSheet("text-align: center;")
        forgot_btn.clicked.connect(self._recover_admin)
        layout.addWidget(forgot_btn)

        self.setLayout(layout)
        self.username_input.setFocus()

    def _attempt_online_login(self):
        email = self.username_input.text().strip()
        password = self.password_input.text()
        if not email or not password:
            self.error_label.setText("Enter your cloud email and password first.")
            return
        base_url = os.environ.get("SOLONA_API_URL", os.environ.get("SPMS_API_URL", "")).strip()
        if not base_url:
            base_url, ok = QInputDialog.getText(self, "Cloud service", "Backend URL (for example https://pharmacy.example.com):")
            if not ok or not base_url.strip():
                return
        try:
            from logic.cloud import complete_online_sign_in, online_login

            tokens, identity = online_login(base_url.strip(), email, password)
            workspaces = identity["workspaces"]
            labels = [f"{w['tenant_name']} — {w['role']}" for w in workspaces]
            workspace_label, ok = QInputDialog.getItem(self, "Choose tenant", "Tenant:", labels, 0, False)
            if not ok:
                return
            workspace = workspaces[labels.index(workspace_label)]
            shops = workspace["shops"]
            if not shops:
                raise RuntimeError("This account has no active shop assignment.")
            shop_labels = [s["shop_name"] for s in shops]
            shop_label, ok = QInputDialog.getItem(self, "Choose shop", "Shop:", shop_labels, 0, False)
            if not ok:
                return
            shop = shops[shop_labels.index(shop_label)]
            from database.db_manager import get_connection

            conn = get_connection()
            try:
                local_medicines = conn.execute("SELECT COUNT(*) FROM medicines").fetchone()[0]
                local_sales = conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
                pending = conn.execute("SELECT COUNT(*) FROM sync_outbox WHERE status IN ('pending','failed','inflight')").fetchone()[0]
            finally:
                conn.close()
            warning = (
                f"Link this computer to {workspace['tenant_name']} / {shop['shop_name']}?\n\n"
                f"This local database currently contains {local_medicines} medicines, {local_sales} historical sales, "
                f"and {pending} pending events. Existing records are retained; unmatched stock will be flagged for review."
            )
            if QMessageBox.question(self, "Confirm tenant and shop", warning,
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            user = complete_online_sign_in(base_url.strip(), email, password, workspace, shop,
                                           tokens=tokens, identity=identity)
        except Exception as exc:
            self.error_label.setText(str(exc))
            return
        self.error_label.setText("")
        self._open_main_window(user)

    def _recover_admin(self):
        pin, ok = QInputDialog.getText(
            self,
            "Reset Admin Password",
            "Enter your Recovery PIN to continue.\n\n"
            "(Only a Recovery PIN configured by the shop owner is accepted.)",
            QLineEdit.Password,
        )
        if not ok:
            return
        if not auth.verify_recovery_pin(pin):
            QMessageBox.warning(self, "Incorrect PIN", "That Recovery PIN is not correct.")
            return

        new_password, ok = QInputDialog.getText(
            self, "Choose New Admin Password",
            "Enter a new password with at least 12 characters.", QLineEdit.Password,
        )
        if not ok:
            return
        confirm_password, ok = QInputDialog.getText(
            self, "Confirm New Admin Password", "Re-enter the new password.", QLineEdit.Password,
        )
        if not ok:
            return
        if new_password != confirm_password:
            QMessageBox.warning(self, "Password Mismatch", "The two passwords do not match.")
            return
        if len(new_password.strip()) < 12:
            QMessageBox.warning(self, "Password Too Short", "Use at least 12 characters.")
            return

        reply = QMessageBox.warning(
            self,
            "Reset Admin Password",
            "This changes the admin account password to the new password you entered.\n\n"
            "Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        username = auth.recover_admin_access(new_password)
        audit.log(None, "(recovery)", "admin_password_reset", f"admin account '{username}' reset via Recovery PIN")
        QMessageBox.information(
            self,
            "Password Reset",
            f"The administrator password was changed. Sign in as {username} with the new password.",
        )
        self.username_input.setText(username)
        self.password_input.clear()

    def _attempt_login(self):
        username = self.username_input.text().strip()
        password = self.password_input.text()

        if not username or not password:
            self.error_label.setText("Please enter both username and password.")
            return

        user = auth.login(username, password)
        if user is None:
            self.error_label.setText("Invalid username or password.")
            self.password_input.clear()
            return

        self.error_label.setText("")
        self._open_main_window(user)

    def _open_main_window(self, user):
        # Imported here to avoid a circular import (main_window imports feature
        # views which may, in turn, import back into ui in future modules).
        from ui.main_window import MainWindow

        self.main_window = MainWindow(user)
        self.main_window.show()
        self.close()
