import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, Flag, Link2, ShieldAlert, ShieldCheck } from "lucide-react";
import { Link } from "react-router";

import { api } from "../api/endpoints";
import type { ActionItem, ActionItemStatus } from "../api/types";
import { describeDeadline } from "../lib/format";
import { ACTION_STATUS_LABEL } from "../lib/labels";
import { useFeedback } from "./feedback-context";
import { Avatar, Badge } from "./ui";

/** Invalidate everything an action-item change can affect. */
function useItemUpdate() {
  const queryClient = useQueryClient();
  const { toast } = useFeedback();
  return useMutation({
    mutationFn: ({ id, status }: { id: string; status: ActionItemStatus; task: string }) => api.updateActionItem(id, { status }),
    onSuccess: (updated, vars) => {
      void queryClient.invalidateQueries({ queryKey: ["action-items"] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      void queryClient.invalidateQueries({ queryKey: ["meeting", updated.meeting_id, "intelligence"] });
      void queryClient.invalidateQueries({ queryKey: ["meeting", updated.meeting_id, "minutes"] });
      toast({
        title: vars.status === "done" ? "Marked as done" : `Moved to ${ACTION_STATUS_LABEL[vars.status].toLowerCase()}`,
        text: vars.task,
      });
    },
    onError: () => toast({ tone: "error", title: "Could not update the action item", text: "Please try again." }),
  });
}

export function ActionItemRow({ item, showMeeting = false, showEvidence = false }: {
  item: ActionItem;
  showMeeting?: boolean;
  showEvidence?: boolean;
}) {
  const update = useItemUpdate();
  const done = item.status === "done";
  const pendingStatus = update.isPending ? update.variables?.status : undefined;
  const shownStatus = pendingStatus ?? item.status;

  return (
    <li className={`list-item${done ? " is-done" : ""}`}>
      <button
        type="button"
        className={`check${shownStatus === "done" ? " done" : ""}`}
        disabled={update.isPending}
        aria-label={done ? `Mark "${item.task}" as not done` : `Mark "${item.task}" as done`}
        onClick={() => update.mutate({ id: item.id, status: done ? "pending" : "done", task: item.task })}
      >
        <Check size={13} strokeWidth={3} />
      </button>

      <div className="body">
        <div className="title">{item.task}</div>
        <div className="meta">
          <span className="row" style={{ gap: 6 }}>
            <Avatar name={item.owner_name} size="sm" />
            {item.owner_name ?? "Unassigned"}
          </span>
          <span className="meta-sep" />
          <span style={{ color: item.is_overdue ? "var(--danger-text)" : undefined, fontWeight: item.is_overdue ? 600 : undefined }}>
            {describeDeadline(item.deadline)}
          </span>
          {item.deadline_text && <span className="faint">(“{item.deadline_text}”)</span>}
          {item.priority === "high" && <Badge tone="danger" icon={Flag}>High priority</Badge>}
          {showMeeting && item.meeting_title && (
            <>
              <span className="meta-sep" />
              <Link to={`/meetings/${item.meeting_id}`} className="row" style={{ gap: 4 }}>
                <Link2 size={12} /> {item.meeting_title}
              </Link>
            </>
          )}
        </div>
        {showEvidence && (
          <div style={{ marginTop: 8 }}>
            {item.evidence_quote ? (
              <>
                {item.evidence_verified ? (
                  <Badge tone="success" icon={ShieldCheck}>Evidence verified</Badge>
                ) : (
                  <span title="The quoted passage was not found in the transcript. Check this item.">
                    <Badge tone="warning" icon={ShieldAlert}>Evidence not found</Badge>
                  </span>
                )}
                <div className="quote">“{item.evidence_quote}”</div>
              </>
            ) : (
              <Badge tone="warning" icon={ShieldAlert}>No evidence quoted</Badge>
            )}
          </div>
        )}
      </div>

      <label className="sr-only" htmlFor={`status-${item.id}`}>Status of {item.task}</label>
      <select
        id={`status-${item.id}`}
        className="select"
        value={item.status}
        disabled={update.isPending}
        onChange={(e) => update.mutate({ id: item.id, status: e.target.value as ActionItemStatus, task: item.task })}
      >
        {Object.entries(ACTION_STATUS_LABEL).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
      </select>
    </li>
  );
}
