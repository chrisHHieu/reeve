// For the cloud-account providers the add row asks for the family, which goes into the model id
// (`bedrock:claude/…`, `vertex:openweight/…`); other providers keep the plain add row.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ModelChecklist } from "./ModelChecklist";

vi.mock("../api", () => ({
  addModel: vi.fn(async (id: string) => ({ ok: true, models: [id], model: id })),
  removeModel: vi.fn(async () => ({ ok: true, models: [], model: "" })),
  setDefaultModel: vi.fn(async () => ({ ok: true })),
  getSettings: vi.fn(async () => ({ models: [], model: "" })),
  getModelCatalog: vi.fn(async () => ({
    models: [{ id: "openrouter:z-ai/glm-5.2", name: "GLM-5.2", provider: "openrouter", context: 1_048_576, vision: false, reasoning: true, free: false }],
    default: "",
  })),
  MODELS_CHANGED: "reeve:models-changed",
}));

import { addModel } from "../api";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const KNOWN = ["openai", "anthropic", "bedrock", "vertex", "openrouter"];

function list(provider: string, curated: string[] = []) {
  return render(<ModelChecklist provider={provider} knownProviders={KNOWN} suggested={[]} curated={curated} defaultModel="" onChanged={() => {}} />);
}

function typeAndAdd(id: string) {
  fireEvent.change(screen.getByPlaceholderText("Add another model…"), { target: { value: id } });
  fireEvent.click(screen.getByText("Add"));
}

describe("ModelChecklist's add row", () => {
  it("puts the Vertex family chosen into the id", () => {
    list("vertex");
    fireEvent.change(screen.getByTestId("mlist-family"), { target: { value: "openweight" } });
    typeAndAdd("meta/llama-4-maverick-17b-128e-instruct-maas");
    expect(addModel).toHaveBeenCalledWith("vertex:openweight/meta/llama-4-maverick-17b-128e-instruct-maas");
  });

  it("starts Bedrock on the Claude family, and leaves a family typed by hand as it is", () => {
    list("bedrock");
    typeAndAdd("anthropic.claude-sonnet-4-6-v1:0");
    expect(addModel).toHaveBeenCalledWith("bedrock:claude/anthropic.claude-sonnet-4-6-v1:0");
    typeAndAdd("other/amazon.nova-2-pro-v1:0");
    expect(addModel).toHaveBeenLastCalledWith("bedrock:other/amazon.nova-2-pro-v1:0");
  });

  it("asks no family of a plain provider", () => {
    list("openrouter");
    expect(screen.queryByTestId("mlist-family")).toBeNull();
    typeAndAdd("z-ai/glm-5.2");
    expect(addModel).toHaveBeenCalledWith("openrouter:z-ai/glm-5.2");
  });

  it("a row has the catalogue's name and the facts the picker gives", async () => {
    list("openrouter", ["openrouter:z-ai/glm-5.2"]);
    // What the composer's picker shows for the same model (owner, 2026-10-21).
    expect(await screen.findByText("GLM-5.2")).toBeTruthy();
    const facts = screen.getByTestId("model-facts-openrouter:z-ai/glm-5.2").textContent;
    expect(facts).toContain("z-ai/glm-5.2");
    expect(facts).toContain("1.05M");
    expect(screen.getByTestId("model-switch-openrouter:z-ai/glm-5.2").getAttribute("aria-checked")).toBe("true");
  });
});
