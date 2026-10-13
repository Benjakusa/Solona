# Production hardening audit results

Audit scope: the hardening work in the current working tree, based on the supplied desktop and backend repro scripts and targeted regression tests. This is an implementation record, not a certification or production approval.

## Verified in this work

- Backend membership changes now enforce role hierarchy, active membership checks, owner protection, and audited denials. Invitation acceptance rechecks inviter authority.
- Backend request database sessions commit before the HTTP response is sent. A forced commit-failure regression covers tenant registration.
- Desktop and backend return calculations allocate discounts and taxes to sale lines and partial returns using integer-cent arithmetic.
- Desktop profit reporting uses sale-time unit cost and accounts for allocated discounts, taxes, and logged returns.
- Invoice numbering uses a persistent counter and recovers from deleted invoice-number gaps.
- Desktop expiry alerts and cart availability use in-stock batch expiry. Stock corrections require traceable fields; explicit purchase selling-price changes are applied and audited.
- Password verification rejects malformed stored hashes. Password changes enforce a minimum length and protect the last active administrator. Recovery uses a configured PIN and chosen password.
- Sync fixes cover cloud bootstrap selling prices, replayed purchase numeric conversion, medicine stock movements, and original sale timestamps.
- LAN enrollment rejects duplicate/revoked IDs, provides explicit credential-rotating reinstatement, encrypts stored device credentials, and bounds request body size and socket time.
- Backup deletion and retention reject paths outside registered backup roots and symlink traversal.

## Verification evidence

Targeted regression tests were added alongside the changes. Earlier in the work, desktop baseline ran with 15 passing tests; backend baseline exposed a role-dependent RLS test setup issue. With a dedicated non-superuser test database, a subsequent backend suite passed 18 tests. Additional targeted tests were run for later work packages.

A final full-suite run is still required after all current changes. Windows packaging and production-like end-to-end workflows have not been verified. Do not interpret targeted passing tests as proof of overall safety or correctness.

## Findings still open

Role enforcement in the desktop business-logic layer, login throttling, password hash migration/work-factor review, complete payment and refund authorization, sync conflict handling, device TLS and replay protection, coordinator enrollment hardening, backup encryption/restore drills, complete inventory alert/count semantics, cross-platform startup locking, migration reliability, monitoring, and release automation require further work. The tracked list is maintained in [PRODUCTION_READINESS.md](PRODUCTION_READINESS.md).
