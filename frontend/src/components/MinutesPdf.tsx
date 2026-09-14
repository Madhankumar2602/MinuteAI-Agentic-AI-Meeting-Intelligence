import { Download, ExternalLink, FileCheck2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type { MinutesPdf } from "../api/types";
import { formatBytes } from "../lib/format";
import { downloadFrom, useMinutesPdf } from "../lib/minutes";
import { ErrorBanner, Spinner } from "./ui";

type Action = "view" | "download";

/**
 * View / Download controls for the minutes PDF.
 *
 * "View" shows the PDF inside the app, in a dialog. Opening a new window from
 * script after a network request is blocked by many browsers (and was, in live
 * testing), so the dialog offers "Open in new tab" as an ordinary link, which
 * is never blocked.
 */
function useMinutesPdfActions(meetingId: string) {
  const pdf = useMinutesPdf(meetingId);
  const [preview, setPreview] = useState<MinutesPdf | null>(null);
  const [action, setAction] = useState<Action | null>(null);

  function run(next: Action) {
    setAction(next);
    pdf.mutate(undefined, {
      onSuccess: (result) => (next === "view" ? setPreview(result) : downloadFrom(result.download_url)),
    });
  }

  return {
    run,
    pending: pdf.isPending ? action : null,
    error: pdf.error,
    last: pdf.data,
    dialog: preview && <PdfPreviewDialog pdf={preview} onClose={() => setPreview(null)} />,
  };
}

export function MinutesPdfButtons({ meetingId }: { meetingId: string }) {
  const { run, pending, error, dialog } = useMinutesPdfActions(meetingId);
  return (
    <>
      <button type="button" className="btn btn-gradient" disabled={Boolean(pending)} onClick={() => run("view")}>
        {pending === "view" ? <Spinner label="Preparing PDF" /> : <><FileCheck2 size={15} /> View minutes PDF</>}
      </button>
      <button type="button" className="btn" disabled={Boolean(pending)} onClick={() => run("download")} aria-label="Download minutes PDF">
        {pending === "download" ? <Spinner label="Preparing PDF" /> : <><Download size={15} /> Download</>}
      </button>
      {error != null && <span className="sr-only" role="alert">The PDF could not be prepared.</span>}
      {dialog}
    </>
  );
}

export function MinutesPdfCard({ meetingId, summary }: { meetingId: string; summary: string }) {
  const { run, pending, error, last, dialog } = useMinutesPdfActions(meetingId);
  return (
    <section className="card pdf-card" aria-labelledby="pdf-heading">
      <div className="pdf-card-art" aria-hidden><FileCheck2 size={26} /></div>
      <h2 className="card-title" id="pdf-heading">Minutes of Meeting PDF</h2>
      <p className="muted small" style={{ margin: "4px 0 14px" }}>{summary} Always reflects your latest corrections.</p>
      <ErrorBanner error={error} />
      <div className="row" style={{ gap: 8 }}>
        <button type="button" className="btn btn-primary" disabled={Boolean(pending)} onClick={() => run("view")}>
          {pending === "view" ? <Spinner label="Preparing PDF" /> : <><FileCheck2 size={15} /> View</>}
        </button>
        <button type="button" className="btn" disabled={Boolean(pending)} onClick={() => run("download")}>
          {pending === "download" ? <Spinner label="Preparing PDF" /> : <><Download size={15} /> Download PDF</>}
        </button>
      </div>
      {last && (
        <p className="faint xs" style={{ marginTop: 10 }} aria-live="polite">
          {last.filename} · {formatBytes(last.size_bytes)}{last.pages ? ` · ${last.pages} page${last.pages === 1 ? "" : "s"}` : ""}{last.reused ? " · up to date" : " · freshly generated"}
        </p>
      )}
      {dialog}
    </section>
  );
}

function PdfPreviewDialog({ pdf, onClose }: { pdf: MinutesPdf; onClose: () => void }) {
  const closeButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    closeButton.current?.focus();
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal pdf-modal" role="dialog" aria-modal="true" aria-labelledby="pdf-preview-title">
        <div className="pdf-modal-head">
          <div style={{ minWidth: 0 }}>
            <h2 id="pdf-preview-title" className="truncate">{pdf.filename}</h2>
            <p className="faint xs" style={{ margin: 0 }}>{formatBytes(pdf.size_bytes)}{pdf.pages ? ` · ${pdf.pages} pages` : ""} · links expire in {Math.round(pdf.expires_in / 60)} min</p>
          </div>
          <div className="row" style={{ gap: 8, flexWrap: "nowrap" }}>
            <a className="btn btn-sm" href={pdf.view_url} target="_blank" rel="noopener noreferrer"><ExternalLink size={14} /> Open in new tab</a>
            <a className="btn btn-sm btn-primary" href={pdf.download_url}><Download size={14} /> Download</a>
            <button ref={closeButton} type="button" className="btn btn-ghost btn-sm btn-icon" aria-label="Close preview" onClick={onClose}><X size={16} /></button>
          </div>
        </div>
        <iframe className="pdf-frame" title="Minutes of Meeting PDF preview" src={pdf.view_url} />
      </div>
    </div>
  );
}
