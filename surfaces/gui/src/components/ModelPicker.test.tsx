// @vitest-environment jsdom
// The model picker (ModelPicker.tsx): providers down the left, their models on the right,
// the model in use and its thinking levels across the foot. The catalogue comes from
// /v1/models/catalog; the list degrades to the session's own `models` when the fetch fails,
// so switching models never depends on the network. Picking a model keeps the panel open,
// so the new model's thinking level is one more click (owner, 2026-10-13: it used to take
// opening the panel a second time). A provider only filters.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ModelPicker, rowProvider, rowsForProvider } from "./ModelPicker";
import type { CatalogModel } from "../api";
import { addModel, getModelCatalog } from "../api";

vi.mock("../api", () => ({
  getModelCatalog: vi.fn(),
  addModel: vi.fn(),
  MODELS_CHANGED: "reeve:models-changed",
}));

const CATALOG: CatalogModel[] = [
  {
    id: "deepseek:deepseek-v4-flash",
    name: "DeepSeek V4 Flash",
    provider: "deepseek",
    provider_title: "DeepSeek",
    context: 1_000_000,
    output: 384_000,
    vision: true,
    reasoning: true,
    reasoning_levels: ["low", "high"],
    inputs: ["text", "image"],
    free: true,
    cost: { input: 0.15, output: 0.6 },
  },
  {
    id: "gpt-5.6-sol",
    name: "GPT-5.6 Sol",
    provider: "openai",
    provider_title: "OpenAI",
    context: 400_000,
    reasoning: false,
    vision: true,
    inputs: ["text", "image", "pdf"],
    cost: { input: 1.25, output: 10 },
  },
  {
    id: "opencode-go:qwen3.7-max",
    name: "Qwen3.7 Max",
    provider: "opencode-go",
    provider_title: "OpenCode Go",
    context: 262_144,
  },
  {
    id: "opencode-go:longcat-2.0",
    name: "LongCat-2.0",
    provider: "opencode-go",
    provider_title: "OpenCode Go",
    context: 128_000,
    status: "deprecated",
  },
];

type PickerProps = {
  value: string;
  models: string[];
  modelLabels?: Record<string, string>;
  onChange: (id: string) => void;
  onModelsChanged?: () => void;
  thinking?: string;
  onThinkingChange?: (level: string) => void;
  disabled?: boolean;
  sessionKey?: string;
};

const props = (over: Partial<PickerProps> = {}): PickerProps => ({
  value: "gpt-5.6-sol",
  // Every catalogue model turned on: the list is what Settings turned on (owner, 2026-10-21).
  models: CATALOG.map((row) => row.id),
  modelLabels: {},
  onChange: vi.fn(),
  onModelsChanged: vi.fn(),
  ...over,
});

const open = (over: Partial<PickerProps> = {}) => {
  const p = props(over);
  render(<ModelPicker {...p} />);
  fireEvent.click(screen.getByTestId("model-picker-trigger"));
  return p;
};

// The menu opens narrowed to the loaded model's provider (the hydration fix); the grouped
// catalogue needs the user's own "All" pick on the left. A real click, not a test shim.
const openAll = (over: Partial<PickerProps> = {}) => {
  const p = open(over);
  fireEvent.click(screen.getByTestId("provider-row-all"));
  return p;
};

const groupNames = () => [...document.querySelectorAll(".mp-group")].map((g) => g.textContent);

