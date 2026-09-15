import { describe, expect, it } from "vitest";

import { daysUntil, describeDeadline, formatBytes, formatDate, formatDuration } from "./format";
import { inputProblem, sourceTypeFor } from "./meetingInput";

const today = new Date(2026, 8, 14, 15, 30); // 14 Sep 2026, local afternoon

describe("deadlines", () => {
  it("counts whole calendar days regardless of the time of day", () => {
    expect(daysUntil("2026-09-14", today)).toBe(0);
    expect(daysUntil("2026-09-15", today)).toBe(1);
    expect(daysUntil("2026-09-11", today)).toBe(-3);
  });

  it("describes deadlines in plain language", () => {
    expect(describeDeadline(null, today)).toBe("No deadline");
    expect(describeDeadline("2026-09-14", today)).toBe("Due today");
    expect(describeDeadline("2026-09-15", today)).toBe("Due tomorrow");
    expect(describeDeadline("2026-09-13", today)).toBe("1 day overdue");
    expect(describeDeadline("2026-09-10", today)).toBe("4 days overdue");
    expect(describeDeadline("2026-09-20", today)).toBe("Due in 6 days");
    expect(describeDeadline("2026-10-30", today)).toMatch(/^Due /);
  });
});

describe("formatting", () => {
  it("shows a bare date as that calendar day, not the day before in western time zones", () => {
    expect(formatDate("2026-09-16")).toContain("16");
  });

  it("formats sizes, durations, and missing values", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(4_937_822)).toBe("4.7 MB");
    expect(formatDuration(154)).toBe("2 min 34 s");
    expect(formatDuration(null)).toBe("—");
    expect(formatDate(null)).toBe("—");
  });
});

describe("inputProblem", () => {
  it("requires enough text for notes or a transcript, a matching file for audio or video, and nothing for 'add later'", () => {
    const wav = new File(["RIFF"], "standup.wav", { type: "audio/wav" });
    const mp4 = new File(["...."], "all-hands.mp4", { type: "video/mp4" });
    expect(inputProblem({ mode: "transcript", transcript: "  too short  ", file: null })).toMatch(/at least 20/);
    expect(inputProblem({ mode: "notes", transcript: "short", file: null })).toMatch(/meeting notes of at least 20/);
    expect(inputProblem({ mode: "notes", transcript: "Budget approved; Leela sends forecast.", file: null })).toBeNull();
    expect(inputProblem({ mode: "audio", transcript: "", file: null })).toMatch(/Choose an audio file/);
    expect(inputProblem({ mode: "audio", transcript: "", file: wav })).toBeNull();
    expect(inputProblem({ mode: "video", transcript: "", file: mp4 })).toBeNull();
    expect(inputProblem({ mode: "audio", transcript: "", file: mp4 })).toMatch(/is a video. Choose the Video option/);
    expect(inputProblem({ mode: "later", transcript: "", file: null })).toBeNull();
  });

  it("maps each input to the meeting's source type", () => {
    expect(["notes", "transcript", "audio", "video", "later"].map((m) => sourceTypeFor(m as never))).toEqual(["text", "text", "audio", "video", "text"]);
  });
});

describe("groupByUrgency", () => {
  it("orders overdue first and keeps closed work separate", async () => {
    const { groupByUrgency } = await import("./groups");
    const base = { status: "pending", deadline: null } as const;
    const item = (id: string, over: object) => ({ ...base, id, ...over }) as never;
    const groups = groupByUrgency(
      [
        item("later", { deadline: "2026-12-01" }),
        item("none", {}),
        item("done", { status: "done", deadline: "2026-09-01" }),
        item("week", { deadline: "2026-09-18" }),
        item("late", { deadline: "2026-09-10" }),
        item("today", { deadline: "2026-09-14", status: "in_progress" }),
      ],
      today,
    );
    expect(groups.map((g) => [g.key, g.items.map((i) => (i as { id: string }).id)])).toEqual([
      ["overdue", ["late"]],
      ["today", ["today"]],
      ["week", ["week"]],
      ["later", ["later"]],
      ["none", ["none"]],
      ["closed", ["done"]],
    ]);
  });
});

describe("matchStrength", () => {
  it("labels similarity in plain language without treating it as a percentage", async () => {
    const { matchStrength } = await import("./search");
    expect(matchStrength(0.62).label).toBe("Strong match");
    expect(matchStrength(0.45).label).toBe("Strong match");
    expect(matchStrength(0.28).label).toBe("Good match");
    expect(matchStrength(0.25).label).toBe("Good match");
    expect(matchStrength(0.05).label).toBe("Weak match");
    expect(matchStrength(-0.2).label).toBe("Weak match");
  });
});

describe("citations", () => {
  it("splits an answer into text and citation numbers, and links each source to the right place", async () => {
    const { splitCitations, sourceHref } = await import("./ask");
    expect(splitCitations("A [1]. B [2][3].")).toEqual(["A ", 1, ". B ", 2, 3, "."]);
    expect(splitCitations("No citations")).toEqual(["No citations"]);
    const base = { number: 1, meeting_id: "m1", meeting_title: "T", meeting_date: "2026-09-10T10:00:00Z", text: "x", score: 0.5 };
    expect(sourceHref({ ...base, kind: "transcript", char_start: 5, char_end: 9 })).toBe("/meetings/m1?tab=transcript&from=5&to=9");
    expect(sourceHref({ ...base, kind: "decision", char_start: null, char_end: null })).toBe("/meetings/m1");
  });
});
