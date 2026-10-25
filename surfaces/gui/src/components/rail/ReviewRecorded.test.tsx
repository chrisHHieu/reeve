// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ReviewRecorded } from "./ReviewRecorded";
import { recordedChangesFromItems } from "../../reviewChanges";
import type { Item } from "../../types";

vi.mock("../../api", () => ({
  undoToolCall: vi.fn().mockResolvedValue({ ok: true, restored: ["src/a.ts"] }),
}));

afterEach(cleanup);

const items: Item[] = [
  {
    kind: "tool",
    id: "t1",
    callId: "c1",
    name: "write_file",
    args: {},
    status: "success",
    undoable: true,
    presentation: {
      summary: "wrote src/a.ts",
      card: {
        card: "diff",
        diffs: [{ path: "src/a.ts", old_text: null, new_text: "one\ntwo\n", added: 2, removed: 0 }],
      },
    },
  } as Item,
];

describe("ReviewRecorded", () => {
  it("says so when the conversation recorded nothing", () => {
    render(<ReviewRecorded sessionId="s1" changes={[]} />);
    expect(screen.getByTestId("recorded-empty").textContent).toContain(
      "Reeve hasn't changed any files in this session",
    );
  });

  it("lists the changes with a summary, expands the diff, and undoes one", async () => {
    const onChanged = vi.fn();
    const changes = recordedChangesFromItems(items);
    render(<ReviewRecorded sessionId="s1" changes={changes} onChanged={onChanged} />);

    expect(screen.getByText(/Reeve changed 1 file in 1 turn/)).toBeTruthy();
    const card = screen.getByTestId("recorded-change-src/a.ts");
    expect(card.textContent).toContain("+2");

    // A short list shows its diffs without a click; the header folds one away.
    const body = screen.getByTestId("recorded-diff-src/a.ts");
    expect(body.textContent).toContain("one");
    expect(body.textContent).toContain("two");
    fireEvent.click(screen.getByTestId("recorded-change-src/a.ts").querySelector("button")!);
    expect(screen.queryByTestId("recorded-diff-src/a.ts")).toBeNull();

    const { undoToolCall } = await import("../../api");
    fireEvent.click(screen.getByTestId("recorded-undo-src/a.ts"));
    await waitFor(() => expect(undoToolCall).toHaveBeenCalledWith("s1", "c1"));
    expect(await screen.findByText("Undone")).toBeTruthy();
    expect(onChanged).toHaveBeenCalled();
  });

  it("offers no undo when the engine did not snapshot the call", () => {
    const changes = recordedChangesFromItems([
      {
        kind: "tool",
        id: "t2",
        callId: "c2",
        name: "write_file",
        args: {},
        status: "success",
        presentation: {
          summary: "wrote b",
          card: { card: "diff", diffs: [{ path: "b.md", old_text: "a\n", new_text: "a\nb\n" }] },
        },
      } as Item,
    ]);
    render(<ReviewRecorded sessionId="s1" changes={changes} />);
    expect(screen.queryByTestId("recorded-undo-b.md")).toBeNull();
    // V4: said once for the list, not repeated on every card.
    expect(screen.getByTestId("recorded-undo-note").textContent).toContain("Undo isn't available");
    expect(screen.queryByText("Undo unavailable for this change")).toBeNull();
  });

  it("shows one row per file, relative to the workspace, and undoes its edits newest first", async () => {
    // V3 + V7: two edits of one file were two cards under an absolute path that squeezed
    // the file name to "ca…".
    const edit = (callId: string, oldText: string, newText: string) =>
      ({
        kind: "tool",
        id: callId,
        callId,
        name: "replace_in_file",
        args: {},
        status: "success",
        undoable: true,
        presentation: {
          summary: "edit",
          card: { card: "diff", diffs: [{ path: "/w/orders/cart.py", old_text: oldText, new_text: newText, added: 1, removed: 1 }] },
        },
      }) as Item;
    const changes = recordedChangesFromItems([edit("e1", "a\n", "b\n"), edit("e2", "b\n", "c\n")]);
    render(<ReviewRecorded sessionId="s1" changes={changes} roots={["/w"]} />);

    const cards = screen.getAllByTestId(/^recorded-change-/);
    expect(cards).toHaveLength(1);
    expect(cards[0].textContent).toContain("cart.py");
    expect(cards[0].textContent).toContain("orders");
    expect(cards[0].textContent).not.toContain("/w/");
    // The tool names are a developer's detail: the row's tooltip, not its text.
    expect(cards[0].textContent).not.toContain("replace_in_file");
    expect(cards[0].querySelector(".rev-change-head")!.getAttribute("title")).toContain("replace_in_file ×2");
    expect(cards[0].textContent).toContain("+2");

    const { undoToolCall } = await import("../../api");
    vi.mocked(undoToolCall).mockClear();
    fireEvent.click(screen.getByTestId("recorded-undo-/w/orders/cart.py"));
    await waitFor(() => expect(undoToolCall).toHaveBeenCalledTimes(2));
    expect(vi.mocked(undoToolCall).mock.calls.map((call) => call[1])).toEqual(["e2", "e1"]);
  });

  it("draws the diff with the transcript's rows: one gutter, real line numbers", () => {
    const changes = recordedChangesFromItems(items);
    render(<ReviewRecorded sessionId="s1" changes={changes} />);
    const body = screen.getByTestId("recorded-diff-src/a.ts");
    // The transcript's diff markup (sticky gutter, fills, stripes), not the old flat rows.
    expect(body.querySelector(".rv-diff-rows")).toBeTruthy();
    const numbers = [...body.querySelectorAll(".rv-diff-no")].map((el) => el.textContent);
    expect(numbers).toContain("1");
    expect(numbers).toContain("2");
    expect(body.querySelector(".rv-diff-text")?.textContent).toContain("one");
  });

  it("right-clicking a change header offers the file's own menu", () => {
    const writes: string[] = [];
    Object.assign(navigator, {
      clipboard: {
        writeText: vi.fn((text: string) => {
          writes.push(text);
          return Promise.resolve();
        }),
      },
    });
    const changes = recordedChangesFromItems(items);
    render(<ReviewRecorded sessionId="s1" changes={changes} />);
    fireEvent.contextMenu(screen.getByTestId("recorded-change-src/a.ts").querySelector("button")!);
    expect(screen.getByTestId("recorded-menu-src/a.ts").textContent).toContain("Open file");
    fireEvent.click(screen.getByText("Copy path"));
    expect(writes).toEqual(["src/a.ts"]);
    // A pick puts the menu away.
    expect(screen.queryByTestId("recorded-menu-src/a.ts")).toBeNull();
  });
});

