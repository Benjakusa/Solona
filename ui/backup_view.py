"""Backup & Restore screen — designed for non-technical users:
- One-click "Backup Now" (optionally to a custom folder like a USB drive)
- "Restore Backup" with a mandatory confirmation warning + auto-restart
- A backup history list (date, size, type) with delete
"""
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from logic import backup


class BackupView(QWidget):
    def __init__(self, current_user=None):
        super().__init__()
        self.current_user = current_user
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout()

        title = QLabel("Backup & Restore")
        title.setProperty("heading", True)
        layout.addWidget(title)

        subtitle = QLabel(
            "Backups protect your data. Click 'Backup Now' regularly, and keep a "
            "copy on a USB drive or external folder for extra safety."
        )
        subtitle.setProperty("subheading", True)
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        actions = QHBoxLayout()

        backup_now_btn = QPushButton("💾  Backup Now")
        backup_now_btn.setProperty("success", True)
        backup_now_btn.clicked.connect(lambda: self._do_backup(custom_location=False))
        actions.addWidget(backup_now_btn)

        backup_to_btn = QPushButton("📁  Backup to Custom Location...")
        backup_to_btn.clicked.connect(lambda: self._do_backup(custom_location=True))
        actions.addWidget(backup_to_btn)

        restore_btn = QPushButton("♻️  Restore Backup...")
        restore_btn.setProperty("danger", True)
        restore_btn.clicked.connect(self._do_restore)
        actions.addWidget(restore_btn)

        layout.addLayout(actions)

        history_label = QLabel("Backup History")
        history_label.setProperty("heading", True)
        layout.addWidget(history_label)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Date", "Type", "Size", "Location", "Actions"])
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
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
        records = backup.list_backups()
        self.table.setRowCount(len(records))
        for row, rec in enumerate(records):
            self.table.setItem(row, 0, QTableWidgetItem(rec.date))
            self.table.setItem(row, 1, QTableWidgetItem(rec.type.capitalize()))
            self.table.setItem(row, 2, QTableWidgetItem(rec.size_human))
            self.table.setItem(row, 3, QTableWidgetItem(rec.filename))

            delete_btn = QPushButton("🗑️  Delete")
            delete_btn.setProperty("compact", True)
            delete_btn.setProperty("danger", True)
            delete_btn.clicked.connect(lambda _, r=rec: self._delete_backup(r))
            self.table.setCellWidget(row, 4, delete_btn)

    def _do_backup(self, custom_location: bool):
        destination = None
        if custom_location:
            destination = QFileDialog.getExistingDirectory(self, "Choose backup location")
            if not destination:
                return  # user cancelled

        progress = QProgressDialog("Backing up database...", None, 0, 0, self)
        progress.setWindowTitle("Backup in progress")
        progress.setWindowModality(Qt.WindowModal)
        progress.setCancelButton(None)
        progress.show()
        QApplication.processEvents()

        try:
            record = backup.create_backup(destination_dir=destination, backup_type="manual")
        except Exception as e:
            progress.close()
            QMessageBox.critical(self, "Backup Failed", f"The backup could not be completed:\n{e}")
            return

        progress.close()
        QMessageBox.information(
            self,
            "Backup Successful",
            f"Backup Successful ✅\n\nSaved to:\n{record.filename}\nSize: {record.size_human}",
        )
        self.refresh()

    def _delete_backup(self, record: backup.BackupRecord):
        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Delete this backup file?\n\n{record.filename}\n\nThis cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            backup.delete_backup(record.id)
            self.refresh()
        except ValueError as e:
            QMessageBox.warning(self, "Cannot delete", str(e))

    def _do_restore(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select a backup file to restore", str(backup.BACKUP_DIR), "Database Backups (*.db)"
        )
        if not file_path:
            return

        reply = QMessageBox.warning(
            self,
            "Confirm Restore",
            "This will replace your current data with the selected backup.\n\n"
            "Are you sure you want to continue? This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        progress = QProgressDialog("Restoring database...", None, 0, 0, self)
        progress.setWindowTitle("Restore in progress")
        progress.setWindowModality(Qt.WindowModal)
        progress.setCancelButton(None)
        progress.show()
        QApplication.processEvents()

        try:
            username = self.current_user.username if self.current_user else ""
            backup.restore_backup(file_path, performed_by=username)
        except Exception as e:
            progress.close()
            QMessageBox.critical(self, "Restore Failed", f"The restore could not be completed:\n{e}")
            return

        progress.close()
        QMessageBox.information(
            self,
            "Restore Successful",
            "Restore Successful ✅\n\nThe application will now restart to load the restored data.",
        )
        self._restart_app()

    def _restart_app(self):
        QApplication.instance().quit()
        import os
        import subprocess

        if getattr(sys, "frozen", False):
            subprocess.Popen([sys.executable])
        else:
            subprocess.Popen([sys.executable, sys.argv[0]])
        os._exit(0)
