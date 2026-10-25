import { act, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { OPEN_ARTIFACT_EVENT } from "./Markdown";
import { RightRail } from "./RightRail";

vi.mock("../api", async () => ({
  ...(await vi.importActual<object>("../api")),
  getArtifacts: vi.fn().mockResolvedValue([]),
  getRoots: vi.fn().mockResolvedValue([]),
  getJournalCases: vi.fn().mockResolvedValue([]),
  readArtifact: vi.fn().mockResolvedValue({ ok: true, path: "r.md", kind: "markdown", content: "x" }),
  revealArtifact: vi.fn().mockResolvedValue({ ok: true }),
}));

const railWith = (onPreviewChange: (open: boolean) => void) => <RightRail active sessionId="s1" refreshKey={0} onPreviewChange={onPreviewChange} />;
const settle = () => act(async () => {});

// The rail tells the app when its file viewer opens or closes, and only then. App draws a new
// callback whenever the navigation toggles; telling that callback "open" again folded a sidebar the
// person had just opened (owner-hit 2026-08-21).
describe("RightRail's preview notice", () => {
  it("is sent on a change of state, never for a new callback", async () => {
    const first = vi.fn();
    const { rerender } = render(railWith(first));
    await settle();
    expect(first).not.toHaveBeenCalled();

    await act(async () => {
      window.dispatchEvent(new CustomEvent(OPEN_ARTIFACT_EVENT, { detail: { path: "r.md" } }));
    });
    expect(first.mock.calls).toEqual([[true]]);

    const second = vi.fn();
    rerender(railWith(second));
    await settle();
    expect(second).not.toHaveBeenCalled();
  });
});
