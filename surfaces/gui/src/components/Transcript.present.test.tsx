// An answer written beside `present` (owner, 2026-10-19): the model wrote its whole answer in the
// message that declared the files, so the answer sat inside "Worked" and the short line after the
// call read as the answer. `present` does no work, so prose that only calls it is the answer.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { Transcript } from "./Transcript";
import type { Item } from "../types";

afterEach(cleanup);

const ANSWER =
  "The plan has four stages. Stage 0 carries no risk and frees about 30 GB: clear the caches and " +
  "the build output, with a command to check after each step. Only one folder is in use by a " +
  "running container, so it moves last, once the container is stopped and its settings updated.";

const present = (id = "p1"): Item => ({
  kind: "tool",
  id,
  name: "present",
  args: { files: [{ path: "plan.md" }] },
  status: "ok",
});

const turn = (lead: string, tail: Item[] = [{ kind: "assistant", text: "The plan is ready in plan.md." }]): Item[] => [
  { kind: "user", text: "how should the drive be organised?" },
  { kind: "tool", id: "t1", name: "list_files", args: { path: "." }, status: "ok" },
  { kind: "assistant", text: lead, withCalls: true },
  present(),
  ...tail,
];

const inAnswer = (text: string) => !!screen.getByText(text, { exact: false }).closest(".turn-answer");
const onRail = (text: string) => !!screen.getByText(text, { exact: false }).closest(".turn-rail");

describe("prose beside a present-only step", () => {
  it("is part of the answer, ahead of the line written after the call", () => {
    render(<Transcript items={turn(ANSWER)} onApprove={vi.fn()} />);
    expect(inAnswer("The plan has four stages.")).toBe(true);
    expect(onRail("The plan has four stages.")).toBe(false);
    expect(inAnswer("The plan is ready in plan.md.")).toBe(true);
    const answers = [...document.querySelectorAll(".turn-answer .bubble-assistant")].map((b) => b.textContent ?? "");
    expect(answers.findIndex((t) => t.includes("four stages"))).toBeLessThan(
      answers.findIndex((t) => t.includes("ready in plan.md")),
    );
  });

  it("is the answer when the turn ended on the call", () => {
    render(<Transcript items={turn(ANSWER, [])} onApprove={vi.fn()} />);
    expect(inAnswer("The plan has four stages.")).toBe(true);
  });

  it("is the answer when it runs over more than one line, however short", () => {
    render(<Transcript items={turn("Two things to decide:\n- what Code153 is\n- who reads the cache")} onApprove={vi.fn()} />);
    expect(inAnswer("Two things to decide:")).toBe(true);
  });

  it("stays a step when it is a short lead-in", () => {
    render(<Transcript items={turn("Presenting the plan.")} onApprove={vi.fn()} />);
    expect(onRail("Presenting the plan.")).toBe(true);
  });

  it("stays a step beside a call that does work", () => {
    const items = turn(ANSWER);
    items[3] = { kind: "tool", id: "w1", name: "write_file", args: { path: "plan.md", content: "x" }, status: "ok" };
    render(<Transcript items={items} onApprove={vi.fn()} />);
    expect(onRail("The plan has four stages.")).toBe(true);
  });

  it("waits, live, until the turn answers or ends", () => {
    render(<Transcript items={turn(ANSWER, [])} onApprove={vi.fn()} running />);
    expect(onRail("The plan has four stages.")).toBe(true);
  });
});
