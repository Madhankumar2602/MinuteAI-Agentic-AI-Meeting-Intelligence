import { Monitor, Moon, Sun } from "lucide-react";

import { setThemePreference, useTheme, type ThemePreference } from "../lib/theme";

const NEXT: Record<ThemePreference, ThemePreference> = { light: "dark", dark: "system", system: "light" };
const LABEL: Record<ThemePreference, string> = { light: "Light theme", dark: "Dark theme", system: "System theme" };

/** One button cycling light → dark → system; the icon shows the current choice. */
export function ThemeToggle({ className = "btn btn-ghost btn-sm btn-icon" }: { className?: string }) {
  const { preference } = useTheme();
  const Icon = preference === "light" ? Sun : preference === "dark" ? Moon : Monitor;
  return (
    <button
      type="button"
      className={className}
      aria-label={`${LABEL[preference]} — switch to ${LABEL[NEXT[preference]].toLowerCase()}`}
      title={`${LABEL[preference]} (click to change)`}
      onClick={() => setThemePreference(NEXT[preference])}
    >
      <Icon size={17} />
    </button>
  );
}
