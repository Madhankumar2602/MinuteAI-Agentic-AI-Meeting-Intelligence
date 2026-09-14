import { mediaTypeFor, validateRecording } from "../api/upload";

/** Mirrors backend defaults; the server re-validates everything. */
export const MIN_TRANSCRIPT_CHARS = 20;
export const MAX_RECORDING_BYTES = 200 * 1024 * 1024;

/**
 * The four ways to give MinuteAI a meeting (the core workflow), plus "later".
 * Notes and transcripts are both text, but the model is told which it is
 * reading: notes are not verbatim speech.
 */
export type InputMode = "notes" | "transcript" | "audio" | "video" | "later";

export interface MeetingInputValue {
  mode: InputMode;
  transcript: string;
  file: File | null;
}

export const isTextMode = (mode: InputMode): mode is "notes" | "transcript" => mode === "notes" || mode === "transcript";
export const isFileMode = (mode: InputMode): mode is "audio" | "video" => mode === "audio" || mode === "video";

export function inputProblem(value: MeetingInputValue): string | null {
  if (isTextMode(value.mode) && value.transcript.trim().length < MIN_TRANSCRIPT_CHARS) {
    const what = value.mode === "notes" ? "meeting notes" : "a transcript";
    return `Enter ${what} of at least ${MIN_TRANSCRIPT_CHARS} characters.`;
  }
  if (isFileMode(value.mode)) {
    if (!value.file) return `Choose ${value.mode === "video" ? "a video" : "an audio file"} to upload.`;
    const problem = validateRecording(value.file, MAX_RECORDING_BYTES);
    if (problem) return problem;
    const kind = mediaTypeFor(value.file).startsWith("video/") ? "video" : "audio";
    if (kind !== value.mode) {
      return `"${value.file.name}" is ${kind === "video" ? "a video" : "an audio file"}. Choose the ${kind === "video" ? "Video" : "Audio"} option instead.`;
    }
  }
  return null;
}

/** The meeting's `source_type` for an input. */
export function sourceTypeFor(mode: InputMode): "text" | "audio" | "video" {
  return mode === "audio" || mode === "video" ? mode : "text";
}
