import { getCache, setCache } from './db';

const API = import.meta.env.VITE_API_URL || '';
export const session = {
  get access() { return localStorage.getItem('solona.access'); },
  set access(v) { v ? localStorage.setItem('solona.access', v) : localStorage.removeItem('solona.access'); },
  get refresh() { return localStorage.getItem('solona.refresh'); },
  set refresh(v) { v ? localStorage.setItem('solona.refresh', v) : localStorage.removeItem('solona.refresh'); },
};

export async function request(path, { method = 'GET', body, auth = true, headers = {} } = {}) {
  const send = () => fetch(`${API}/api/v1${path}`, {
    method,
    headers: { ...(body ? { 'Content-Type': 'application/json' } : {}), ...(auth && session.access ? { Authorization: `Bearer ${session.access}` } : {}), ...headers },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  let response = await send();
  if (response.status === 401 && auth && session.refresh && path !== '/auth/refresh') {
    const refresh = await fetch(`${API}/api/v1/auth/refresh`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: session.refresh }),
    });
    if (refresh.ok) {
      const tokens = await refresh.json(); session.access = tokens.access_token; session.refresh = tokens.refresh_token;
      response = await send();
    }
  }
  const data = response.status === 204 ? null : await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data?.error?.message || data?.detail || `Request failed (${response.status})`);
  return data;
}

export async function cachedGet(key, path) {
  try {
    const data = await request(path);
    await setCache(key, data);
    return { data, offline: false };
  } catch (error) {
    const cached = await getCache(key);
    if (!cached) throw error;
    return { data: cached, offline: true };
  }
}
