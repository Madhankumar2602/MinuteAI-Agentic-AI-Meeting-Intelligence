/**
 * Where the access token lives, outside React.
 *
 * The token is held in memory and mirrored to sessionStorage so a refresh keeps
 * the user signed in within the same tab. It is deliberately not in
 * localStorage: a sessionStorage token disappears when the tab closes, which
 * narrows the window in which a stolen token is useful. Any script running on
 * the page could still read it; an httpOnly cookie would prevent that at the
 * cost of CSRF defences on the API. That trade-off is recorded in ADR 0011.
 *
 * Keeping this outside React means the API client reads the current token
 * directly, with no need to re-wire it on every render.
 */
import { configureApiClient } from "../api/client";

const STORAGE_KEY = "minuteai.token";

function read(): string | null {
  try {
    return sessionStorage.getItem(STORAGE_KEY);
  } catch {
    return null; // storage blocked (private mode, site-data settings)
  }
}

let token: string | null = read();
const unauthorizedListeners = new Set<() => void>();

export const session = {
  getToken: (): string | null => token,

  setToken(next: string | null): void {
    token = next;
    try {
      if (next) sessionStorage.setItem(STORAGE_KEY, next);
      else sessionStorage.removeItem(STORAGE_KEY);
    } catch {
      // Without storage the session simply ends at the next reload.
    }
  },

  /** Subscribe to "the server rejected our token". Returns an unsubscribe function. */
  onUnauthorized(listener: () => void): () => void {
    unauthorizedListeners.add(listener);
    return () => unauthorizedListeners.delete(listener);
  },

  /** For tests: re-read storage as if the page had just loaded. */
  reload(): void {
    token = read();
  },
};

configureApiClient({
  getToken: session.getToken,
  onUnauthorized: () => unauthorizedListeners.forEach((listener) => listener()),
});
