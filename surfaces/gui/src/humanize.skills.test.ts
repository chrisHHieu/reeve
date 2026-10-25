// The transcript's trust line for skills: a load_skill call always reads
// "Used skill: X", whether the model chose the skill or the person ran it with /skill.
import { describe, expect, it } from "vitest";
import { humanizeTool } from "./humanize";

describe("humanizeTool(load_skill)", () => {
  it("names the skill that was used", () => {
    expect(humanizeTool("load_skill", { name: "incident-summary" })).toEqual({ pre: "Used skill: ", obj: "incident-summary" });
  });

  it("holds up with no arguments or empty ones", () => {
    expect(humanizeTool("load_skill", null).obj).toBe("");
    expect(humanizeTool("load_skill", {}).obj).toBe("");
  });
});
