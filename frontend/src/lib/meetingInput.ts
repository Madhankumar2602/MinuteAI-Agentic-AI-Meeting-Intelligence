import { validateRecording } from "../api/upload";

/** Mirrors backend defaults; the server re-validates everything. */
export const MIN_TRANSCRIPT_CHARS = 20;
export const MAX_RECORDING_BYTES = 200 * 1024 * 1024;

export type InputMode = "transcript" | "recording" | "later";

export interface MeetingInputValue {
  mode: InputMode;
  transcript: string;
  file: File | null;
}

export function inputProblem(value: MeetingInputValue): string | null {
  if (value.mode === "transcript" && value.transcript.trim().length < MIN_TRANSCRIPT_CHARS) {
    return `Paste a transcript of at least ${MIN_TRANSCRIPT_CHARS} characters.`;
  }
  if (value.mode === "recording") {
    if (!value.file) return "Choose a recording to upload.";
    return validateRecording(value.file, MAX_RECORDING_BYTES);
  }
  return null;
}
