import { useMutation, useQuery } from "@tanstack/react-query";

import { api } from "../api/endpoints";

export const minutesQueryKey = (meetingId: string) => ["meeting", meetingId, "minutes"] as const;

export function useMinutes(meetingId: string, enabled: boolean) {
  return useQuery({ queryKey: minutesQueryKey(meetingId), queryFn: () => api.getMinutes(meetingId), enabled });
}

/**
 * Ask the API for the minutes PDF (rendered, or reused when unchanged).
 *
 * The links it returns are short-lived presigned URLs, so they are requested
 * when the user asks for the PDF rather than when the page loads.
 */
export function useMinutesPdf(meetingId: string) {
  return useMutation({ mutationFn: () => api.minutesPdf(meetingId) });
}

/** Start a download from a URL whose response is sent as an attachment. */
export function downloadFrom(url: string): void {
  const link = document.createElement("a");
  link.href = url;
  link.rel = "noopener";
  document.body.append(link);
  link.click();
  link.remove();
}
