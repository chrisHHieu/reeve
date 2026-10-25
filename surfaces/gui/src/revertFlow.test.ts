// The revert flow at the component-model level: stage → boundary + draft,
// redo → walk forward or clear. Uses the real itemsFromMessages projection so the gate
// predicates and the transcript the user sees are exercised together.
import { describe, expect, it } from "vitest";
import { itemsFromMessages } from "./itemsFromMessages";
import { canRedoMarker, canUndoItems, revertBoundaryAt, revertDraft } from "./revertFlow";

const MESSAGES = [
  { role: "system", content: "be helpful" },
  { role: "user", content: "first question" },
  { role: "assistant", content: "first answer" },
  { role: "user", content: "second question" },
  { role: "assistant", content: "second answer" },
] as any;

describe("revert flow", () => {
  it("undo stages a boundary and the last user text comes back as the draft", () => {
    const items = itemsFromMessages(MESSAGES, { boundary: 3, staged_at: 5 });

    expect(revertBoundaryAt(items)).toBe(2); // user, assistant, boundary, user, assistant
    expect(canUndoItems(items)).toBe(true); // "first question" is still visible
    expect(canRedoMarker({ boundary: 3, staged_at: 5 })).toBe(true);

    const draft = revertDraft({
      ok: true,
      message: { index: 3, text: "second question" },
    });
    expect(draft).toEqual({ text: "second question", attachments: undefined });
  });

  it("undoing every user message closes the gate", () => {
    const items = itemsFromMessages(MESSAGES, { boundary: 1, staged_at: 5 });

    expect(canUndoItems(items)).toBe(false);
    expect(canRedoMarker({ boundary: 1, staged_at: 5 })).toBe(true);
  });

  it("redo clear removes the boundary and re-opens undo", () => {
    const staged = itemsFromMessages(MESSAGES, { boundary: 3, staged_at: 5 });
    const cleared = itemsFromMessages(MESSAGES, null);

    expect(revertBoundaryAt(cleared)).toBe(-1);
    expect(canUndoItems(cleared)).toBe(true);
    expect(canRedoMarker(null)).toBe(false);
    // Clearing drops exactly the boundary item; every message was always on screen.
    expect(cleared.length).toBe(staged.length - 1);
  });

  it("a refused revert returns no draft", () => {
    expect(revertDraft({ ok: false, error: "nothing to undo" })).toBeNull();
    expect(revertDraft({ ok: true, message: { index: 0, text: "" } })).toBeNull();
  });
});
