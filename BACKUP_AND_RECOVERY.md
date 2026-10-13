# Backup and recovery

## Desktop SQLite

For compatibility with existing installations, SOLONA stores data under `%LOCALAPPDATA%\PharmacyManagementSystem\data\pharmacy.db` when packaged. Use the application backup screen or `logic.backup.create_backup`; backups use SQLite's online backup API so committed pages in a WAL are included. Keep encrypted copies on a separate protected device/location. Do not rely on a file copy of a live database, screenshots, or sync as backup.

To restore, stop all application processes, retain the current database and its `-wal`/`-shm` files, validate the selected backup, and restore using the application. Restore creates a safety backup first. Reopen the application and verify integrity, users, recent sales, balances, stock, outbox/conflicts, and reports before continuing. Preserve the restore history log. If data was entered after the backup, export/preserve the current database before restore and reconcile those transactions; restoring an older snapshot can discard newer local work.

## Shop coordinator

Stop the coordinator for host-level file copies, or use SQLite's online backup API. Protect the database and enrollment secret. Keep terminal databases/outboxes before coordinator recovery. Restore the latest verified coordinator snapshot, then replay/reconcile stable event IDs and investigate conflicts. If no valid coordinator backup exists, re-seed only from the authorized baseline and reconcile every terminal before resuming coordinated stock claims.

## Cloud PostgreSQL

Use encrypted base backups and WAL archiving/PITR to a separate failure domain. Define RPO/RTO, retention, encryption-key ownership and access logging. At least quarterly and after schema/backup changes, restore to an isolated PostgreSQL instance, run integrity checks, validate Alembic version, tenant/shop row counts, representative exports and financial/stock totals, then run readiness and isolation checks. Record duration and gaps. Tenant JSON exports are logical portability artifacts and do not replace PITR backups.

For recovery, declare an incident, stop unsafe writes if consistency is uncertain, preserve logs and terminal outboxes, restore/PITR into isolation, validate with application owners, and only then switch traffic. Never downgrade production migrations as the rollback mechanism; use forward fixes or a verified restore. After recovery, rotate affected secrets/tokens, reconcile M-Pesa against Safaricom records, review sync conflicts, notify affected stakeholders as legally required, and document incident actions.
