import { describe, expect, it } from "vitest";

import { daysUntil, describeDeadline, formatBytes, formatDate, formatDuration } from "./format";
import { inputProblem } from "./meetingInput";

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
  it("requires enough transcript text, or a valid recording, but nothing for 'add later'", () => {
    expect(inputProblem({ mode: "transcript", transcript: "  too short  ", file: null })).toMatch(/at least 20/);
    expect(inputProblem({ mode: "transcript", transcript: "Priya: this is a long enough line.", file: null })).toBeNull();
    expect(inputProblem({ mode: "recording", transcript: "", file: null })).toMatch(/Choose a recording/);
    expect(inputProblem({ mode: "later", transcript: "", file: null })).toBeNull();
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
