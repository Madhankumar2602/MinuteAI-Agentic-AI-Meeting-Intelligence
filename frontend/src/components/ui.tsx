import { AlertCircle, Inbox, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { ApiError, errorMessage } from "../api/client";
import type { JobStatus, MeetingStatus } from "../api/types";
import { avatarColor, initials } from "../lib/people";

type Tone = "neutral" | "primary" | "success" | "warning" | "danger" | "info" | "violet";

export function Badge({ tone = "neutral", dot = false, pulse = false, icon: Icon, children }: {
  tone?: Tone;
  dot?: boolean;
  pulse?: boolean;
  icon?: LucideIcon;
  children: ReactNode;
}) {
  return (
    <span className={`badge badge-${tone}`}>
      {Icon && <Icon aria-hidden />}
      {(dot || pulse) && <span className={`dot${pulse ? " pulse" : ""}`} aria-hidden />}
      {children}
    </span>
  );
}

const MEETING_STATUS: Record<MeetingStatus, { label: string; tone: Tone; pulse?: boolean }> = {
  created: { label: "Draft", tone: "neutral" },
  queued: { label: "Queued", tone: "info", pulse: true },
  processing: { label: "Processing", tone: "primary", pulse: true },
  completed: { label: "Ready", tone: "success" },
  failed: { label: "Failed", tone: "danger" },
};

export function MeetingStatusBadge({ status }: { status: MeetingStatus }) {
  // A status added to the API before the UI knows it renders as plain text
  // instead of crashing the page.
  const s = MEETING_STATUS[status] ?? { label: String(status), tone: "neutral" as const };
  return <Badge tone={s.tone} dot pulse={s.pulse}>{s.label}</Badge>;
}

const JOB_STATUS: Record<JobStatus, { label: string; tone: Tone; pulse?: boolean }> = {
  QUEUED: { label: "Queued", tone: "info", pulse: true },
  PROCESSING: { label: "Running", tone: "primary", pulse: true },
  COMPLETED: { label: "Completed", tone: "success" },
  FAILED: { label: "Failed", tone: "danger" },
};

export function JobStatusBadge({ status }: { status: JobStatus }) {
  const s = JOB_STATUS[status] ?? { label: String(status), tone: "neutral" as const };
  return <Badge tone={s.tone} dot pulse={s.pulse}>{s.label}</Badge>;
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return <span className="spinner" role="status" aria-label={label} />;
}

export function PageLoading() {
  return (
    <div className="stack" aria-busy="true" aria-label="Loading">
      <Skeleton height={34} width="40%" />
      <Skeleton height={18} width="60%" />
      <div className="stat-grid" style={{ marginTop: 10 }}>
        {[0, 1, 2, 3].map((i) => <Skeleton key={i} height={112} radius={12} />)}
      </div>
      <Skeleton height={260} radius={12} />
    </div>
  );
}

export function Skeleton({ height = 16, width = "100%", radius }: { height?: number; width?: number | string; radius?: number }) {
  return <div className="skeleton" style={{ height, width, borderRadius: radius }} aria-hidden />;
}

export function ErrorBanner({ error, title }: { error: unknown; title?: string }) {
  if (!error) return null;
  const requestId = error instanceof ApiError ? error.requestId : null;
  return (
    <div className="alert alert-danger" role="alert">
      <AlertCircle size={17} aria-hidden />
      <div>
        {title && <strong>{title} </strong>}
        {errorMessage(error)}
        {requestId && <span className="request-id">Request ID: {requestId}</span>}
      </div>
    </div>
  );
}

export function EmptyState({ icon: Icon = Inbox, title, children, action }: {
  icon?: LucideIcon;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-icon"><Icon size={24} aria-hidden /></div>
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {action && <div className="actions">{action}</div>}
    </div>
  );
}

export function PageHeader({ eyebrow, title, subtitle, actions }: {
  eyebrow?: ReactNode;
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="page-head">
      <div style={{ minWidth: 0 }}>
        {eyebrow && <div className="page-eyebrow">{eyebrow}</div>}
        <h1 className="page-title">{title}</h1>
        {subtitle && <div className="page-subtitle">{subtitle}</div>}
      </div>
      {actions && <div className="row">{actions}</div>}
    </div>
  );
}

export function ProgressBar({ fraction, label }: { fraction: number; label: string }) {
  const pct = Math.round(Math.min(1, Math.max(0, fraction)) * 100);
  return (
    <div className="progress" role="progressbar" aria-label={label} aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
      <span style={{ width: `${pct}%` }} />
    </div>
  );
}

export function Avatar({ name, size }: { name: string | null | undefined; size?: "sm" | "lg" }) {
  const label = name?.trim() || "Unassigned";
  return (
    <span className={`avatar${size ? ` ${size}` : ""}`} style={{ background: avatarColor(label) }} title={label} aria-hidden>
      {name ? initials(name) : "?"}
    </span>
  );
}
