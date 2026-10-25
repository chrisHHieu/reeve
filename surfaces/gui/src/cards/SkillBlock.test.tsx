import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { SkillBlock } from "./SkillBlock";

afterEach(cleanup);

const CARD = {
  card: "skill" as const,
  name: "weekly-report",
  description: "Monday status report.",
  instructions: "# Steps\n\n1. Navigate.",
};

describe("SkillBlock", () => {
  it("renders the skill name, description and markdown body", () => {
    render(<SkillBlock card={CARD} />);
    expect(screen.getByText("weekly-report")).toBeDefined();
    expect(screen.getByText("Monday status report.")).toBeDefined();
    expect(screen.getByTestId("skill-instructions").textContent).toContain("Steps");
  });

  it("deep-links to Settings ▸ Skills", () => {
    const seen: unknown[] = [];
    const listener = (event: Event) => seen.push((event as CustomEvent).detail);
    window.addEventListener("reeve:open-settings", listener);
    render(<SkillBlock card={CARD} />);
    fireEvent.click(screen.getByText("Open in Settings ↗"));
    window.removeEventListener("reeve:open-settings", listener);
    expect(seen[0]).toEqual({ section: "skills" });
  });
});
