// The file tree's git vocabulary, ported from the reference design: M/A/D/R/U letters in mono, the name
// tinted by the same status, a folder dot when anything below it changed, and indent guides
// drawn from the row's depth variable.
// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("../../api", async () => {
  const actual = await vi.importActual<Record<string, unknown>>("../../api");
  return {
    ...actual,
    revealPath: vi.fn(async () => ({ ok: true })),
    readArtifact: vi.fn(async () => ({
      kind: "folder",
      entries: [{ name: "agent.py", dir: false, size: 12 }],
    })),
  };
});

import { revealPath } from "../../api";
import { FileTree } from "../RightRail";

afterEach(cleanup);

const roots = [{ path: "proj", label: "proj", writable: true, exists: true }];

describe("FileTree", () => {
  it("reads a changed file the way git does: the letter, the tint, and a dot on the folder", async () => {
    render(
      <FileTree
        sessionId="s"
        roots={roots as never}
        onSelect={vi.fn()}
        changedStatus={(path) => (path.endsWith("agent.py") ? "M" : undefined)}
        changedBelow={(path) => path === "proj"}
      />,
    );
    // Open the root, then the file row appears with its status.
    fireEvent.click(screen.getByLabelText("Expand proj"));
    await waitFor(() => expect(screen.getByTestId("tree-st-proj/agent.py")).toBeTruthy());
    const status = screen.getByTestId("tree-st-proj/agent.py");
    expect(status.textContent).toBe("M");
    expect(status.getAttribute("data-type")).toBe("modified");
    // The name carries the same status, and the folder a dot.
    const name = screen.getByText("agent.py");
    expect(name.getAttribute("data-st")).toBe("modified");
    expect(document.querySelector(".tree-dot")).toBeTruthy();
  });

  it("marks an untracked file U, not the git plumbing '??'", async () => {
    render(
      <FileTree
        sessionId="s"
        roots={roots as never}
        onSelect={vi.fn()}
        changedStatus={() => "??"}
      />,
    );
    fireEvent.click(screen.getByLabelText("Expand proj"));
    await waitFor(() => expect(screen.getByTestId("tree-st-proj/agent.py")).toBeTruthy());
    expect(screen.getByTestId("tree-st-proj/agent.py").textContent).toBe("U");
  });

  it("walks the tree with the arrows: → opens a folder, ↓ steps into it", async () => {
    render(<FileTree sessionId="s" roots={roots as never} onSelect={vi.fn()} />);
    const root = screen.getByTestId("files-root-row");
    root.focus();
    fireEvent.keyDown(root, { key: "ArrowRight" });
    await waitFor(() => expect(screen.getByText("agent.py")).toBeTruthy());
    fireEvent.keyDown(root, { key: "ArrowDown" });
    expect(document.activeElement?.textContent).toContain("agent.py");
    // ← closes the folder again (focus stays where the reader is).
    fireEvent.keyDown(root, { key: "ArrowLeft" });
    await waitFor(() => expect(screen.queryByText("agent.py")).toBeNull());
  });

  it("drags a FILE as a workspace path, and a folder not at all", async () => {
    render(
      <FileTree sessionId="s" roots={roots as never} onSelect={vi.fn()} />,
    );
    fireEvent.click(screen.getByLabelText("Expand proj"));
    await waitFor(() => expect(screen.getByText("agent.py")).toBeTruthy());
    const drag = (el: Element) => {
      const data: Record<string, string> = {};
      const transfer = {
        setData: (type: string, value: string) => {
          data[type] = value;
        },
        effectAllowed: "",
      };
      fireEvent.dragStart(el, { dataTransfer: transfer });
      return { data, transfer };
    };
    // The drag-to-chat payload: the path, under both our MIME and text/plain.
    const file = drag(screen.getByText("agent.py"));
    expect(file.data["application/x-reeve-file"]).toBe("proj/agent.py");
    expect(file.data["text/plain"]).toBe("proj/agent.py");
    expect(file.transfer.effectAllowed).toBe("copy");
    // A folder has nothing to attach: it is not draggable.
    expect(screen.getByTestId("files-root-row").getAttribute("draggable")).toBe("false");
  });

  it("right-click offers open / copy path / reveal, and reveal asks the server", async () => {
    const onSelect = vi.fn();
    render(<FileTree sessionId="s" roots={roots as never} onSelect={onSelect} />);
    fireEvent.click(screen.getByLabelText("Expand proj"));
    await waitFor(() => expect(screen.getByText("agent.py")).toBeTruthy());

    fireEvent.contextMenu(screen.getByText("agent.py"));
    const menu = screen.getByTestId("tree-menu");
    expect(menu.textContent).toContain("Copy path");
    // The path goes to the server, which owns the workspace root and the boundary check.
    fireEvent.click(screen.getByText("Reveal in file manager"));
    await waitFor(() => expect(revealPath).toHaveBeenCalledWith("s", "proj/agent.py"));
    expect(screen.queryByTestId("tree-menu")).toBeNull();

    // A folder row's menu opens the listing instead of a preview.
    fireEvent.contextMenu(screen.getByTestId("files-root-row"));
    expect(screen.getByTestId("tree-menu").textContent).toContain("Open folder");
    fireEvent.click(screen.getByText("Open folder"));
    expect(onSelect).not.toHaveBeenCalled();
  });
});
