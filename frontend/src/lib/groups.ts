import type { ActionItem } from "../api/types";
import { daysUntil } from "./format";

interface Group {
  key: string;
  label: string;
  tone: string;
  items: ActionItem[];
}

/** Group by urgency so the most important work is always at the top. */
export function groupByUrgency(items: ActionItem[], today = new Date()): Group[] {
  const groups: Group[] = [
    { key: "overdue", label: "Overdue", tone: "var(--danger)", items: [] },
    { key: "today", label: "Due today", tone: "var(--warning)", items: [] },
    { key: "week", label: "This week", tone: "var(--primary)", items: [] },
    { key: "later", label: "Later", tone: "var(--text-3)", items: [] },
    { key: "none", label: "No deadline", tone: "var(--text-3)", items: [] },
    { key: "closed", label: "Done or cancelled", tone: "var(--success)", items: [] },
  ];
  const find = (key: string) => groups.find((g) => g.key === key)!;
  for (const item of items) {
    if (item.status === "done" || item.status === "cancelled") find("closed").items.push(item);
    else if (!item.deadline) find("none").items.push(item);
    else {
      const days = daysUntil(item.deadline, today);
      find(days < 0 ? "overdue" : days === 0 ? "today" : days <= 7 ? "week" : "later").items.push(item);
    }
  }
  return groups.filter((g) => g.items.length > 0);
}

