import type { AgentRun, Proposal, ProposalKind } from "../api/types";

export const PROPOSAL_KIND_LABEL: Record<ProposalKind, string> = {
  overdue_action: "Overdue action item",
  due_soon_action: "Due soon",
  unassigned_action: "No owner",
  open_decision: "Open decision",
  unresolved_topic: "Unresolved topic",
  recurring_topic: "Recurring topic",
};

export const STEP_LABEL: Record<string, string> = {
  observe: "Observed your meetings",
  deduplicate: "Skipped what was already proposed",
  prioritise: "Prioritised by urgency",
  recall: "Recalled related passages",
  decide_and_draft: "Decided and drafted",
  verify: "Checked the drafts",
  propose: "Proposed follow-ups",
  failed: "Stopped",
};

type Step = AgentRun["steps"][number];

/** One plain-language line describing what a trace step did. */
export function describeStep(step: Step): string {
  const n = (key: string) => Number(step[key] ?? 0);
  switch (step.step) {
    case "observe":
      return `${n("total")} situation${n("total") === 1 ? "" : "s"} found`;
    case "deduplicate":
      return `${n("already_proposed")} already proposed, ${n("remaining")} new`;
    case "prioritise": {
      const selected = Array.isArray(step.selected) ? step.selected.length : 0;
      return `${selected} selected` + (n("deferred") ? `, ${n("deferred")} left for the next run` : "");
    }
    case "recall": {
      const passages = step.passages && typeof step.passages === "object" ? Object.values(step.passages as Record<string, number>) : [];
      return `${passages.reduce((a, b) => a + b, 0)} passages from past meetings`;
    }
    case "decide_and_draft":
      return step.fallback ? "The AI was unavailable, so standard templates were used" : `${n("drafts")} drafts by ${String(step.model)}`;
    case "verify": {
      const adjusted = step.adjustments && typeof step.adjustments === "object" ? Object.keys(step.adjustments).length : 0;
      const resolved = Array.isArray(step.resolved) ? step.resolved.length : 0;
      return `${adjusted} draft${adjusted === 1 ? "" : "s"} corrected, ${resolved} already resolved`;
    }
    case "propose":
      return `${n("created")} follow-up${n("created") === 1 ? "" : "s"} waiting for your approval`;
    case "failed":
      return `Error: ${String(step.error)}`;
    default:
      return "";
  }
}

/** A mailto: link for an approved follow-up, so it can be sent from the user's own email. */
export function mailtoHref(proposal: Proposal): string {
  const subject = proposal.final_subject ?? proposal.draft_subject;
  const body = proposal.final_body ?? proposal.draft_body;
  return `mailto:?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
}