describe("ReviewRecorded by turn", () => {
  const turnEdit = (id: string, path: string, oldText: string, newText: string) =>
    ({
      kind: "tool",
      id,
      callId: id,
      name: "replace_in_file",
      status: "ok",
      undoable: true,
      presentation: { card: { card: "diff", diffs: [{ path, old_text: oldText, new_text: newText }] } },
    }) as any;
  const user = (text: string) => ({ kind: "user", text }) as any;

  it("groups the session's changes by the message that asked for them, newest first", () => {
    const changes = recordedChangesFromItems([
      user("fix the parser"),
      turnEdit("e1", "a.py", "a\n", "b\n"),
      user("add retry to upload"),
      turnEdit("e2", "up.py", "x\n", "y\n"),
      turnEdit("e3", "t.py", "", "z\n"),
    ]);
    render(<ReviewRecorded sessionId="s1" changes={changes} />);
    const turns = screen.getAllByTestId(/^recorded-turn-/);
    expect(turns.map((t) => t.getAttribute("data-testid"))).toEqual(["recorded-turn-2", "recorded-turn-1"]);
    expect(turns[0].textContent).toContain("Turn 2");
    expect(turns[0].textContent).toContain("add retry to upload");
    expect(screen.getByText(/Reeve changed 3 files in 2 turns/)).toBeTruthy();
    // A turn with several undoable files offers Undo turn; a one-file turn uses the file's.
    expect(screen.getByTestId("recorded-undo-turn-2")).toBeTruthy();
    expect(screen.queryByTestId("recorded-undo-turn-1")).toBeNull();
  });

  it("undoes a whole turn newest first", async () => {
    const changes = recordedChangesFromItems([
      user("add retry"),
      turnEdit("e2", "up.py", "x\n", "y\n"),
      turnEdit("e3", "t.py", "", "z\n"),
    ]);
    const { undoToolCall } = await import("../../api");
    vi.mocked(undoToolCall).mockClear();
    render(<ReviewRecorded sessionId="s1" changes={changes} />);
    fireEvent.click(screen.getByTestId("recorded-undo-turn-1"));
    await waitFor(() => expect(vi.mocked(undoToolCall).mock.calls.map((c) => c[1])).toEqual(["e3", "e2"]));
  });

  it("holds an earlier turn's undo while a later turn changed the same file", () => {
    const changes = recordedChangesFromItems([
      user("first"),
      turnEdit("e1", "a.py", "a\n", "b\n"),
      user("second"),
      turnEdit("e2", "a.py", "b\n", "c\n"),
    ]);
    render(<ReviewRecorded sessionId="s1" changes={changes} />);
    // Restoring turn 1's copy would overwrite turn 2's edit, so turn 1 waits.
    const blocked = screen.getByTestId("recorded-blocked-a.py");
    expect(blocked.textContent).toBe("Undo Turn 2 first");
    expect(blocked.getAttribute("title")).toContain("Changed again in Turn 2");
    expect(screen.getAllByTestId("recorded-undo-a.py")).toHaveLength(1);
  });

  it("opens the file Files asked about", () => {
    const changes = recordedChangesFromItems([user("x"), turnEdit("e1", "a.py", "a\n", "b\n")]);
    render(<ReviewRecorded sessionId="s1" changes={changes} focusPath="a.py" />);
    expect(screen.getByTestId("recorded-diff-a.py")).toBeTruthy();
  });

  it("keeps a long list a list: diffs stay folded past four files", () => {
    const many = ["a", "b", "c", "d", "e"].map(
      (name, i) =>
        ({
          kind: "tool",
          id: `t${i}`,
          callId: `c${i}`,
          name: "write_file",
          args: {},
          status: "success",
          undoable: true,
          presentation: {
            summary: name,
            card: { card: "diff", diffs: [{ path: `src/${name}.ts`, old_text: null, new_text: "x\n", added: 1, removed: 0 }] },
          },
        }) as Item,
    );
    render(<ReviewRecorded sessionId="s1" changes={recordedChangesFromItems(many)} />);
    expect(screen.queryByTestId("recorded-diff-src/a.ts")).toBeNull();
  });
});
