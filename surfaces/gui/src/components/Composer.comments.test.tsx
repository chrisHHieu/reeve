// @vitest-environment jsdom
// Pending line comments become chips above the input: the pending
// set is visible, labelled with file:line, and removable before the message is sent.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { Composer } from "./Composer";

afterEach(cleanup);

const props = (over: Record<string, unknown> = {}) => ({
  mode: "interactive",
  model: "gpt-5.6-sol",
  running: false,
  connected: true,
  onSend: vi.fn(),
  onInterrupt: vi.fn(),
  onModeChange: vi.fn(),
  onModelChange: vi.fn(),
  ...over,
});

describe("Composer / line comment chips", () => {
  it("shows nothing when there are no pending comments", () => {
    render(<Composer {...props()} />);
    expect(screen.queryByTestId("composer-comments")).toBeNull();
  });

  it("renders one chip per comment with its text and file:line", () => {
    render(
      <Composer
        {...props({
          comments: [
            { id: "c1", file: "src/app.py", line: 12, text: "rename this", origin: "file" },
          ],
        })}
      />,
    );
    const row = screen.getByTestId("composer-comments");
    expect(row.textContent).toContain("rename this");
    expect(row.textContent).toContain("app.py:12");
  });

  it("removes a comment through its ✕", () => {
    const onRemoveComment = vi.fn();
    render(
      <Composer
        {...props({
          onRemoveComment,
          comments: [
            { id: "c1", file: "src/app.py", line: 12, text: "rename this", origin: "file" },
            { id: "c2", file: "src/util.ts", line: 3, text: "use a map", origin: "review" },
          ],
        })}
      />,
    );
    expect(screen.getAllByTestId(/composer-comment-/)).toHaveLength(2);
    fireEvent.click(screen.getByLabelText("Remove comment on app.py:12"));
    expect(onRemoveComment).toHaveBeenCalledWith("c1");
  });

  it("edits a comment through its chip and hands the new text up", () => {
    const onEditComment = vi.fn();
    render(
      <Composer
        {...props({
          onEditComment,
          comments: [
            { id: "c1", file: "src/app.py", line: 12, text: "rename this", origin: "file" },
          ],
        })}
      />,
    );
    // Click the chip → the editor replaces it, pre-filled with the comment.
    fireEvent.click(screen.getByTestId("composer-comment-c1"));
    const editor = screen.getByTestId("composer-comment-edit-c1");
    const area = editor.querySelector("textarea") as HTMLTextAreaElement;
    expect(area.value).toBe("rename this");
    fireEvent.change(area, { target: { value: "rename that" } });
    fireEvent.click(within(editor).getByRole("button", { name: "Save" }));
    expect(onEditComment).toHaveBeenCalledWith("c1", "rename that");
  });
});
