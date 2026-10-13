"""Admin-only screen for managing user accounts (create, edit, deactivate, delete)."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from logic import audit, auth


class UserEditDialog(QDialog):
    """Create or edit a user. If `user` is None, this creates a new account."""

    def __init__(self, parent=None, user: auth.User = None):
        super().__init__(parent)
        self.user = user
        self.setWindowTitle("Edit User" if user else "Add User")
        self.setMinimumWidth(360)
        self._build_ui()

    def _build_ui(self):
        layout = QFormLayout()

        self.username_input = QLineEdit()
        self.full_name_input = QLineEdit()
        self.role_input = QComboBox()
        self.role_input.addItems(["cashier", "admin"])
        self.active_input = QCheckBox("Active")
        self.active_input.setChecked(True)
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.Password)

        if self.user:
            self.username_input.setText(self.user.username)
            self.username_input.setEnabled(False)  # username is immutable once created
            self.full_name_input.setText(self.user.full_name)
            self.role_input.setCurrentText(self.user.role)
            self.active_input.setChecked(self.user.is_active)
            self.password_input.setPlaceholderText("Leave blank to keep current password")
        else:
            self.password_input.setPlaceholderText("Password")

        layout.addRow("Username", self.username_input)
        layout.addRow("Full name", self.full_name_input)
        layout.addRow("Role", self.role_input)
        layout.addRow("Password", self.password_input)
        layout.addRow("", self.active_input)

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
            if self.user is None:
                auth.create_user(
                    username=self.username_input.text(),
                    password=self.password_input.text(),
                    full_name=self.full_name_input.text(),
                    role=self.role_input.currentText(),
                )
            else:
                auth.update_user(
                    user_id=self.user.id,
                    full_name=self.full_name_input.text(),
                    role=self.role_input.currentText(),
                    is_active=self.active_input.isChecked(),
                    new_password=self.password_input.text() or None,
                )
            self.accept()
        except ValueError as e:
            QMessageBox.warning(self, "Cannot save", str(e))


class UsersView(QWidget):
    def __init__(self, current_user: auth.User):
        super().__init__()
        self.current_user = current_user
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout()

        header = QHBoxLayout()
        title = QLabel("User Management")
        title.setProperty("heading", True)
        header.addWidget(title)
        header.addStretch()
        add_btn = QPushButton("➕  Add User")
        add_btn.setProperty("success", True)
        add_btn.clicked.connect(self._add_user)
        header.addWidget(add_btn)
        layout.addLayout(header)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Username", "Full Name", "Role", "Active", "Actions"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
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
        users = auth.list_users()
        self.table.setRowCount(len(users))
        for row, user in enumerate(users):
            self.table.setItem(row, 0, QTableWidgetItem(user.username))
            self.table.setItem(row, 1, QTableWidgetItem(user.full_name))
            self.table.setItem(row, 2, QTableWidgetItem(user.role))
            self.table.setItem(row, 3, QTableWidgetItem("Yes" if user.is_active else "No"))

            actions = QWidget()
            actions_layout = QHBoxLayout()
            actions_layout.setContentsMargins(4, 2, 4, 2)
            actions_layout.setSpacing(6)
            edit_btn = QPushButton("✏️  Edit")
            edit_btn.setProperty("compact", True)
            edit_btn.clicked.connect(lambda _, u=user: self._edit_user(u))
            delete_btn = QPushButton("🗑️  Delete")
            delete_btn.setProperty("compact", True)
            delete_btn.setProperty("danger", True)
            delete_btn.clicked.connect(lambda _, u=user: self._delete_user(u))
            actions_layout.addWidget(edit_btn)
            actions_layout.addWidget(delete_btn)
            actions.setLayout(actions_layout)
            self.table.setCellWidget(row, 4, actions)

    def _add_user(self):
        dialog = UserEditDialog(self)
        if dialog.exec() == QDialog.Accepted:
            audit.log(self.current_user.id, self.current_user.username, "create_user",
                       f"created user '{dialog.username_input.text().strip()}'")
            self.refresh()

    def _edit_user(self, user: auth.User):
        dialog = UserEditDialog(self, user=user)
        if dialog.exec() == QDialog.Accepted:
            audit.log(self.current_user.id, self.current_user.username, "edit_user",
                       f"edited user '{user.username}'")
            self.refresh()

    def _delete_user(self, user: auth.User):
        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Are you sure you want to delete user '{user.username}'? This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            auth.delete_user(user.id, current_user_id=self.current_user.id)
            audit.log(self.current_user.id, self.current_user.username, "delete_user",
                       f"deleted user '{user.username}'")
            self.refresh()
        except ValueError as e:
            QMessageBox.warning(self, "Cannot delete", str(e))
        except Exception as e:
            QMessageBox.critical(self, "Cannot delete", f"An unexpected error occurred:\n{e}")
