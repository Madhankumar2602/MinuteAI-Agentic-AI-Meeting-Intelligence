import type { ActionItemStatus, DecisionStatus } from "../api/types";

export const ACTION_STATUS_LABEL: Record<ActionItemStatus, string> = {
  pending: "Pending",
  in_progress: "In progress",
  done: "Done",
  cancelled: "Cancelled",
};

export const DECISION_STATUS_LABEL: Record<DecisionStatus, string> = {
  open: "Open",
  resolved: "Resolved",
  superseded: "Superseded",
};
