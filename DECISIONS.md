# Hardening decisions

## Scope and release classification

The supplied hardening mission is broader than the code and test changes completed so far. This work records individual fixes and their evidence without representing the application or backend as production-ready. Release status remains internal evaluation until every required operational, security, regulatory, and platform gate is evidenced.

## Financial arithmetic

Sale discounts and tax are allocated to lines with integer-cent arithmetic. Partial returns consume a deterministic per-unit share so multiple returns reconcile to the sale's recorded net amount. Historical sale-line costs are used for profit reporting; current catalogue costs must not rewrite historical margin.

## Inventory and expiry

Expiry decisions are based on active positive-stock batches. A correction that adds stock must carry batch, expiry, unit-cost, actor, and reason data. A correction does not silently overwrite medicine master data or existing batch identity.

## Synchronization

Replayed events preserve event occurrence time and are idempotent by event identity. When stock movement cannot be applied safely, the system should report a conflict for review rather than silently inventing a successful reconciliation. The current implementation does not yet cover every event type or outage/replay scenario.

## LAN credential storage

Coordinator device credentials are encrypted at rest using AES-GCM. Deployments should provide `SOLONA_LAN_MASTER_KEY` and protect it with the host's secret-management controls. When absent, the coordinator creates a local key file beside its database; losing either file prevents decryption. This mechanism does not provide transport encryption or request replay protection.

## Backup path safety

Backup paths are limited to registered roots, and deletion/retention reject symlink or traversal paths. Backup encryption and full restore verification remain release work.
