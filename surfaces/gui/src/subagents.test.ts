import { describe, expect, it } from "vitest";
import { agentColor, buildAgentColors } from "./subagents";

// An agent's declared color wins; otherwise the name hashes
// into a stable palette. Stability is the point — the same agent must wear the same tint
// in every session and across reloads.
describe("agent color", () => {
  it("prefers the declared color (hex or theme literal) over the palette", () => {
    expect(agentColor("explore", "#ff00ff")).toBe("#ff00ff");
    expect(agentColor("explore", "var(--accent)")).toBe("var(--accent)");
    expect(agentColor("explore", "   ")).not.toBe("   ");
  });

  it("hashes the name deterministically when no color was declared", () => {
    const first = agentColor("explore");
    expect(agentColor("explore")).toBe(first);
    expect(agentColor("Explore")).toBe(first); // case-insensitive, like the reference design's `tone`
    expect(first).toMatch(/^#[0-9a-f]{6}$/);
  });

  it("indexes declarations by lowercased name and skips the rest", () => {
    const index = buildAgentColors([
      { name: "Explore", color: "#123456" },
      { name: "general" },
    ]);
    expect(index).toEqual({ explore: "#123456" });
    expect(agentColor("explore", index["explore"])).toBe("#123456");
  });
});
