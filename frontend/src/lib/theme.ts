/**
 * Light / dark / system theme.
 *
 * The chosen preference is stored per browser. The resolved theme is written
 * to <html data-theme>, which the stylesheet reads. index.html applies it
 * before the first paint so a dark-mode user never sees a white flash.
 */
import { useSyncExternalStore } from "react";

export type ThemePreference = "light" | "dark" | "system";
const STORAGE_KEY = "minuteai.theme";

const media = typeof window !== "undefined" && window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
const listeners = new Set<() => void>();

function readPreference(): ThemePreference {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return value === "light" || value === "dark" || value === "system" ? value : "system";
  } catch {
    return "system";
  }
}

let preference: ThemePreference = readPreference();

export function resolveTheme(pref: ThemePreference): "light" | "dark" {
  if (pref === "system") return media?.matches ? "dark" : "light";
  return pref;
}

function apply(): void {
  document.documentElement.dataset.theme = resolveTheme(preference);
  listeners.forEach((listener) => listener());
}

media?.addEventListener?.("change", () => preference === "system" && apply());

export function setThemePreference(next: ThemePreference): void {
  preference = next;
  try {
    localStorage.setItem(STORAGE_KEY, next);
  } catch {
    // Preference simply won't persist across reloads.
  }
  apply();
}

export function useTheme(): { preference: ThemePreference; resolved: "light" | "dark" } {
  const pref = useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => preference,
    () => "system" as const,
  );
  return { preference: pref, resolved: resolveTheme(pref) };
}

if (typeof document !== "undefined") apply();