beforeEach(() => {
  window.localStorage.clear();
  vi.mocked(getModelCatalog).mockResolvedValue({
    models: CATALOG,
    default: "gpt-5.6-sol",
  });
  vi.mocked(addModel).mockResolvedValue({
    ok: true,
    models: [],
    model: "",
  } as never);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("rowsForProvider", () => {
  it("returns every row without a filter and exactly one provider's with it", () => {
    expect(rowsForProvider(CATALOG, null)).toHaveLength(CATALOG.length);
    expect(rowsForProvider(CATALOG, "opencode-go").map((r) => r.id)).toEqual([
      "opencode-go:qwen3.7-max",
      "opencode-go:longcat-2.0",
    ]);
  });

  it("falls back to the id prefix when a row does not carry a provider", () => {
    expect(rowProvider({ id: "bare-model", name: "Bare", provider: "" })).toBe("openai");
  });
});

describe("ModelPicker", () => {
  it("is one control in the row: the providers and the thinking level are inside its panel", async () => {
    // Three pills in a row (provider | model | thinking) were cut to "Vertex AI …" and "H…"
    // in a rail-open column. Closed, there is one trigger; open, the other two are in it.
    render(<ModelPicker {...props({ value: "deepseek:deepseek-v4-flash", models: ["deepseek:deepseek-v4-flash"] })} />);
    expect(screen.queryByTestId("model-picker-providers")).toBeNull();
    expect(screen.queryByTestId("model-thinking")).toBeNull();
    expect(document.querySelectorAll("button")).toHaveLength(1);

    fireEvent.click(screen.getByTestId("model-picker-trigger"));
    const panel = screen.getByTestId("model-picker-panel");
    expect(panel.contains(screen.getByTestId("model-picker-providers"))).toBe(true);
    expect(panel.contains(await screen.findByTestId("model-thinking"))).toBe(true);
  });

  it("a model, then its thinking level, in one opening: the panel stays and its foot follows the pick", async () => {
    // The owner's report: pick a provider, pick a model, and the panel shut; the thinking row
    // had been the OLD model's, so the new one's level took opening the panel again.
    const onThinkingChange = vi.fn();
    const p = props({ onThinkingChange });
    const { rerender } = render(<ModelPicker {...p} />);
    fireEvent.click(screen.getByTestId("model-picker-trigger"));
    await screen.findByTestId("model-row-gpt-5.6-sol");
    expect(screen.queryByTestId("model-thinking")).toBeNull();

    fireEvent.click(screen.getByTestId("provider-row-deepseek"));
    fireEvent.click(await screen.findByTestId("model-row-deepseek:deepseek-v4-flash"));
    await waitFor(() => expect(p.onChange).toHaveBeenCalledWith("deepseek:deepseek-v4-flash"));
    // The composer hands the new model back as the value; the panel is still there.
    rerender(<ModelPicker {...p} value="deepseek:deepseek-v4-flash" />);
    expect(screen.getByTestId("model-picker-panel")).toBeTruthy();
    expect(screen.getByTestId("model-detail").textContent).toContain("DeepSeek V4 Flash");
    fireEvent.click(screen.getByTestId("thinking-high"));
    expect(onThinkingChange).toHaveBeenCalledWith("high");
    expect(screen.getByTestId("model-picker-panel")).toBeTruthy();
  });

  it("lists the catalogue grouped by provider and tags free models", async () => {
    openAll();
    expect(await screen.findByTestId("model-row-deepseek:deepseek-v4-flash")).toBeTruthy();
    expect(screen.getByTestId("model-row-gpt-5.6-sol")).toBeTruthy();
    expect(groupNames()).toEqual(expect.arrayContaining(["DeepSeek", "OpenAI"]));
    expect(screen.getByText("Free")).toBeTruthy();
  });

  it("says which models their provider marks deprecated, and only those", async () => {
    openAll();
    const tag = await screen.findByTestId("model-retiring-opencode-go:longcat-2.0");
    expect(tag.textContent).toBe("Retiring");
    expect(tag.getAttribute("title")).toMatch(/stop being served/);
    expect(screen.getByTestId("model-row-opencode-go:longcat-2.0").contains(tag)).toBe(true);
    expect(screen.getAllByText("Retiring")).toHaveLength(1);
  });

  it("puts context and price on each row, so models can be compared without pointing at each", async () => {
    openAll();
    const row = await screen.findByTestId("model-row-gpt-5.6-sol");
    expect(row.textContent).toContain("400k · $1.25 / $10");
    const free = screen.getByTestId("model-row-deepseek:deepseek-v4-flash");
    expect(free.textContent).toContain("1M");
    expect(free.textContent).not.toContain("$");
  });

  it("filters as the user types", async () => {
    open();
    // Wait for the CATALOGUE rows (not the offline fallback) before filtering.
    await screen.findByText("400k · $1.25 / $10");
    fireEvent.change(screen.getByTestId("model-picker-search"), {
      target: { value: "gpt" },
    });
    expect(screen.queryByTestId("model-row-deepseek:deepseek-v4-flash")).toBeNull();
    expect(screen.getByTestId("model-row-gpt-5.6-sol")).toBeTruthy();
  });

  it("the foot describes the model in use, never the row under the pointer", async () => {
    open({ value: "deepseek:deepseek-v4-flash" });
    await screen.findByTestId("model-thinking");
    const detail = screen.getByTestId("model-detail");
    expect(detail.textContent).toContain("DeepSeek V4 Flash");
    expect(detail.textContent).toContain("reads text, image");
    expect(detail.textContent).toContain("1M tokens");
    expect(detail.textContent).toContain("free");
    fireEvent.click(screen.getByTestId("provider-row-all"));
    fireEvent.mouseEnter(screen.getByTestId("model-row-gpt-5.6-sol"));
    expect(screen.getByTestId("model-detail").textContent).not.toContain("GPT-5.6 Sol");
  });

  it("lists the models turned on in Settings, counts them, and shows the rest when asked", async () => {
    // Settings turned on GPT-5.6 Sol and one of OpenCode Go's two: the picker says the same.
    open({ models: ["gpt-5.6-sol", "opencode-go:qwen3.7-max"] });
    fireEvent.click(screen.getByTestId("provider-row-all"));
    await screen.findByTestId("model-row-opencode-go:qwen3.7-max");
    expect(screen.queryByTestId("model-row-opencode-go:longcat-2.0")).toBeNull();
    expect(screen.queryByTestId("model-row-deepseek:deepseek-v4-flash")).toBeNull();
    expect(screen.queryByTestId("provider-row-deepseek")).toBeNull();
    expect(screen.getByTestId("provider-row-all").textContent).toContain("2");
    expect(screen.getByTestId("provider-row-opencode-go").textContent).toContain("1");
    // The rest of the catalogue is one click away, drawn as not turned on.
    fireEvent.click(screen.getByTestId("model-picker-more"));
    expect(screen.getByTestId("model-row-opencode-go:longcat-2.0").className).toContain(" off");
    expect(screen.getByTestId("provider-row-all").textContent).toContain("4");
    expect(screen.getByTestId("model-picker-more").textContent).toBe("Only the ones turned on");
  });

  it("keeps a check on the current model", async () => {
    open({ value: "deepseek:deepseek-v4-flash", models: ["deepseek:deepseek-v4-flash"] });
    const row = await screen.findByTestId("model-row-deepseek:deepseek-v4-flash");
    expect(row.querySelector(".mp-check")).toBeTruthy();
    expect(row.getAttribute("aria-selected")).toBe("true");
  });

  it("adds a not-yet-selected model and switches to it, and the panel stays", async () => {
    const p = openAll({ models: ["gpt-5.6-sol"] });
    fireEvent.click(await screen.findByTestId("model-picker-more"));
    fireEvent.click(await screen.findByTestId("model-row-deepseek:deepseek-v4-flash"));
    await waitFor(() => expect(addModel).toHaveBeenCalledWith("deepseek:deepseek-v4-flash"));
    expect(p.onModelsChanged).toHaveBeenCalled();
    await waitFor(() => expect(p.onChange).toHaveBeenCalledWith("deepseek:deepseek-v4-flash"));
    expect(screen.getByTestId("model-picker-panel")).toBeTruthy();
  });

  it("Enter on a row picks it and closes the panel", async () => {
    const p = openAll({ models: ["gpt-5.6-sol", "deepseek:deepseek-v4-flash"] });
    await screen.findByText("400k · $1.25 / $10");
    fireEvent.keyDown(screen.getByTestId("model-row-deepseek:deepseek-v4-flash"), { key: "Enter" });
    await waitFor(() => expect(p.onChange).toHaveBeenCalledWith("deepseek:deepseek-v4-flash"));
    await waitFor(() => expect(screen.queryByTestId("model-picker-panel")).toBeNull());
  });

  it("remembers the last models picked and lists them first under All", async () => {
    openAll({ models: ["gpt-5.6-sol", "deepseek:deepseek-v4-flash"] });
    await screen.findByText("400k · $1.25 / $10");
    fireEvent.click(screen.getByTestId("model-row-deepseek:deepseek-v4-flash"));
    expect(await screen.findByTestId("model-recent-deepseek:deepseek-v4-flash")).toBeTruthy();
    expect(groupNames()[0]).toBe("Recent");
  });

  it("does not add a model that is already in the selectable list", async () => {
    const p = openAll({ models: ["gpt-5.6-sol", "deepseek:deepseek-v4-flash"] });
    // The fallback list already carries this id; wait for the CATALOGUE render so the
    // click lands on a mounted row (the fetch replaces the fallback nodes).
    await screen.findByText("400k · $1.25 / $10");
    fireEvent.click(screen.getByTestId("model-row-deepseek:deepseek-v4-flash"));
    expect(addModel).not.toHaveBeenCalled();
    expect(p.onChange).toHaveBeenCalledWith("deepseek:deepseek-v4-flash");
  });

  it("falls back to the session's models when the catalogue is unreachable", async () => {
    vi.mocked(getModelCatalog).mockRejectedValue(new Error("offline"));
    openAll({ models: ["gpt-5.6-sol", "ollama:qwen3"] });
    expect(await screen.findByTestId("model-row-ollama:qwen3")).toBeTruthy();
    expect(screen.getByTestId("model-row-gpt-5.6-sol")).toBeTruthy();
    // The fallback says why: missing facts read as a bug otherwise.
    expect(screen.getByTestId("model-picker-note").textContent).toContain(
      "catalogue unavailable",
    );
  });

  it("offers an inline thinking control for a model whose catalogue row states levels", async () => {
    const onThinkingChange = vi.fn();
    open({ value: "deepseek:deepseek-v4-flash", models: ["deepseek:deepseek-v4-flash"], onThinkingChange });
    // One press on the level, at the foot of the panel; the panel stays open for it.
    fireEvent.click(await screen.findByTestId("thinking-high"));
    expect(onThinkingChange).toHaveBeenCalledWith("high");
    expect(screen.getByTestId("model-picker-panel")).toBeTruthy();
    // "Default" hands the choice back to the model: no level is sent.
    fireEvent.click(screen.getByTestId("thinking-default"));
    expect(onThinkingChange).toHaveBeenLastCalledWith("");
  });

  it("left and right move the thinking level when the caret is not in the search box", async () => {
    const onThinkingChange = vi.fn();
    open({ value: "deepseek:deepseek-v4-flash", models: ["deepseek:deepseek-v4-flash"], onThinkingChange, thinking: "low" });
    await screen.findByTestId("model-thinking");
    fireEvent.keyDown(screen.getByTestId("model-row-deepseek:deepseek-v4-flash"), { key: "ArrowRight" });
    expect(onThinkingChange).toHaveBeenLastCalledWith("high");
    onThinkingChange.mockClear();
    fireEvent.keyDown(screen.getByTestId("model-picker-search"), { key: "ArrowRight" });
    expect(onThinkingChange).not.toHaveBeenCalled();
  });

  it("shows the current level on the trigger, beside the model's name", async () => {
    render(<ModelPicker {...props({ value: "deepseek:deepseek-v4-flash", models: ["deepseek:deepseek-v4-flash"], thinking: "high" })} />);
    const level = await screen.findByTestId("model-picker-level");
    expect(level.textContent).toBe("· High");
    expect(screen.getByTestId("model-picker-trigger").contains(level)).toBe(true);
    // …and marks it in the panel.
    fireEvent.click(screen.getByTestId("model-picker-trigger"));
    expect(screen.getByTestId("thinking-high").getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByTestId("thinking-default").getAttribute("aria-pressed")).toBe("false");
  });

  it("a level the model does not offer shows as its default, not the old model's level", async () => {
    // Owner-hit 2026-10-24: "medium" picked on a three-level model stayed on the trigger after a
    // switch to one offering low and high, with no button in the panel for it.
    render(<ModelPicker {...props({ value: "deepseek:deepseek-v4-flash", models: ["deepseek:deepseek-v4-flash"], thinking: "medium" })} />);
    expect((await screen.findByTestId("model-picker-level")).textContent).toBe("· Default");
    fireEvent.click(screen.getByTestId("model-picker-trigger"));
    expect(screen.getByTestId("thinking-default").getAttribute("aria-pressed")).toBe("true");
  });

  it("shows no thinking level when the model states none, and says so in the panel", async () => {
    open();
    // The row stays and says so, instead of vanishing and moving everything above it.
    expect((await screen.findByTestId("model-thinking-none")).textContent).toContain("no thinking levels");
    expect(screen.queryByTestId("model-thinking")).toBeNull();
    expect(screen.queryByTestId("model-picker-level")).toBeNull();
  });

  it("retries the catalogue on open after a mount failure", async () => {
    vi.mocked(getModelCatalog).mockRejectedValueOnce(new Error("old backend"));
    const p = props();
    render(<ModelPicker {...p} />);
    fireEvent.click(screen.getByTestId("model-picker-trigger"));
    // The mount attempt failed; opening retried and the catalogue arrived.
    expect(await screen.findByText("400k · $1.25 / $10")).toBeTruthy();
    expect(getModelCatalog).toHaveBeenCalledTimes(2);
  });

  it("a provider only filters: the model in use does not change", async () => {
    const p = open();
    fireEvent.click(await screen.findByTestId("provider-row-opencode-go"));
    expect(p.onChange).not.toHaveBeenCalled();
    expect(addModel).not.toHaveBeenCalled();
    expect(screen.getByTestId("model-row-opencode-go:qwen3.7-max")).toBeTruthy();
    expect(screen.getByTestId("model-row-opencode-go:longcat-2.0")).toBeTruthy();
    expect(screen.queryByTestId("model-row-gpt-5.6-sol")).toBeNull();
    // One provider's list has nothing to group.
    expect(document.querySelector(".mp-group")).toBeNull();
    expect(screen.getByTestId("provider-row-opencode-go").getAttribute("aria-checked")).toBe("true");
  });

  it("All restores the grouped catalogue after a provider filter", async () => {
    open();
    fireEvent.click(await screen.findByTestId("provider-row-opencode-go"));
    fireEvent.click(screen.getByTestId("provider-row-all"));
    expect(await screen.findByTestId("model-row-deepseek:deepseek-v4-flash")).toBeTruthy();
    expect(screen.getByTestId("model-row-gpt-5.6-sol")).toBeTruthy();
    expect(groupNames()).toEqual(expect.arrayContaining(["DeepSeek", "OpenCode Go"]));
  });

  it("lists each provider with its model count, and the current one is picked", async () => {
    open({ value: "opencode-go:qwen3.7-max" });
    await screen.findByTestId("model-row-opencode-go:longcat-2.0");
    const row = screen.getByTestId("provider-row-opencode-go");
    expect(row.textContent).toContain("OpenCode Go");
    expect(row.textContent).toContain("2");
    expect(row.getAttribute("aria-checked")).toBe("true");
    expect(screen.getByTestId("provider-row-deepseek").getAttribute("aria-checked")).toBe("false");
  });

  it("searches every provider and every model, whatever is picked on the left", async () => {
    // DeepSeek is not turned on: a search still finds it.
    open({ models: ["gpt-5.6-sol", "opencode-go:qwen3.7-max", "opencode-go:longcat-2.0"] });
    fireEvent.click(await screen.findByTestId("provider-row-opencode-go"));
    await screen.findByTestId("model-row-opencode-go:longcat-2.0");
    fireEvent.change(screen.getByTestId("model-picker-search"), {
      target: { value: "deepseek" },
    });
    expect(screen.getByTestId("model-row-deepseek:deepseek-v4-flash")).toBeTruthy();
    expect(screen.queryByTestId("model-row-opencode-go:qwen3.7-max")).toBeNull();
    fireEvent.change(screen.getByTestId("model-picker-search"), {
      target: { value: "nothing-like-this" },
    });
    expect(screen.getByText("No model matches “nothing-like-this”")).toBeTruthy();
  });

  it("roves the visible rows with arrow keys and closes on Escape", async () => {
    openAll();
    await screen.findByTestId("model-row-deepseek:deepseek-v4-flash");
    fireEvent.keyDown(screen.getByTestId("model-picker-search"), { key: "ArrowDown" });
    expect(document.activeElement).toBe(
      screen.getByTestId("model-row-deepseek:deepseek-v4-flash"),
    );
    fireEvent.keyDown(screen.getByTestId("model-picker-panel"), { key: "Escape" });
    expect(screen.queryByTestId("model-picker-panel")).toBeNull();
  });

  it("mounts narrowed to the hydrated session's provider — the picked row and the model list", async () => {
    open({ value: "opencode-go:qwen3.7-max" });
    expect(screen.getByTestId("provider-row-opencode-go").getAttribute("aria-checked")).toBe("true");
    expect(await screen.findByTestId("model-row-opencode-go:qwen3.7-max")).toBeTruthy();
    expect(screen.getByTestId("model-row-opencode-go:longcat-2.0")).toBeTruthy();
    expect(screen.queryByTestId("model-row-gpt-5.6-sol")).toBeNull();
    expect(screen.queryByTestId("model-row-deepseek:deepseek-v4-flash")).toBeNull();
    expect(document.querySelector(".mp-group")).toBeNull();
  });

  it("keeps an explicit All choice through a later model hydration", async () => {
    const p = props({ sessionKey: "s1" });
    const { rerender } = render(<ModelPicker {...p} />);
    fireEvent.click(screen.getByTestId("model-picker-trigger"));
    fireEvent.click(screen.getByTestId("provider-row-all"));
    expect(await screen.findByTestId("model-row-deepseek:deepseek-v4-flash")).toBeTruthy();
    // The session's model detail lands after the pick — All is a choice, not staleness.
    rerender(<ModelPicker {...p} value="opencode-go:qwen3.7-max" />);
    expect(screen.getByTestId("model-row-deepseek:deepseek-v4-flash")).toBeTruthy();
    expect(screen.getByTestId("model-row-gpt-5.6-sol")).toBeTruthy();
    expect(screen.getByTestId("model-row-opencode-go:qwen3.7-max")).toBeTruthy();
  });

  it("a model change from elsewhere moves the derived provider filter", async () => {
    const p = props();
    const { rerender } = render(<ModelPicker {...p} />);
    fireEvent.click(screen.getByTestId("model-picker-trigger"));
    await screen.findByTestId("model-row-gpt-5.6-sol");
    expect(screen.queryByTestId("model-row-opencode-go:qwen3.7-max")).toBeNull();
    rerender(<ModelPicker {...p} value="opencode-go:qwen3.7-max" />);
    expect(await screen.findByTestId("model-row-opencode-go:qwen3.7-max")).toBeTruthy();
    expect(screen.queryByTestId("model-row-gpt-5.6-sol")).toBeNull();
  });

  it("retires the previous session's explicit filter on a session change", async () => {
    const p = props({ sessionKey: "s1" });
    const { rerender } = render(<ModelPicker {...p} />);
    fireEvent.click(screen.getByTestId("model-picker-trigger"));
    fireEvent.click(screen.getByTestId("provider-row-all"));
    expect(await screen.findByTestId("model-row-deepseek:deepseek-v4-flash")).toBeTruthy();
    rerender(<ModelPicker {...p} value="opencode-go:qwen3.7-max" sessionKey="s2" />);
    expect(await screen.findByTestId("model-row-opencode-go:qwen3.7-max")).toBeTruthy();
    expect(screen.queryByTestId("model-row-gpt-5.6-sol")).toBeNull();
  });
});
