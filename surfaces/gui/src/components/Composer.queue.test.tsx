// @vitest-environment jsdom
// Queueing a message mid-turn.
//
// The engine reads one turn at a time, so before this the composer simply refused the
// keystroke while the agent worked: the draft stayed on screen and Enter did nothing, which
// reads as a broken input. A sentence typed during a long run is a real intent, so it is
// parked (with its attachments and skill) and the surface sends it when the turn ends.

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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

function typeAndSend(value: string) {
  const box = screen.getByRole("textbox");
  fireEvent.change(box, { target: { value } });
  fireEvent.keyDown(box, { key: "Enter" });
}

describe("Composer / queueing while the agent works", () => {
  it("queues instead of sending when a turn is running", () => {
    const onSend = vi.fn();
    const onQueue = vi.fn();
    render(<Composer {...props({ running: true, onSend, onQueue })} />);
    typeAndSend("also update the changelog");
    expect(onQueue).toHaveBeenCalledWith("also update the changelog", [], undefined);
    expect(onSend).not.toHaveBeenCalled();
    // The draft clears: it now lives in the queue banner, not in the box.
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("");
  });

  it("still refuses mid-turn when the surface has no queue", () => {
    const onSend = vi.fn();
    render(<Composer {...props({ running: true, onSend })} />);
    typeAndSend("hello");
    expect(onSend).not.toHaveBeenCalled();
  });

  it("sends normally when nothing is running", () => {
    const onSend = vi.fn();
    const onQueue = vi.fn();
    render(<Composer {...props({ onSend, onQueue })} />);
    typeAndSend("go");
    expect(onSend).toHaveBeenCalledWith("go", [], undefined);
    expect(onQueue).not.toHaveBeenCalled();
  });

  it("keeps the corner for Stop alone: no Queue button, and no unlabeled button beside it", () => {
    render(<Composer {...props({ running: true, onQueue: vi.fn(), onSteer: vi.fn() })} />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "next" } });
    expect(screen.queryByTestId("queue-send")).toBeNull();
    expect(screen.queryByTestId("steer-send")).toBeNull();
    expect(screen.getByTestId("composer-stop")).toBeTruthy();
  });

  it("says what will happen to a message typed mid-turn", () => {
    render(<Composer {...props({ running: true, onQueue: vi.fn() })} />);
    expect(screen.getByRole("textbox").getAttribute("placeholder")).toBe("Enter queues it for after this turn");
  });

  it("tells Ctrl+Enter apart from plain Enter while the agent works, in a line short enough not to wrap", () => {
    render(<Composer {...props({ running: true, onQueue: vi.fn(), onSteer: vi.fn() })} />);
    const ph = screen.getByRole("textbox").getAttribute("placeholder") || "";
    expect(ph).toBe("Enter queues it, Ctrl+Enter sends it now");
    // The old sentence was 85 characters and wrapped to two lines in a rail-open column.
    expect(ph.length).toBeLessThanOrEqual(44);
  });

  it("Ctrl+Enter steers plain text into the running turn", () => {
    const onSteer = vi.fn();
    const onQueue = vi.fn();
    render(<Composer {...props({ running: true, onQueue, onSteer })} />);
    const box = screen.getByRole("textbox");
    fireEvent.change(box, { target: { value: "wait — use the other table" } });
    fireEvent.keyDown(box, { key: "Enter", ctrlKey: true });
    expect(onSteer).toHaveBeenCalledWith("wait — use the other table");
    expect(onQueue).not.toHaveBeenCalled();
    expect((box as HTMLTextAreaElement).value).toBe("");
  });

  it("Ctrl+Enter without a steer handler still queues", () => {
    const onQueue = vi.fn();
    render(<Composer {...props({ running: true, onQueue })} />);
    const box = screen.getByRole("textbox");
    fireEvent.change(box, { target: { value: "later" } });
    fireEvent.keyDown(box, { key: "Enter", ctrlKey: true });
    expect(onQueue).toHaveBeenCalledWith("later", [], undefined);
  });

  it("Ctrl+Enter with an attachment falls back to queue — steer is text-only", () => {
    const onSteer = vi.fn();
    const onQueue = vi.fn();
    render(
      <Composer
        {...props({
          running: true,
          onQueue,
          onSteer,
          prefill: {
            text: "check this",
            attachments: [{ kind: "text", name: "a.txt", text: "hi" }],
            nonce: 1,
          },
        })}
      />,
    );
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter", ctrlKey: true });
    expect(onSteer).not.toHaveBeenCalled();
    expect(onQueue).toHaveBeenCalled();
  });

  it("shows the waiting prompts, in order, and lets one be pulled back", () => {
    const onUnqueue = vi.fn();
    render(
      <Composer
        {...props({
          running: true,
          onQueue: vi.fn(),
          onUnqueue,
          queued: [
            { id: "q1", text: "first thing" },
            { id: "q2", text: "second thing", skill: "review" },
          ],
        })}
      />,
    );
    // Rows of the composer's own card, not a card of their own above it.
    const list = screen.getByTestId("queued-prompts");
    expect(list.closest(".composer")).not.toBeNull();
    expect(screen.getAllByTestId("queued-prompt").map((row) => row.textContent)).toEqual([
      expect.stringContaining("first thing"),
      expect.stringContaining("/review"),
    ]);
    expect(screen.getAllByTestId("queued-prompt-when").map((n) => n.textContent)).toEqual(["after this turn", "after this turn"]);
    fireEvent.click(screen.getAllByLabelText("Remove from the queue")[0]);
    expect(onUnqueue).toHaveBeenCalledWith("q1");
  });

  it("hands a queued message to the running turn with Send now, and takes it off the queue", () => {
    const onSteer = vi.fn();
    const onUnqueue = vi.fn();
    render(
      <Composer
        {...props({
          running: true,
          onQueue: vi.fn(),
          onSteer,
          onUnqueue,
          queued: [
            { id: "q1", text: "first thing" },
            { id: "q2", text: "second thing", skill: "review" },
            { id: "q3", text: "third thing", mentions: ["worker"] },
          ],
        })}
      />,
    );
    // Only plain text can join a running turn: a skill or a mention frames a new one, and
    // the server would refuse the splice, so those rows do not offer it.
    const rows = screen.getAllByTestId("queued-prompt");
    expect(rows.map((row) => !!row.querySelector('[data-testid="queued-prompt-now"]'))).toEqual([true, false, false]);
    fireEvent.click(screen.getByTestId("queued-prompt-now"));
    expect(onSteer).toHaveBeenCalledWith("first thing");
    expect(onUnqueue).toHaveBeenCalledWith("q1");
  });

  it("offers no Send now when nothing is running to send it into", () => {
    render(<Composer {...props({ onQueue: vi.fn(), onSteer: vi.fn(), queued: [{ id: "q1", text: "first thing" }] })} />);
    expect(screen.queryByTestId("queued-prompt-now")).toBeNull();
  });

  it("shows three queued messages and folds the rest into one line that opens", () => {
    const queued = Array.from({ length: 6 }, (_, i) => ({ id: `q${i}`, text: `message ${i + 1}` }));
    render(<Composer {...props({ running: true, onQueue: vi.fn(), queued })} />);
    expect(screen.getAllByTestId("queued-prompt")).toHaveLength(3);
    const toggle = screen.getByTestId("queued-prompts-toggle");
    expect(toggle.parentElement?.textContent).toContain("3 more are queued");
    fireEvent.click(toggle);
    expect(screen.getAllByTestId("queued-prompt")).toHaveLength(6);
    expect(screen.getByTestId("queued-prompts-toggle").textContent).toBe("Show fewer");
  });

  it("renders no banner when the queue is empty", () => {
    render(<Composer {...props({ onQueue: vi.fn(), queued: [] })} />);
    expect(screen.queryByTestId("queued-prompts")).toBeNull();
  });

  it("sends the draft into the running turn on Ctrl+Enter, and steers instead of queueing", () => {
    const onSteer = vi.fn();
    const onQueue = vi.fn();
    render(<Composer {...props({ running: true, onQueue, onSteer })} />);
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "wait — use the other table" },
    });
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter", ctrlKey: true });
    expect(onSteer).toHaveBeenCalledWith("wait — use the other table");
    expect(onQueue).not.toHaveBeenCalled();
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("");
  });

  it("keeps Enter as Queue when Steer is available", () => {
    const onSteer = vi.fn();
    const onQueue = vi.fn();
    render(<Composer {...props({ running: true, onQueue, onSteer })} />);
    typeAndSend("also update the changelog");
    expect(onQueue).toHaveBeenCalledWith("also update the changelog", [], undefined);
    expect(onSteer).not.toHaveBeenCalled();
  });

  it("withdraws Steer as soon as the draft carries an @mention", () => {
    const onSteer = vi.fn();
    render(
      <Composer
        {...props({
          running: true,
          onQueue: vi.fn(),
          onSteer,
          agents: [{ name: "worker", description: "worker agent" }],
        })}
      />,
    );
    // With a mention the draft frames a new turn: Ctrl+Enter does not splice it in.
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "@worker check this" },
    });
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter", ctrlKey: true });
    expect(onSteer).not.toHaveBeenCalled();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "check this" } });
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter", ctrlKey: true });
    expect(onSteer).toHaveBeenCalledWith("check this");
  });
});
