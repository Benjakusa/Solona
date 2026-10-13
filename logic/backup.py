"""Backup & Restore business logic.

Design notes (this module is the app's top priority per spec):
- Backups are plain file copies of the SQLite .db file, with a timestamped
  filename, written by default to backups/ next to the app.
- Every backup is verified by comparing file size to the source before it is
  recorded as successful.
- Filenames are never reused: if a name collision occurs (same-second backup),
  a numeric suffix is appended instead of overwriting.
- Auto-backups are capped (oldest deleted beyond the retention count) so disk
  usage stays bounded; manual backups are never auto-deleted.
"""
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from database.db_manager import BACKUP_DIR, DATA_DIR, DB_PATH, get_connection

# Tables that must be present for a file to be trusted as a real backup of
# this app's database, rather than some unrelated or corrupted file.
_REQUIRED_TABLES = {"users", "medicines", "sales", "sale_items", "customers", "suppliers"}
_BACKUP_ROOTS_FILE = "backup_roots.json"


def _registered_backup_roots() -> list[Path]:
    roots = [Path(BACKUP_DIR), Path(DATA_DIR)]
    registry = Path(DATA_DIR) / _BACKUP_ROOTS_FILE
    try:
        configured = json.loads(registry.read_text(encoding="utf-8"))
        if isinstance(configured, list):
            roots.extend(Path(value) for value in configured if isinstance(value, str))
    except (OSError, ValueError, TypeError):
        pass
    resolved = []
    for root in roots:
        try:
            canonical = root.resolve()
        except OSError:
            continue
        if canonical not in resolved:
            resolved.append(canonical)
    return resolved


def _register_backup_root(root: Path) -> None:
    canonical = root.resolve()
    registry = Path(DATA_DIR) / _BACKUP_ROOTS_FILE
    registry.parent.mkdir(parents=True, exist_ok=True)
    try:
        values = json.loads(registry.read_text(encoding="utf-8"))
        if not isinstance(values, list):
            values = []
    except (OSError, ValueError, TypeError):
        values = []
    value = str(canonical)
    if value not in values:
        values.append(value)
        registry.write_text(json.dumps(values), encoding="utf-8")


def _managed_backup_path(filename: str) -> Path:
    """Resolve a backup-log path only within a registered, nonsymlink root."""
    candidate = Path(filename)
    if not candidate.is_absolute():
        candidate = Path(BACKUP_DIR) / candidate
    try:
        resolved = candidate.resolve(strict=False)
    except OSError as exc:
        raise ValueError("Backup path is not available.") from exc
    for root in _registered_backup_roots():
        if not resolved.is_relative_to(root):
            continue
        current = candidate
        while current != current.parent and current != root:
            if current.is_symlink():
                raise ValueError("Backup path may not contain symbolic links.")
            current = current.parent
        if current != root:
            continue
        if candidate.exists() and not candidate.is_file():
            raise ValueError("Backup path is not a regular file.")
        return candidate
    raise ValueError("Backup path is outside the registered backup folders.")


def _validate_backup_file(path: Path) -> None:
    """Raise ValueError if `path` is not a usable backup of this app's DB.
    Restoring a corrupt/unrelated file would otherwise silently destroy the
    live database, so this check runs before anything is touched."""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise ValueError(f"Backup file failed an integrity check: {integrity}")
            tables = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        finally:
            conn.close()
    except sqlite3.DatabaseError as e:
        raise ValueError(f"This file is not a valid database backup: {e}")

    missing = _REQUIRED_TABLES - tables
    if missing:
        raise ValueError(
            "This file doesn't look like a SOLONA Pharmacy backup "
            f"(missing tables: {', '.join(sorted(missing))})."
        )

AUTO_BACKUP_RETENTION = 15


@dataclass
class BackupRecord:
    id: int
    filename: str
    date: str
    size: int
    type: str

    @property
    def size_human(self) -> str:
        size = self.size
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024:
                return f"{size:.1f} {unit}" if unit != "B" else f"{size} {unit}"
            size /= 1024
        return f"{size:.1f} TB"


def _row_to_record(row) -> BackupRecord:
    return BackupRecord(id=row["id"], filename=row["filename"], date=row["date"], size=row["size"], type=row["type"])


