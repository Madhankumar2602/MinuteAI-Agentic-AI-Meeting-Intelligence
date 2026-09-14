import { describe, expect, it, vi } from "vitest";

import { configureApiClient } from "./client";
import { mediaTypeFor, uploadRecording, validateRecording } from "./upload";
import { apiError, mockApi } from "../test/mockApi";

const MB = 1024 * 1024;
const file = (name: string, type: string, size = 1024) => new File([new Uint8Array(size)], name, { type });

describe("mediaTypeFor", () => {
  it("uses the browser-reported type when present", () => {
    expect(mediaTypeFor(file("a.wav", "audio/wav"))).toBe("audio/wav");
  });

  it("infers from the extension when the browser reports nothing useful", () => {
    expect(mediaTypeFor(file("standup.m4a", ""))).toBe("audio/mp4");
    expect(mediaTypeFor(file("clip.MOV", "application/octet-stream"))).toBe("video/quicktime");
  });
});

describe("validateRecording", () => {
  it("accepts supported audio and video", () => {
    expect(validateRecording(file("a.mp3", "audio/mpeg"), 10 * MB)).toBeNull();
    expect(validateRecording(file("b.mp4", "video/mp4"), 10 * MB)).toBeNull();
  });

  it("rejects unsupported, empty, and oversized files with a readable reason", () => {
    expect(validateRecording(file("notes.pdf", "application/pdf"), 10 * MB)).toMatch(/not a supported/);
    expect(validateRecording(file("empty.wav", "audio/wav", 0), 10 * MB)).toMatch(/empty/);
    expect(validateRecording(file("big.wav", "audio/wav", 3 * MB), 2 * MB)).toMatch(/larger than the 2 MB limit/);
  });
});

/** Minimal XMLHttpRequest stand-in: records the form and reports success. */
function stubStorage(status = 204) {
  const sent: FormData[] = [];
  class FakeXhr {
    status = 0;
    upload = { onprogress: null as ((e: ProgressEvent) => void) | null };
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    onabort: (() => void) | null = null;
    open() {}
    abort() {}
    send(form: FormData) {
      sent.push(form);
      this.upload.onprogress?.({ lengthComputable: true, loaded: 5, total: 10 } as ProgressEvent);
      this.status = status;
      this.onload?.();
    }
  }
  vi.stubGlobal("XMLHttpRequest", FakeXhr);
  return sent;
}

const TICKET = {
  upload_url: "http://storage.local/bucket",
  fields: { key: "users/u/meetings/m/source/x.wav", "Content-Type": "audio/wav", policy: "p", "x-amz-signature": "s" },
  upload_token: "upload-token-1234567890",
  expires_in: 900,
  max_bytes: 200 * MB,
};

describe("uploadRecording", () => {
  it("asks for a URL, posts policy fields before the file, then confirms", async () => {
    configureApiClient({ getToken: () => "t", onUnauthorized: () => undefined });
    const { requests } = mockApi({
      "POST /api/v1/meetings/m1/media/upload-url": TICKET,
      "POST /api/v1/meetings/m1/media/complete": { media: {}, job: {} },
    });
    const sent = stubStorage();
    const progress: number[] = [];

    await uploadRecording("m1", file("standup.wav", "audio/wav"), { onProgress: (f) => progress.push(f) });

    expect(requests[0]!.body).toEqual({ filename: "standup.wav", content_type: "audio/wav", size_bytes: 1024 });
    const names = [...sent[0]!.keys()];
    expect(names.at(-1)).toBe("file"); // S3 ignores fields that come after the file
    expect(names.slice(0, -1)).toEqual(Object.keys(TICKET.fields));
    expect(progress).toEqual([0.5, 1]);
    expect(requests[1]!.body).toEqual({ upload_token: TICKET.upload_token, replace_manual_transcript: false });
  });

  it("re-confirms with replace=true only if the user agrees to discard a typed transcript", async () => {
    configureApiClient({ getToken: () => "t", onUnauthorized: () => undefined });
    let calls = 0;
    const { requests } = mockApi({
      "POST /api/v1/meetings/m1/media/upload-url": TICKET,
      "POST /api/v1/meetings/m1/media/complete": () =>
        ++calls === 1 ? apiError(409, "manual_transcript_exists", "Typed transcript exists.") : { body: { media: {}, job: {} } },
    });
    stubStorage();
    const confirm = vi.fn(() => true);

    await uploadRecording("m1", file("a.wav", "audio/wav"), { confirmReplaceTranscript: confirm });

    expect(confirm).toHaveBeenCalledOnce();
    const completes = requests.filter((r) => r.path.endsWith("/complete"));
    expect(completes.map((r) => (r.body as { replace_manual_transcript: boolean }).replace_manual_transcript)).toEqual([false, true]);
  });

  it("surfaces the conflict when the user declines", async () => {
    configureApiClient({ getToken: () => "t", onUnauthorized: () => undefined });
    mockApi({
      "POST /api/v1/meetings/m1/media/upload-url": TICKET,
      "POST /api/v1/meetings/m1/media/complete": apiError(409, "manual_transcript_exists", "Typed transcript exists."),
    });
    stubStorage();

    await expect(
      uploadRecording("m1", file("a.wav", "audio/wav"), { confirmReplaceTranscript: () => false }),
    ).rejects.toMatchObject({ code: "manual_transcript_exists" });
  });

  it("reports a storage rejection without calling complete", async () => {
    configureApiClient({ getToken: () => "t", onUnauthorized: () => undefined });
    const { requests } = mockApi({ "POST /api/v1/meetings/m1/media/upload-url": TICKET });
    stubStorage(400);

    await expect(uploadRecording("m1", file("a.wav", "audio/wav"))).rejects.toMatchObject({ code: "storage_rejected" });
    expect(requests.some((r) => r.path.endsWith("/complete"))).toBe(false);
  });
});
