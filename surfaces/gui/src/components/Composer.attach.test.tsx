// @vitest-environment jsdom
// Drag a file out of the rail's tree and into the composer: the tree
// hands over a workspace PATH, the composer reads it through the artifact API and shows the
// same attachment chip an OS file drop would.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Composer } from "./Composer";
import { resetModelInputsCache } from "../modelInputs";

const readArtifact = vi.fn();
const getModelCatalog = vi.fn();
vi.mock("../api", async () => {
  const actual = await vi.importActual<Record<string, unknown>>("../api");
  return {
    ...actual,
    readArtifact: (...args: unknown[]) => readArtifact(...args),
    getModelCatalog: (...args: unknown[]) => getModelCatalog(...args),
  };
});

afterEach(() => {
  cleanup();
  readArtifact.mockReset();
  getModelCatalog.mockReset();
  resetModelInputsCache();
});

const props = (over: Record<string, unknown> = {}) => ({
  mode: "interactive",
  model: "gpt-5.6-sol",
  running: false,
  connected: true,
  sessionId: "s1",
  onSend: vi.fn(),
  onInterrupt: vi.fn(),
  onModeChange: vi.fn(),
  onModelChange: vi.fn(),
  ...over,
});

const cardNames = (root: Element, selector: string) =>
  Array.from(root.querySelectorAll(selector)).map((card) => card.querySelector(".att-card-name")?.textContent);
const refusedNames = (root: Element) => cardNames(root, '[data-testid="attachment-card"][data-state="refused"]');
const attachedNames = (root: Element) => cardNames(root, '[data-testid="attachment-card"]:not([data-state])');

describe("Composer / a file dragged in from the tree", () => {
  it("attaches a workspace path as its text", async () => {
    readArtifact.mockResolvedValue({ ok: true, path: "harness/agent.py", kind: "text", content: "print(1)\n" });
    render(<Composer {...props()} />);
    window.dispatchEvent(
      new CustomEvent("reeve-attach-file", { detail: { path: "harness/agent.py" } }),
    );
    await waitFor(() => expect(screen.getByText("agent.py")).toBeTruthy());
    expect(readArtifact).toHaveBeenCalledWith("s1", "harness/agent.py");
  });

  it("attaches an image as its data URL", async () => {
    readArtifact.mockResolvedValue({
      ok: true,
      path: "shot.png",
      kind: "image",
      data_url: "data:image/png;base64,AAA",
    });
    render(<Composer {...props()} />);
    window.dispatchEvent(new CustomEvent("reeve-attach-file", { detail: { path: "shot.png" } }));
    // An image attachment is its thumbnail, named by `alt` (the chip has no text line).
    await waitFor(() => expect(screen.getByAltText("shot.png")).toBeTruthy());
  });

  it("says why a file could not be attached instead of attaching it empty", async () => {
    readArtifact.mockResolvedValue({ ok: false, path: "big.bin", kind: "binary", error: "binary file" });
    const { container } = render(<Composer {...props()} />);
    window.dispatchEvent(new CustomEvent("reeve-attach-file", { detail: { path: "big.bin" } }));
    // The file is a card that says why it will not go, never an attachment.
    await waitFor(() => expect(screen.getByText(/Binary file/)).toBeTruthy());
    expect(refusedNames(container)).toEqual(["big.bin"]);
    expect(attachedNames(container)).toEqual([]);
  });

  it("takes the path out of a drop that carried no browser File", async () => {
    readArtifact.mockResolvedValue({ ok: true, path: "notes.md", kind: "text", content: "# hi\n" });
    const { container } = render(<Composer {...props()} />);
    const drop = container.querySelector(".composer")!;
    const transfer = {
      files: { length: 0 },
      getData: (type: string) => (type === "application/x-reeve-file" ? "notes.md" : ""),
    };
    fireEvent.drop(drop, { dataTransfer: transfer });
    await waitFor(() => expect(screen.getByText("notes.md")).toBeTruthy());
  });
});

// Every file that does not go in says so. The composer used to drop a 12 MB image, a file it
// could not read and the ninth attachment without a word (audit 2026-09-30).
describe("Composer / files that are not attached say why", () => {
  const drop = (container: HTMLElement, files: File[]) =>
    fireEvent.drop(container.querySelector(".composer")!, {
      dataTransfer: { files, getData: () => "" },
    });

  it("names a file over the size limit", async () => {
    const { container } = render(<Composer {...props()} />);
    const big = new File([new Uint8Array(11 * 1024 * 1024)], "huge.png", { type: "image/png" });
    drop(container, [big]);
    await waitFor(() => expect(screen.getByText(/11\.0 MB is over the 10 MB limit/)).toBeTruthy());
    expect(refusedNames(container)).toEqual(["huge.png"]);
    expect(screen.queryByAltText("huge.png")).toBeNull();
    expect(screen.getByTestId("composer-attach-refused").textContent).toBe("1 file will not be sent");
  });

  it("says a dropped folder is not a file", async () => {
    const { container } = render(<Composer {...props()} />);
    drop(container, [new File([], "my-folder", { type: "" })]);
    await waitFor(() => expect(screen.getByText(/It is empty, or a folder/)).toBeTruthy());
    expect(refusedNames(container)).toEqual(["my-folder"]);
  });

  it("says when more than eight files were added", async () => {
    const { container } = render(<Composer {...props()} />);
    const files = Array.from({ length: 10 }, (_, i) => new File([`note ${i}`], `n${i}.txt`, { type: "text/plain" }));
    drop(container, files);
    await waitFor(() => expect(screen.getByText(/2 files not added — a message carries at most 8 files/)).toBeTruthy());
    expect(screen.getByText("n7.txt")).toBeTruthy();
    expect(screen.queryByText("n8.txt")).toBeNull();
  });
});

// The picker's "Inputs" row is a promise; the composer checks the file against it when it is attached.
describe("Composer / a file the selected model cannot read", () => {
  it("is called out at attach time, from the same Inputs the picker shows", async () => {
    getModelCatalog.mockResolvedValue({
      models: [{ id: "gpt-5.6-sol", name: "GPT-5.6 Sol", provider: "openai", inputs: ["text", "image"] }],
      default: "",
    });
    const { container } = render(<Composer {...props()} />);
    const mp3 = new File(["ID3"], "call.mp3", { type: "audio/mpeg" });
    fireEvent.drop(container.querySelector(".composer")!, { dataTransfer: { files: [mp3], getData: () => "" } });
    await waitFor(() => expect(screen.getByText(/call\.mp3: GPT-5\.6 Sol doesn't take audio \(inputs: text, image\)/)).toBeTruthy());
    expect(screen.getByText("call.mp3")).toBeTruthy(); // still attached: the person decides
  });

  it("stays quiet when the model lists the input", async () => {
    getModelCatalog.mockResolvedValue({
      models: [{ id: "gpt-5.6-sol", name: "GPT-5.6 Sol", provider: "openai", inputs: ["text", "image", "audio"] }],
      default: "",
    });
    const { container } = render(<Composer {...props()} />);
    const mp3 = new File(["ID3"], "call.mp3", { type: "audio/mpeg" });
    fireEvent.drop(container.querySelector(".composer")!, { dataTransfer: { files: [mp3], getData: () => "" } });
    await waitFor(() => expect(screen.getByText("call.mp3")).toBeTruthy());
    expect(screen.queryByText(/doesn't take audio/)).toBeNull();
  });
});
