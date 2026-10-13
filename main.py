"""Application entry point: initializes the database, applies the app-wide
stylesheet, and shows the login screen."""
import ctypes
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from database.db_manager import init_db
from ui.login_window import LoginWindow
from ui.styles import APP_STYLESHEET

# "Global\" makes this visible across sessions/users on the same machine, not
# just the current login session.
_MUTEX_NAME = "Global\\PharmacyManagementSystem_SingleInstance"
_ERROR_ALREADY_EXISTS = 183

# Module-level so the handle stays alive for the process lifetime -- a local
# variable would be eligible for garbage collection (and the mutex released)
# as soon as main() moved past using it.
_instance_mutex = None


def _acquire_single_instance_lock() -> bool:
    """True if this process now holds the app's single-instance lock, False
    if another instance already holds it. Two windows of the app open at
    once (an easy accident on a shared shop PC) would otherwise show each
    other stale data until refreshed -- this stops it at the source.

    Uses a named Windows mutex (via ctypes, no extra dependency) rather than
    a Qt local socket/pipe: the OS releases it automatically on process exit
    *or* crash, so there's no stale-lock state to ever clean up by hand.
    """
    global _instance_mutex
    kernel32 = ctypes.windll.kernel32
    _instance_mutex = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    return kernel32.GetLastError() != _ERROR_ALREADY_EXISTS


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(APP_STYLESHEET)

    if not _acquire_single_instance_lock():
        QMessageBox.warning(
            None,
            "Already Running",
            "SOLONA Pharmacy is already open.\n\n"
            "Please use the existing window instead of opening a new one.",
        )
        sys.exit(0)

    init_db()

    if getattr(sys, "frozen", False):
        try:
            import keyring

            bootstrap_password = keyring.get_password("ShaniPharmacy", "first_run_admin")
            if bootstrap_password:
                QMessageBox.information(
                    None, "Initial Administrator Password",
                    "A unique initial administrator password was created. Store it securely; "
                    "it is shown only once.\n\n"
                    f"Username: admin\nPassword: {bootstrap_password}\n\n"
                    "Change it immediately after sign-in.",
                )
                keyring.delete_password("ShaniPharmacy", "first_run_admin")
        except Exception:
            pass

    login_window = LoginWindow()
    login_window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
