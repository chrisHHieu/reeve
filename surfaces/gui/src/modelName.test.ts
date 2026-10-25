import { describe, expect, it } from "vitest";
import { displayModel, displayModelFull, modelSwitchNoticeText } from "./modelName";

describe("displayModel", () => {
  it("prefers the curated label's name half and drops the provider suffix", () => {
    expect(displayModel("anthropic:claude-opus-4-8", { "anthropic:claude-opus-4-8": "Claude Opus 4.8 · Anthropic" })).toBe(
      "Claude Opus 4.8",
    );
  });

  it("cleans a provider-prefixed raw id: prefix, org, hyphens", () => {
    expect(displayModel("openrouter:google/gemini-3.7-flash")).toBe("Gemini 3.7 Flash");
    expect(displayModel("google/gemini-3.7-flash")).toBe("Gemini 3.7 Flash");
  });

  it("capitalizes known families the way they spell themselves", () => {
    expect(displayModel("openai:gpt-4o")).toBe("GPT 4o");
    expect(displayModel("deepseek/deepseek-chat")).toBe("DeepSeek Chat");
    expect(displayModel("zai-org/glm-5.3")).toBe("GLM 5.3");
  });

  it("leaves a bare human name alone", () => {
    expect(displayModel("Ollama")).toBe("Ollama");
  });
});

describe("displayModelFull", () => {
  it("a curated label passes through whole — its provider suffix is already right", () => {
    expect(displayModelFull("vertex:gemini-3.6-flash", { "vertex:gemini-3.6-flash": "Gemini 3.6 Flash · Vertex AI" })).toBe(
      "Gemini 3.6 Flash · Vertex AI",
    );
  });

  it("an uncatalogued id gets its serving provider so same-name models are tellable apart", () => {
    expect(displayModelFull("openrouter:google/gemini-3.7-flash")).toBe("Gemini 3.7 Flash · OpenRouter");
    expect(displayModelFull("vertex:gemini-3.6-flash")).toBe("Gemini 3.6 Flash · Vertex AI");
    expect(displayModelFull("google/gemini-3.7-flash")).toBe("Gemini 3.7 Flash · Google");
  });

  it("an id with no provider or org prefix has nothing to add", () => {
    expect(displayModelFull("Ollama")).toBe("Ollama");
  });
});

describe("modelSwitchNoticeText", () => {
  it("rewrites a raw id in the switch notice, provider included", () => {
    expect(modelSwitchNoticeText("Model switched to openrouter:google/gemini-3.7-flash")).toBe(
      "Model switched to Gemini 3.7 Flash · OpenRouter",
    );
  });

  it("keeps a notice that already reads as a friendly name with its provider", () => {
    expect(modelSwitchNoticeText("Model switched to Gemini 3.6 Flash · Vertex AI")).toBe(
      "Model switched to Gemini 3.6 Flash · Vertex AI",
    );
  });

  it("strips a provider prefix glued to a custom string's friendly name", () => {
    expect(modelSwitchNoticeText("Model switched to vertex:gemini/Gemini 3.7 Flash")).toBe(
      "Model switched to Gemini 3.7 Flash · Vertex AI",
    );
  });

  it("passes through notices without an id, and the empty fallback", () => {
    expect(modelSwitchNoticeText("Model switched")).toBe("Model switched");
    expect(modelSwitchNoticeText(undefined)).toBe("Model switched");
  });
});
