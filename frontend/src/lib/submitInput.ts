import { api } from "../api/endpoints";
import { uploadRecording } from "../api/upload";
import { isFileMode, isTextMode, type MeetingInputValue } from "./meetingInput";

/**
 * Send a meeting's content and start processing.
 *
 * Notes / transcript: save the text (with its kind), then queue processing.
 * Audio / video:      upload it; confirming the upload queues processing server-side.
 */
export async function submitMeetingInput(
  meetingId: string,
  value: MeetingInputValue,
  options: {
    onProgress?: (fraction: number) => void;
    /** Asked before typed text is replaced by the recording's transcript. */
    confirmReplaceTranscript?: () => boolean | Promise<boolean>;
  } = {},
): Promise<void> {
  if (isTextMode(value.mode)) {
    await api.putTranscript(meetingId, value.transcript.trim(), value.mode);
    await api.process(meetingId);
  } else if (isFileMode(value.mode) && value.file) {
    await uploadRecording(meetingId, value.file, {
      onProgress: options.onProgress,
      confirmReplaceTranscript: options.confirmReplaceTranscript,
    });
  }
}
