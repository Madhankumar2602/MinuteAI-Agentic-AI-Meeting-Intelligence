import { createContext, useContext } from "react";

export type ToastTone = "success" | "error" | "info";

export interface ToastInput {
  title: string;
  text?: string;
  tone?: ToastTone;
}

export interface ConfirmInput {
  title: string;
  message?: string;
  confirmLabel?: string;
  tone?: "danger" | "primary";
}

export interface FeedbackApi {
  toast: (input: ToastInput) => void;
  /** Resolves true if the user confirms, false if they cancel or dismiss. */
  confirm: (input: ConfirmInput) => Promise<boolean>;
}

export const FeedbackContext = createContext<FeedbackApi | null>(null);

export function useFeedback(): FeedbackApi {
  const ctx = useContext(FeedbackContext);
  if (!ctx) throw new Error("useFeedback must be used inside <FeedbackProvider>");
  return ctx;
}
