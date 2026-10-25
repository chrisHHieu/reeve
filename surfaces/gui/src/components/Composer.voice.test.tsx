import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Composer } from "./Composer";

// The composer's microphone, against a stand-in for the desktop bridge: the microphone is
// the desktop app's alone, and a browser draws none.

const READY = {
  recording: false,
  model_installed: true,
  model_verified: true,
  test_passed: true,
  download_in_progress: false,
  model_name: "Whisper Base English (local)",
  model_bytes: 147964211,
  supported: true,
  device_summary: "macOS 15 · Apple Silicon",
  compatibility_reason: null,
};

/** The bridge's answers by command; anything not listed answers null. A function throws or
 *  computes its answer. */
type Answers = Record<string, unknown | (() => unknown)>;
let invoke: ReturnType<typeof vi.fn>;
function bridge(answers: Answers) {
  invoke = vi.fn(async (cmd: string) => {
    const answer = answers[cmd];
    return typeof answer === "function" ? (answer as () => unknown)() : (answer ?? null);
  });
  (globalThis as any).reeveDesktop = { invoke, on: () => () => {} };
}

const composer = (extra: Partial<Parameters<typeof Composer>[0]> = {}) => (
  <Composer mode="interactive" model="gpt-5.6-sol" running={false} connected onSend={vi.fn()} onInterrupt={vi.fn()} onModeChange={vi.fn()} onModelChange={vi.fn()} {...extra} />
);

beforeEach(() => bridge({ get_dictation_status: READY, start_dictation: { ...READY, recording: true }, stop_dictation: "hello from the mic" }));
afterEach(() => {
  cleanup();
  delete (globalThis as any).reeveDesktop;
});

describe("Composer voice input", () => {
  it("draws no microphone outside the desktop app", () => {
    delete (globalThis as any).reeveDesktop;
    render(composer());
    expect(screen.queryByLabelText(/dictation|Voice Input/)).toBeNull();
  });

  it("sends a device not set up yet to Settings, and records nothing", async () => {
    bridge({ get_dictation_status: { ...READY, model_verified: false, test_passed: false } });
    const onConfigureVoiceInput = vi.fn();
    render(composer({ onConfigureVoiceInput }));
    const mic = await screen.findByLabelText("Configure Voice Input in Settings");
    expect(mic.getAttribute("aria-disabled")).toBe("true");
    fireEvent.click(mic);
    await waitFor(() => expect(onConfigureVoiceInput).toHaveBeenCalled());
    expect(invoke).not.toHaveBeenCalledWith("start_dictation", undefined);
  });

  it("records with the wave drawn and Send held, and leaves the words as a draft to edit", async () => {
    render(composer());
    fireEvent.click(await screen.findByLabelText("Start dictation"));
    const stop = await screen.findByLabelText("Stop dictation");
    expect(document.querySelector(".voice-wave-bars")).toBeTruthy();
    expect(screen.getByLabelText("Send").hasAttribute("disabled")).toBe(true);

    fireEvent.click(stop);
    await screen.findByLabelText("Start dictation");
    expect((screen.getByPlaceholderText(/Ask anything/) as HTMLTextAreaElement).value).toBe("hello from the mic");
    expect(document.querySelector(".voice-wave-bars")).toBeNull();
  });

  it("says why recording could not start, and the microphone can be pressed again", async () => {
    bridge({
      get_dictation_status: READY,
      start_dictation: () => {
        throw new Error("No microphone is available.");
      },
    });
    render(composer());
    fireEvent.click(await screen.findByLabelText("Start dictation"));
    expect((await screen.findByRole("alert")).textContent).toContain("No microphone is available.");
    expect(screen.getByLabelText("Start dictation").hasAttribute("disabled")).toBe(false);
  });
});
