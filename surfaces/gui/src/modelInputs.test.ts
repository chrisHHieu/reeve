import { beforeEach, describe, expect, it, vi } from "vitest";

const getModelCatalog = vi.fn();
vi.mock("./api", () => ({ getModelCatalog: (...a: unknown[]) => getModelCatalog(...a) }));

import { inputNeeded, resetModelInputsCache, rowFor, unreadableNotices } from "./modelInputs";

const rows = [
  { id: "opencode-go:mimo-v2.6-pro", name: "MiMo-V2.6-Pro", provider: "opencode-go", inputs: ["text", "image", "audio", "video"] },
  { id: "opencode-go:deepseek-v4.1-flash", name: "DeepSeek V4.1 Flash", provider: "opencode-go", inputs: ["text", "image"] },
  { id: "gpt-5.5", name: "GPT-5.5", provider: "openai", inputs: null },
];
const audio = { kind: "file" as const, name: "call.mp3", mime: "audio/mpeg", data_url: "data:audio/mpeg;base64,AA==" };
const video = { kind: "file" as const, name: "clip.mp4", mime: "video/mp4", data_url: "data:video/mp4;base64,AA==" };

beforeEach(() => {
  resetModelInputsCache();
  getModelCatalog.mockResolvedValue({ models: rows, default: "" });
});

describe("which input a file needs", () => {
  it("needs the model to read images, audio and video; text and PDFs have local fallbacks", () => {
    expect(inputNeeded({ kind: "image", name: "a.png" })).toBe("image");
    expect(inputNeeded(audio)).toBe("audio");
    expect(inputNeeded(video)).toBe("video");
    expect(inputNeeded({ kind: "pdf", name: "a.pdf" })).toBeNull();
    expect(inputNeeded({ kind: "text", name: "a.txt", text: "x" })).toBeNull();
    expect(inputNeeded({ kind: "file", name: "a.docx", mime: "application/zip" })).toBeNull();
  });
});

describe("rowFor", () => {
  it("finds the row by id, or by its bare name when that is unambiguous", () => {
    expect(rowFor(rows as never, "gpt-5.5")?.name).toBe("GPT-5.5");
    expect(rowFor(rows as never, "mimo-v2.6-pro")?.name).toBe("MiMo-V2.6-Pro");
    expect(rowFor(rows as never, "no-such-model")).toBeNull();
  });
});

describe("unreadableNotices", () => {
  it("says so when the selected model's Inputs do not list what was attached", async () => {
    const notes = await unreadableNotices("opencode-go:deepseek-v4.1-flash", [audio, video]);
    expect(notes).toHaveLength(2);
    expect(notes[0]).toContain("call.mp3: DeepSeek V4.1 Flash doesn't take audio (inputs: text, image)");
    expect(notes[1]).toContain("doesn't take video");
  });

  it("stays quiet for a model that lists it", async () => {
    expect(await unreadableNotices("opencode-go:mimo-v2.6-pro", [audio, video])).toEqual([]);
  });

  it("never reports an unknown as a no", async () => {
    expect(await unreadableNotices("gpt-5.5", [audio])).toEqual([]); // the catalogue states no inputs
    expect(await unreadableNotices("unlisted-model", [audio])).toEqual([]);
  });

  it("does not ask the catalogue when nothing attached needs it", async () => {
    getModelCatalog.mockClear();
    expect(await unreadableNotices("opencode-go:deepseek-v4.1-flash", [{ kind: "text", name: "a.txt", text: "x" }])).toEqual([]);
    expect(getModelCatalog).not.toHaveBeenCalled();
  });
});
