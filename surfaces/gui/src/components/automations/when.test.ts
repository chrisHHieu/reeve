import { describe, expect, it } from "vitest";
import { fromCron, toCron } from "./schedule";
import { runState, startedBy } from "./runState";
import { dayWord, took, whenText } from "./when";

const at = (y: number, m: number, d: number, h = 0, min = 0) => new Date(y, m - 1, d, h, min).getTime() / 1000;
const now = new Date(2026, 9, 11, 20, 30); // Sunday 11 Oct 2026, 20:30

describe("when a run was or will be", () => {
  it("is said relative to today while that is the shorter way", () => {
    expect(dayWord(at(2026, 10, 11, 21, 21), now, "en-GB")).toBe("today");
    expect(dayWord(at(2026, 10, 12, 0, 5), now, "en-GB")).toBe("tomorrow");
    expect(dayWord(at(2026, 10, 10, 23, 59), now, "en-GB")).toBe("yesterday");
    expect(dayWord(at(2026, 10, 14, 9), now, "en-GB")).toBe("Wed");
    expect(dayWord(at(2026, 10, 7, 9), now, "en-GB")).toBe("Wed");
  });

  it("is a date past a week, with the year once it is another year", () => {
    expect(dayWord(at(2026, 10, 2, 9), now, "en-GB")).toBe("2 Oct");
    expect(dayWord(at(2026, 11, 20, 9), now, "en-GB")).toBe("20 Nov");
    expect(dayWord(at(2025, 12, 31, 9), now, "en-GB")).toBe("31 Dec 2025");
  });

  it("joins the day and the time, and is empty for no time at all", () => {
    expect(whenText(at(2026, 10, 11, 21, 21), now, "en-GB")).toBe("today 21:21");
    expect(whenText(null, now, "en-GB")).toBe("");
    expect(whenText(0, now, "en-GB")).toBe("");
  });

  it("says how long a run took in whole seconds", () => {
    expect(took(41.4)).toBe("41s");
    expect(took(143)).toBe("2m 23s");
    expect(took(182)).toBe("3m 02s");
    expect(took(3900)).toBe("1h 05m");
    expect(took(-1)).toBe("");
  });
});

describe("the schedule the form can say", () => {
  it("writes a day choice and a time as a cron and reads it back", () => {
    for (const [day, time, cron] of [
      ["daily", "17:40", "40 17 * * *"],
      ["weekdays", "08:00", "0 8 * * 1-5"],
      ["weekends", "10:30", "30 10 * * 0,6"],
      ["mon", "09:00", "0 9 * * 1"],
      ["sun", "23:59", "59 23 * * 0"],
    ]) {
      expect(toCron(day, time)).toBe(cron);
      expect(fromCron(cron)).toEqual({ day, time });
    }
  });

  it("reads the other spellings of Sunday and the weekend", () => {
    expect(fromCron("0 9 * * 7")).toEqual({ day: "sun", time: "09:00" });
    expect(fromCron("0 9 * * 6,0")).toEqual({ day: "weekends", time: "09:00" });
  });

  it("does not pretend to read a schedule it cannot say", () => {
    // The old editor showed each of these as "every day at 09:00", and saving wrote that over them.
    for (const cron of ["*/15 * * * *", "0 9 1 * *", "0 9 * 6 *", "0 9,17 * * *", "0 9 * * 1,3", "nonsense", "", null, undefined]) {
      expect(fromCron(cron)).toBeNull();
    }
  });

  it("falls back to nine o'clock for a time that is not one", () => {
    expect(toCron("daily", "")).toBe("0 9 * * *");
    expect(toCron("nope", "25:99")).toBe("0 9 * * *");
  });
});

describe("what a run came to", () => {
  it("has one word for each of the server's states", () => {
    expect(runState({ status: "running" })).toMatchObject({ word: "Running", tone: "live" });
    expect(runState({ status: "ok" })).toMatchObject({ word: "Done", tone: "" });
    expect(runState({ status: "error" })).toMatchObject({ word: "Failed", tone: "bad" });
    expect(runState({ status: "skipped" })).toMatchObject({ word: "Skipped", tone: "" });
  });

  it("is Needs you while its conversation holds a prompt, whatever its status", () => {
    expect(runState({ status: "running", attention: 1, attention_kind: "approval" })).toEqual({
      word: "Needs you",
      tone: "warm",
      waitsFor: "Approval",
    });
    expect(runState({ status: "ok", attention: 2, attention_kind: "question" }).waitsFor).toBe("Question");
    // A kind this build has no word for is still a run that needs the person.
    expect(runState({ status: "running", attention: 1, attention_kind: "later" })).toMatchObject({ word: "Needs you", waitsFor: "" });
  });

  it("names who started a run", () => {
    expect(startedBy("schedule")).toBe("Schedule");
    expect(startedBy("manual")).toBe("You");
    expect(startedBy("catchup")).toBe("Schedule, late");
    expect(startedBy("webhook")).toBe("webhook");
  });
});
