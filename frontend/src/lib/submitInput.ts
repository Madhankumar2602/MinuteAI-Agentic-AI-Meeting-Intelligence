import { api } from "../api/endpoints";
import { uploadRecording } from "../api/upload";
import type { MeetingInputValue } from "./meetingInput";

/**
 * Send a meeting's content and start processing.
 *
 * Transcript: save it, then queue processing.
 * Recording:  upload it; confirming the upload queues processing server-side.
 */
export async function submitMeetingInput(
  meetingId: string,
  value: MeetingInputValue,
  options: {
    onProgress?: (fraction: number) => void;
    /** Asked before a typed transcript is replaced by the recording's transcript. */
    confirmReplaceTranscript?: () => boolean | Promise<boolean>;
  } = {},
): Promise<void> {
  if (value.mode === "transcript") {
    await api.putTranscript(meetingId, value.transcript.trim());
    await api.process(meetingId);
  } else if (value.mode === "recording" && value.file) {
    await uploadRecording(meetingId, value.file, {
      onProgress: options.onProgress,
      confirmReplaceTranscript: options.confirmReplaceTranscript,
    });
  }
}
