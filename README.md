# SOLONA Pharmacy Web

SOLONA is an offline-capable pharmacy point-of-sale and inventory web app. The browser interface is a Progressive Web App (PWA); the FastAPI service and PostgreSQL database provide authenticated workspace operations and cross-device synchronization.

## Current web workflows

- Sign in and workspace/shop discovery
- Dashboard with cached inventory and expiry/low-stock alerts
- Inventory list backed by the cloud catalogue
- POS using cached batches, with locally queued sale events
- Browser device enrollment and event sync

Customer and supplier management, reports, returns, receipts, user administration, and backup/restore are not yet implemented in the web interface. The current web API does not expose all required endpoints for these workflows.

## Run the web app

Requirements: Node.js 20+ and npm.

```sh
cd web
npm install
npm run dev
```

Set `VITE_API_URL` to the API origin if the service is hosted separately. Configure the API's `CORS_ORIGINS` to include the web origin. For a same-origin deployment, proxy `/api/v1` and `/health` to the FastAPI service and serve the Vite build from the web host.

```sh
npm run build
npm run preview
```

The PWA service worker and offline storage require HTTPS in production (or localhost during development). `web/public/sw.js` caches the app shell; IndexedDB caches workspace, inventory, and alert responses and stores pending sale events. Browser storage can be cleared by the user or browser, so it is not a replacement for a verified backup.

## Offline and sync behavior

A user must sign in and load the workspace while online before cached screens can be reopened offline. Sales are saved locally first and queued with stable event and transaction IDs. The UI reports them as pending until the sync API acknowledges them as applied. Conflicts remain visible in the local queue for operator review.

Each browser profile must be enrolled as a device while online. Device enrollment is available under Settings and requires the `device:manage` permission and an available subscription device slot. The backend validates device credentials and staff membership during replay. Do not clear site data or move the browser profile before the outbox syncs. Concurrent offline sales from different terminals can conflict when stock or prices change; offline sales are not guaranteed stock reservations.

The browser stores access/refresh tokens and device credentials in localStorage to keep this initial PWA signed in and able to sync after browser restart. Use a trusted device, HTTPS, and a strong browser profile password. A production release should move credentials to a hardened browser storage design and complete security review.

## Backend

See [backend/README.md](backend/README.md) for PostgreSQL setup, migrations, API configuration, deployment requirements, and integration tests. The backend is the only server component; there is no Python desktop client or shop LAN coordinator in this repository's active application path.
