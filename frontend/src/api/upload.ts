/**
 * Recording upload: three steps, with the bytes going straight to storage.
 *
 *   1. ask the API for a presigned POST (the API validates type and size)
 *   2. POST the file directly to object storage (storage enforces the policy)
 *   3. confirm with the API (it verifies the stored file and queues processing)
 *
 * Step 2 uses XMLHttpRequest rather than fetch because fetch still cannot
 * report upload progress, and a multi-megabyte recording needs a progress bar.
 */
import { api } from "./endpoints";
import { ApiError } from "./client";
import type { UploadComplete } from "./types";

/** Mirrors the backend allow-list (app/services/media_validation.py). */
export const ACCEPTED_MEDIA_TYPES = [
  "audio/mpeg",
  "audio/mp3",
  "audio/wav",
  "audio/x-wav",
  "audio/wave",
  "audio/mp4",
  "audio/x-m4a",
  "audio/aac",
  "audio/ogg",
  "audio/webm",
  "audio/flac",
  "video/mp4",
  "video/webm",
  "video/quicktime",
] as const;

export const ACCEPT_ATTRIBUTE = ".mp3,.wav,.m4a,.aac,.ogg,.webm,.flac,.mp4,.mov,audio/*,video/mp4,video/webm,video/quicktime";

/** Browsers sometimes report an empty or generic type; infer from the extension. */
export function mediaTypeFor(file: File): string {
  if (file.type && file.type !== "application/octet-stream") return file.type;
  const ext = file.name.split(".").pop()?.toLowerCase();
  const byExt: Record<string, string> = {
    mp3: "audio/mpeg", wav: "audio/wav", m4a: "audio/mp4", aac: "audio/aac", ogg: "audio/ogg",
    webm: "audio/webm", flac: "audio/flac", mp4: "video/mp4", mov: "video/quicktime",
  };
  return (ext && byExt[ext]) || file.type || "application/octet-stream";
}

/** Client-side pre-check for a friendly message. The server remains the authority. */
export function validateRecording(file: File, maxBytes: number): string | null {
  const type = mediaTypeFor(file);
  if (!(ACCEPTED_MEDIA_TYPES as readonly string[]).includes(type)) {
    return `"${file.name}" is not a supported audio or video file.`;
  }
  if (file.size === 0) return `"${file.name}" is empty.`;
  if (file.size > maxBytes) return `"${file.name}" is larger than the ${Math.floor(maxBytes / 1048576)} MB limit.`;
  return null;
}

export function postToStorage(
  url: string,
  fields: Record<string, string>,
  file: File,
  onProgress?: (fraction: number) => void,
  signal?: AbortSignal,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    // Policy fields must precede the file: S3 ignores fields sent after it.
    for (const [name, value] of Object.entries(fields)) form.append(name, value);
    form.append("file", file);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress?.(event.loaded / event.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        onProgress?.(1);
        resolve();
      } else {
        reject(new ApiError(xhr.status, "storage_rejected", "Storage rejected the upload. Try again, or choose a smaller supported file."));
      }
    };
    xhr.onerror = () => reject(new ApiError(0, "storage_unreachable", "Could not reach file storage."));
    xhr.onabort = () => reject(new DOMException("Upload cancelled", "AbortError"));
    signal?.addEventListener("abort", () => xhr.abort(), { once: true });
    xhr.send(form);
  });
}

export async function uploadRecording(
  meetingId: string,
  file: File,
  options: {
    onProgress?: (fraction: number) => void;
    signal?: AbortSignal;
    /**
     * Asked when the meeting already has a typed transcript, which the API
     * will not discard silently. Return true to replace it with the recording.
     */
    confirmReplaceTranscript?: () => boolean | Promise<boolean>;
  } = {},
): Promise<UploadComplete> {
  const ticket = await api.createUploadUrl(meetingId, {
    filename: file.name,
    content_type: mediaTypeFor(file),
    size_bytes: file.size,
  });
  await postToStorage(ticket.upload_url, ticket.fields, file, options.onProgress, options.signal);
  try {
    return await api.completeUpload(meetingId, ticket.upload_token);
  } catch (error) {
    // The file is already in storage; only the confirmation needs repeating.
    if (
      error instanceof ApiError &&
      error.code === "manual_transcript_exists" &&
      options.confirmReplaceTranscript &&
      (await options.confirmReplaceTranscript())
    ) {
      return api.completeUpload(meetingId, ticket.upload_token, true);
    }
    throw error;
  }
}
