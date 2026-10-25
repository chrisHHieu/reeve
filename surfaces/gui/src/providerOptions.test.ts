// The providers the model picker lists down its side (providerOptions.ts): titles come
// from the catalogue, with each provider's model count, and the id's own prefix stands in
// when the catalogue cannot be reached.

import { describe, expect, it } from "vitest";
import { providerKeyOf, providerOptions } from "./providerOptions";
import type { CatalogModel } from "./api";

const CATALOG: CatalogModel[] = [
  {
    id: "deepseek:deepseek-v4-flash",
    name: "DeepSeek V4 Flash",
    provider: "deepseek",
    provider_title: "DeepSeek",
  },
  {
    id: "deepseek:deepseek-v4",
    name: "DeepSeek V4",
    provider: "deepseek",
    provider_title: "DeepSeek",
  },
  { id: "gpt-5.6-sol", name: "GPT-5.6 Sol", provider: "openai", provider_title: "OpenAI" },
];

describe("providerKeyOf", () => {
  it("reads the provider prefix, and bare ids are OpenAI's", () => {
    expect(providerKeyOf("deepseek:deepseek-v4-flash")).toBe("deepseek");
    expect(providerKeyOf("gpt-5.6-sol")).toBe("openai");
  });
});

describe("providerOptions", () => {
  it("groups catalogue models by provider and keeps the first model as the pick", () => {
    const options = providerOptions(CATALOG, [], "gpt-5.6-sol");
    expect(options.map((o) => o.key)).toEqual(["deepseek", "openai"]);
    expect(options[0].title).toBe("DeepSeek");
    expect(options[0].modelId).toBe("deepseek:deepseek-v4-flash");
  });

  it("carries each provider's model count for the row's muted label", () => {
    const options = providerOptions(CATALOG, [], "gpt-5.6-sol");
    expect(options.find((o) => o.key === "deepseek")?.count).toBe(2);
    expect(options.find((o) => o.key === "openai")?.count).toBe(1);
  });

  it("falls back to the session's own ids when the catalogue is unreachable", () => {
    const options = providerOptions([], ["anthropic:claude-opus-4-8", "gpt-5.5"], "gpt-5.5");
    expect(options.map((o) => o.title)).toEqual(["Anthropic", "OpenAI"]);
  });

  it("always offers the active model's provider so the trigger can name it", () => {
    const options = providerOptions([], ["gpt-5.5"], "zai:glm-5.2");
    expect(options.map((o) => o.key)).toContain("zai");
  });
});
