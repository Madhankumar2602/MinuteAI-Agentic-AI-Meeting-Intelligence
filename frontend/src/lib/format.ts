const dateFormatter = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", year: "numeric" });
const dateTimeFormatter = new Intl.DateTimeFormat(undefined, {
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "numeric",
  minute: "2-digit",
});

/** "10 Sep 2026". Accepts ISO datetimes or plain YYYY-MM-DD dates. */
export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  // A bare date must not be parsed as UTC midnight, or it can display as the
  // previous day west of Greenwich.
  const date = /^\d{4}-\d{2}-\d{2}$/.test(value) ? new Date(`${value}T00:00:00`) : new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : dateFormatter.format(date);
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : dateTimeFormatter.format(date);
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return m ? `${m} min ${s.toString().padStart(2, "0")} s` : `${s} s`;
}

/** Days from today (local) to a YYYY-MM-DD date. Negative = in the past. */
export function daysUntil(date: string, today: Date = new Date()): number {
  const target = new Date(`${date}T00:00:00`);
  const start = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  return Math.round((target.getTime() - start.getTime()) / 86_400_000);
}

export function describeDeadline(date: string | null | undefined, today: Date = new Date()): string {
  if (!date) return "No deadline";
  const days = daysUntil(date, today);
  if (days === 0) return "Due today";
  if (days === 1) return "Due tomorrow";
  if (days === -1) return "1 day overdue";
  if (days < 0) return `${-days} days overdue`;
  if (days <= 7) return `Due in ${days} days`;
  return `Due ${formatDate(date)}`;
}

/** Convert a <input type="datetime-local"> value to an ISO string with offset. */
export function localInputToIso(value: string): string {
  return new Date(value).toISOString();
}

export function nowForDateTimeInput(now: Date = new Date()): string {
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}