def _unique_destination(directory: Path, base_name: str) -> Path:
    """Never overwrite an existing backup filename; append _1, _2, ... if needed."""
    candidate = directory / base_name
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    counter = 1
    while True:
        candidate = directory / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def create_backup(destination_dir: Optional[str] = None, backup_type: str = "manual") -> BackupRecord:
    """Copy the live DB file to `destination_dir` (default: backups/) with a
    timestamped name, verify it copied correctly, and log it. Raises on failure."""
    if not DB_PATH.exists():
        raise FileNotFoundError("No database file found to back up yet.")

    target_dir = Path(destination_dir) if destination_dir else BACKUP_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    _register_backup_root(target_dir)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    base_name = f"backup_{timestamp}.db"
    dest_path = _unique_destination(target_dir, base_name)

    source = sqlite3.connect(str(DB_PATH))
    target = sqlite3.connect(str(dest_path))
    try:
        source.backup(target)
    except Exception:
        dest_path.unlink(missing_ok=True)
        raise
    finally:
        target.close()
        source.close()
    _validate_backup_file(dest_path)

    dest_size = dest_path.stat().st_size

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO backup_log (filename, size, type) VALUES (?, ?, ?)",
            (str(dest_path), dest_size, backup_type),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM backup_log WHERE filename = ?", (str(dest_path),)).fetchone()
    finally:
        conn.close()

    if backup_type == "auto":
        _enforce_auto_retention()

    return _row_to_record(row)


def _enforce_auto_retention():
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM backup_log WHERE type='auto' ORDER BY date DESC"
        ).fetchall()
        for row in rows[AUTO_BACKUP_RETENTION:]:
            try:
                path = _managed_backup_path(row["filename"])
            except ValueError:
                # Preserve suspicious rows for review and never unlink their target.
                continue
            if path.exists():
                path.unlink(missing_ok=True)
            conn.execute("DELETE FROM backup_log WHERE id = ?", (row["id"],))
        conn.commit()
    finally:
        conn.close()


def list_backups() -> list[BackupRecord]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM backup_log ORDER BY date DESC").fetchall()
        records = [_row_to_record(r) for r in rows]
    finally:
        conn.close()
    # Drop entries whose file has gone missing (e.g. deleted outside the app).
    safe_records = []
    for record in records:
        try:
            if _managed_backup_path(record.filename).is_file():
                safe_records.append(record)
        except ValueError:
            continue
    return safe_records


def delete_backup(record_id: int) -> None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM backup_log WHERE id=?", (record_id,)).fetchone()
        if row is None:
            raise ValueError("Backup record not found.")
        path = _managed_backup_path(row["filename"])
        if path.exists():
            path.unlink()
        conn.execute("DELETE FROM backup_log WHERE id=?", (record_id,))
        conn.commit()
    finally:
        conn.close()


def run_daily_auto_backup_if_needed() -> None:
    """Create a silent auto-backup once per calendar day (e.g. on first login).
    Failures are swallowed — auto-backup must never block the user."""
    today = datetime.now().strftime("%Y-%m-%d")
    conn = get_connection()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key='last_auto_backup_date'").fetchone()
        last_date = row["value"] if row else None
        if last_date == today:
            return
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('last_auto_backup_date', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (today,),
        )
        conn.commit()
    finally:
        conn.close()

    try:
        create_backup(backup_type="auto")
    except Exception:
        pass  # background safety net only; never interrupt the user


def run_auto_backup_on_exit() -> None:
    """Silent backup on app close. Failures are swallowed for the same reason."""
    try:
        create_backup(backup_type="auto")
    except Exception:
        pass


def restore_backup(backup_file_path: str, performed_by: str = "") -> None:
    """Replace the live DB with the given backup file. The caller is
    responsible for closing any open DB connections and restarting the app
    afterward (SQLite connections in this app are short-lived per-call, so
    none should be open at this point)."""
    src = Path(backup_file_path)
    if not src.exists():
        raise FileNotFoundError("Selected backup file does not exist.")

    _validate_backup_file(src)

    # Safety copy of the current DB in case the restore needs to be undone.
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        safety_path = DATA_DIR / f"pre_restore_safety_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.db"
        source = sqlite3.connect(str(DB_PATH))
        target = sqlite3.connect(str(safety_path))
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        _validate_backup_file(safety_path)

    # A restore *replaces* the database, so an audit_log row about it would
    # be wiped out by the very act of restoring (the incoming backup's own
    # audit_log doesn't have it). Record it in a plain text file next to the
    # backups instead, which survives independently of any restore.
    try:
        log_path = BACKUP_DIR / "restore_history.log"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(
                f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  "
                f"user={performed_by or 'unknown'}  restored_from={src.name}\n"
            )
    except Exception:
        pass  # never block a restore over a logging failure

    temporary = DB_PATH.with_suffix(DB_PATH.suffix + ".restore-tmp")
    source = sqlite3.connect(str(src))
    target = sqlite3.connect(str(temporary))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    _validate_backup_file(temporary)
    # The desktop normally closes short-lived connections after each operation.
    # Checkpoint any committed WAL content and remove stale sidecars before the
    # database file is atomically replaced, so an old WAL cannot replay on top
    # of the restored snapshot.
    if DB_PATH.exists():
        live = sqlite3.connect(str(DB_PATH))
        try:
            live.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            live.close()
    temporary.replace(DB_PATH)
    for suffix in ("-wal", "-shm"):
        Path(str(DB_PATH) + suffix).unlink(missing_ok=True)
