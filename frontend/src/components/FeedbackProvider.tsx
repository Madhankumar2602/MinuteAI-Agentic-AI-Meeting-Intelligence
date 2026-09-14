/**
 * Toasts and confirmation dialogs.
 *
 * Replaces window.alert/confirm, which block the page, cannot be styled, and
 * give no context. The dialog is accessible: focus moves into it, Escape and
 * the backdrop cancel, focus returns to the triggering element on close, and
 * the destructive button is never the default focus.
 */
import { AlertTriangle, CheckCircle2, Info, X, XCircle } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { FeedbackContext, type ConfirmInput, type ToastInput } from "./feedback-context";

interface ToastItem extends ToastInput {
  id: number;
}

interface PendingConfirm extends ConfirmInput {
  resolve: (value: boolean) => void;
}

const TOAST_ICON = { success: CheckCircle2, error: XCircle, info: Info };
const TOAST_MS = 4500;

export function FeedbackProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const [pending, setPending] = useState<PendingConfirm | null>(null);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => setToasts((all) => all.filter((t) => t.id !== id)), []);

  const toast = useCallback(
    (input: ToastInput) => {
      const id = nextId.current++;
      setToasts((all) => [...all.slice(-3), { tone: "success", ...input, id }]);
      window.setTimeout(() => dismiss(id), TOAST_MS);
    },
    [dismiss],
  );

  const confirm = useCallback(
    (input: ConfirmInput) => new Promise<boolean>((resolve) => setPending({ ...input, resolve })),
    [],
  );

  const close = useCallback(
    (result: boolean) => {
      pending?.resolve(result);
      setPending(null);
    },
    [pending],
  );

  const api = useMemo(() => ({ toast, confirm }), [toast, confirm]);

  return (
    <FeedbackContext.Provider value={api}>
      {children}
      <div className="toast-region" role="status" aria-live="polite">
        {toasts.map((t) => {
          const Icon = TOAST_ICON[t.tone ?? "success"];
          return (
            <div key={t.id} className={`toast ${t.tone ?? "success"}`}>
              <Icon size={18} className="toast-icon" aria-hidden />
              <div className="toast-body">
                <div className="toast-title">{t.title}</div>
                {t.text && <div className="toast-text">{t.text}</div>}
              </div>
              <button type="button" className="btn btn-ghost btn-sm btn-icon" aria-label="Dismiss" onClick={() => dismiss(t.id)}>
                <X size={15} />
              </button>
            </div>
          );
        })}
      </div>
      {pending && <ConfirmDialog request={pending} onClose={close} />}
    </FeedbackContext.Provider>
  );
}

function ConfirmDialog({ request, onClose }: { request: ConfirmInput; onClose: (result: boolean) => void }) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  const previousFocus = useRef<Element | null>(null);
  const tone = request.tone ?? "danger";

  useEffect(() => {
    previousFocus.current = document.activeElement;
    cancelRef.current?.focus();
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose(false);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      (previousFocus.current as HTMLElement | null)?.focus?.();
    };
  }, [onClose]);

  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose(false)}>
      <div className="modal" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title" aria-describedby="confirm-message">
        <div className={`modal-icon ${tone}`}>
          <AlertTriangle size={20} aria-hidden />
        </div>
        <h2 id="confirm-title">{request.title}</h2>
        {request.message && <p id="confirm-message">{request.message}</p>}
        <div className="modal-actions">
          <button ref={cancelRef} type="button" className="btn" onClick={() => onClose(false)}>
            Cancel
          </button>
          <button type="button" className={`btn ${tone === "danger" ? "btn-danger-solid" : "btn-primary"}`} onClick={() => onClose(true)}>
            {request.confirmLabel ?? "Confirm"}
          </button>
        </div>
      </div>
    </div>
  );
}
