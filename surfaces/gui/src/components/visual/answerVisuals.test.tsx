import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { answerParts, drawnInAnswer, placedIds, stripVisualMarkers, turnVisuals, type TurnVisual } from "./answerVisuals";
import { AnswerWithVisuals } from "./VisualPlacement";
import { ToolRow } from "../ToolRow";
import { rowSpec } from "../../cards/registry";
import type { ToolItem } from "../../cards/cardModels";

afterEach(cleanup);

const chart = { type: "chart", kind: "bar", labels: ["a", "b"], series: [{ name: "n", values: [1, 2] }] };
const visual = (id: string | undefined, callId: string): TurnVisual => ({ id, callId, blocks: turnVisuals([tool(callId, id)])[0].blocks });

function tool(callId: string, id?: string): ToolItem {
  return {
    kind: "tool",
    id: callId,
    callId,
    name: "show_visual",
    args: {},
    status: "ok",
    presentation: { summary: "Chart", card: { card: "visual", ...(id ? { id } : {}), blocks: [chart] } as never },
  } as ToolItem;
}

describe("a visual inside its answer", () => {
  it("stands where the answer placed it, once, and the placing line is never shown", () => {
    const text = "A rẻ hơn.\n\n:visual[v1]\n\n- Kéo thanh trượt.\n\n:visual[v1]\n";
    const parts = answerParts(text, [visual("v1", "c1")], true);
    expect(parts.map((p) => p.kind)).toEqual(["text", "visual", "text"]);
    expect(stripVisualMarkers(text)).not.toContain(":visual[");
    expect([...placedIds(text)]).toEqual(["v1"]);
  });

  it("goes after the opening paragraph when the answer did not place it", () => {
    const parts = answerParts("Nhà ở lớn nhất.\n\nChi tiết bên dưới.", [visual(undefined, "c1")], true);
    expect(parts.map((p) => (p.kind === "text" ? p.text.trim() : "visual"))).toEqual(["Nhà ở lớn nhất.", "visual", "Chi tiết bên dưới."]);
    // A narration line places nothing by default: only the closing answer takes the rest.
    expect(answerParts("Đang đọc file.", [visual(undefined, "c1")], false).map((p) => p.kind)).toEqual(["text"]);
  });

  it("draws the visual in the answer and leaves its step row a link to it", () => {
    render(<AnswerWithVisuals text={"A rẻ hơn.\n\n:visual[v1]\n\nXong."} visuals={[visual("v1", "c1")]} fallback />);
    expect(document.getElementById("visual-c1")?.querySelector(".vz-bar")).toBeTruthy();
    expect(screen.queryByText(":visual[v1]")).toBeNull();
    cleanup();
    render(<ToolRow spec={rowSpec(tool("c1", "v1"))} open={false} onToggle={() => {}} visualInAnswer="c1" />);
    expect(screen.getByTestId("visual-in-answer")).toBeTruthy();
    expect(document.querySelector(".vz")).toBeNull();
  });

  it("draws only what the answer placed, and a turn's drafts stay folded on their rows", () => {
    // The probe that showed five visuals in one turn, two of them titled "Test": the answer
    // placed one, and only that one is the answer's.
    const shown = [visual("v1", "c1"), visual("v2", "c2"), visual("v3", "c3")];
    expect(drawnInAnswer("B rẻ hơn.\n\n:visual[v3]\n\nXong.", shown)).toEqual(["c3"]);
    // None placed: the last one shown stands in for the answer's visual, the drafts do not.
    expect(drawnInAnswer("B rẻ hơn.\n\nXong.", shown)).toEqual(["c3"]);
    render(<ToolRow spec={rowSpec(tool("c1", "v1"))} open={false} onToggle={() => {}} visualFolded />);
    expect(document.querySelector(".vz")).toBeNull();
    fireEvent.click(screen.getByTestId("visual-folded"));
    expect(document.querySelector(".vz")).toBeTruthy();
  });
});
