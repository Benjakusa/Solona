import { openDB } from 'idb';

const dbPromise = openDB('solona-web', 1, {
  upgrade(db) {
    db.createObjectStore('cache', { keyPath: 'key' });
    db.createObjectStore('outbox', { keyPath: 'event_id' });
  },
});

export async function getCache(key, fallback = null) {
  return (await (await dbPromise).get('cache', key))?.value ?? fallback;
}
export async function setCache(key, value) {
  return (await dbPromise).put('cache', { key, value, updated_at: new Date().toISOString() });
}
export async function listOutbox() {
  return (await dbPromise).getAll('outbox');
}
export async function enqueue(event) {
  return (await dbPromise).put('outbox', event);
}
export async function removeEvent(id) {
  return (await dbPromise).delete('outbox', id);
}
