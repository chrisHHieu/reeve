import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { VisualBlocks } from "./VisualBlocks";
import { readBlocks } from "./visualModel";
import { spanLength, timeTicks } from "./TimelineBlock";
import { countryBox, loadCountries, openingOf, shareOf } from "./mapData";
import type { MapSpec } from "./visualModel";
import { blockTable, tableHtml, tableText } from "./exportVisual";
import { resetVisualStates, splitVisualStates, takeVisualStates, VisualIdentity, withVisualStates } from "./visualState";

afterEach(cleanup);
beforeEach(resetVisualStates);

describe("a timeline", () => {
  const blocks = readBlocks({
    blocks: [
      {
        type: "timeline",
        title: "Kế hoạch",
        items: [
          { label: "Khảo sát", start: "2026-09-01", end: "2026-09-19", group: "Chuẩn bị", done: true },
          { label: "Phát triển", start: "2026-10-06", end: "2026-11-21", group: "Làm" },
          { label: "Ra mắt", start: "2026-12-08", group: "Làm" },
        ],
      },
    ],
  });

  it("draws a span as a bar, a date as a mark, gathered under its group", () => {
    render(<VisualBlocks blocks={blocks} />);
    expect([...document.querySelectorAll(".vz-tl-group-name")].map((n) => n.textContent)).toEqual(["Chuẩn bị", "Làm"]);
    expect(document.querySelectorAll(".vz-tl-bar")).toHaveLength(2);
    expect(document.querySelectorAll(".vz-tl-mark")).toHaveLength(1);
    expect(document.querySelector(".vz-tl-row[data-done]")?.textContent).toContain("Khảo sát");
  });

  it("ticks by month in a Vietnamese visual's own words", () => {
    const ticks = timeTicks(Date.UTC(2026, 7, 25), Date.UTC(2026, 11, 20), "vi-VN").map((t) => t.label);
    expect(ticks).toEqual(["T9/2026", "T10", "T11", "T12"]);
  });

  it("writes a date beside a single-date mark, and says how long a span runs", () => {
    render(<VisualBlocks blocks={blocks} />);
    expect(document.querySelector(".vz-tl-mark-date")?.textContent).toBe("8/12");
    expect(spanLength(47, "vi-VN")).toBe("7 tuần");
    expect(spanLength(400, "en-US")).toBe("13 months");
  });

  it("zooms in from its buttons and goes back to all of it", async () => {
    render(<VisualBlocks blocks={blocks} />);
    const before = [...document.querySelectorAll(".vz-tl-tick")].map((n) => n.textContent);
    fireEvent.click(screen.getByRole("button", { name: "Phóng to" }));
    // Weeks once about two months are in view, where all of it ticked by month.
    await waitFor(() => expect([...document.querySelectorAll(".vz-tl-tick")].map((n) => n.textContent)).not.toEqual(before));
    expect(screen.getByRole("button", { name: "Xem hết" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Xem hết" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Xem hết" })).toBeNull());
  });
});

describe("a chart a reader can work", () => {
  const months = ["2026-07", "2026-08", "2026-09", "2026-10"];
  const columns = readBlocks({
    blocks: [
      {
        type: "chart",
        kind: "bar",
        title: "Chi tiêu theo hạng mục",
        labels: months,
        series: ["Ăn uống", "Gia đình", "Công việc", "Nhà ở"].map((name, k) => ({ name, values: months.map((_, i) => (k + 1) * 1_000_000 + i) })),
      },
    ],
  });

  it("stacks four series or more, and groups them on request", () => {
    render(<VisualBlocks blocks={columns} />);
    expect(screen.getByRole("radio", { name: "Stacked" }).getAttribute("aria-checked")).toBe("true");
    const stackedWidth = document.querySelector(".vz-col")!.getAttribute("width");
    fireEvent.click(screen.getByRole("radio", { name: "Grouped" }));
    expect(document.querySelector(".vz-col")!.getAttribute("width")).not.toBe(stackedWidth);
  });

  it("hides a series named in the legend and never the last one", () => {
    render(<VisualBlocks blocks={columns} />);
    expect(document.querySelectorAll(".vz-col")).toHaveLength(16);
    fireEvent.click(screen.getByRole("button", { name: "Ăn uống" }));
    expect(document.querySelectorAll(".vz-col")).toHaveLength(12);
    for (const name of ["Gia đình", "Công việc", "Nhà ở"]) fireEvent.click(screen.getByRole("button", { name }));
    expect(document.querySelectorAll(".vz-col")).toHaveLength(4); // the last one stays
  });

  it("reads as a table of its numbers, and opens larger", () => {
    render(<VisualBlocks blocks={columns} />);
    fireEvent.click(screen.getByTestId("visual-as-table"));
    expect(document.querySelectorAll(".vz-table-view tbody tr")).toHaveLength(4);
    fireEvent.click(screen.getByTestId("visual-expand"));
    expect(screen.getByTestId("visual-expanded")).toBeTruthy();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByTestId("visual-expanded")).toBeNull();
  });
});

describe("a map", () => {
  const spec = (regions: { id: string; name: string; value: number }[], points: { lat: number; lon: number; label: string }[] = []) =>
    readBlocks({ blocks: [{ type: "map", unit: "tr USD", regions, points }] })[0] as MapSpec;

  it("lists a country the outlines have no shape for, with its value", async () => {
    const blocks = [spec([{ id: "VNM", name: "Việt Nam", value: 120 }, { id: "SGP", name: "Singapore", value: 45 }, { id: "GIB", name: "Gibraltar", value: 2 }])];
    render(<VisualBlocks blocks={blocks} />);
    // The outlines are 0.7 MB to decode: under a full parallel run that takes more than the default second.
    await waitFor(() => expect(screen.getByTestId("map-small").textContent).toBe("Gibraltar 2 tr USD"), { timeout: 8000 });
  });

  it("opens on its countries, on its one place, or across the antimeridian", async () => {
    const countries = await loadCountries();
    const asia = openingOf(spec([{ id: "VNM", name: "Việt Nam", value: 1 }, { id: "JPN", name: "Nhật Bản", value: 2 }]), countries);
    expect(asia && "box" in asia && asia.box[0]).toBeGreaterThan(100);
    expect(asia && "box" in asia && asia.box[2]).toBeLessThan(150);
    expect(openingOf(spec([], [{ lat: 10.773, lon: 106.7, label: "Saigon Centre" }]), countries)).toEqual({ center: [106.7, 10.773] });
    // Russia measured eastward past 180°, not as the whole world's width.
    const russia = countryBox(countries, "RUS")!;
    expect(russia[2] - russia[0]).toBeLessThan(180);
  });

  it("shades from a fifth of the accent for the least to all of it for the most", () => {
    expect(shareOf(45, { min: 45, max: 210 })).toBeCloseTo(0.18);
    expect(shareOf(210, { min: 45, max: 210 })).toBeCloseTo(1);
  });
});

describe("taking a visual out", () => {
  it("copies a chart as a table a sheet pastes into cells, numbers the visual's way", () => {
    const [chart] = readBlocks({
      blocks: [{ type: "chart", kind: "bar", title: "Chi tiêu", unit: "đ", labels: ["Nhà ở", "Ăn uống"], series: [{ name: "Số tiền", values: [6500000, 2210000] }] }],
    });
    const table = blockTable(chart, "vi-VN")!;
    expect(tableText(table, "Chi tiêu")).toBe("Chi tiêu\n\tSố tiền\nNhà ở\t6.500.000 đ\nĂn uống\t2.210.000 đ");
    expect(tableHtml(table)).toContain("<td>Nhà ở</td><td>6.500.000 đ</td>");
  });

  it("offers copy, image and PNG on each block, and no data copy on a form", () => {
    render(<VisualBlocks blocks={readBlocks({ blocks: [{ type: "metrics", items: [{ label: "Tổng", value: 3 }] }, { type: "form", fields: [{ name: "a", label: "A" }] }] })} />);
    expect(screen.getAllByTestId("visual-png")).toHaveLength(2);
    expect(screen.getAllByTestId("visual-copy")).toHaveLength(1);
  });
});

describe("where the person left a visual", () => {
  const whatIf = readBlocks({
    blocks: [
      {
        type: "what_if",
        title: "Khoản vay",
        inputs: [{ name: "rate", label: "Lãi B", min: 7, max: 14, step: 0.5, value: 10.5, unit: "%" }],
        outputs: [{ label: "Gấp đôi", formula: "rate * 2" }],
      },
    ],
  });

  it("is said with the next message once moved, and only once", async () => {
    render(
      <VisualIdentity.Provider value={{ id: "v1", callId: "c1" }}>
        <VisualBlocks blocks={whatIf} />
      </VisualIdentity.Provider>,
    );
    expect(takeVisualStates()).toEqual([]); // not moved: nothing to say
    fireEvent.change(document.querySelector('input[type="range"]')!, { target: { value: "9" } });
    await waitFor(() => expect(document.querySelector("output")?.textContent).toBe("9,0%".replace(",0", "")));
    const lines = takeVisualStates();
    expect(lines).toEqual(["[v1 as the user left it: Lãi B = 9%. It shows Gấp đôi = 18]"]);
    expect(takeVisualStates()).toEqual([]); // said already
    const sent = withVisualStates("ở mức này thì sao?", lines);
    expect(splitVisualStates(sent)).toEqual({ text: "ở mức này thì sao?", states: [{ id: "v1", state: "Lãi B = 9%. It shows Gấp đôi = 18" }] });
  });

  it("comes back where it was after a reload, and is not said again", async () => {
    const draw = () =>
      render(
        <VisualIdentity.Provider value={{ id: "v1", callId: "c1" }}>
          <VisualBlocks blocks={whatIf} />
        </VisualIdentity.Provider>,
      );
    const first = draw();
    fireEvent.change(document.querySelector('input[type="range"]')!, { target: { value: "9" } });
    expect(takeVisualStates()).toHaveLength(1);
    await new Promise((resolve) => setTimeout(resolve, 350)); // kept once the slider rests
    first.unmount();
    // A reload: the module's memory is gone, the browser's store is not.
    const store = localStorage.getItem("reeve.visualState.v1");
    resetVisualStates();
    localStorage.setItem("reeve.visualState.v1", store!);
    draw();
    expect((document.querySelector('input[type="range"]') as HTMLInputElement).value).toBe("9");
    expect(takeVisualStates()).toEqual([]);
  });

  it("is not said for a visual drawn outside an answer", () => {
    render(<VisualBlocks blocks={whatIf} />);
    fireEvent.change(document.querySelector('input[type="range"]')!, { target: { value: "9" } });
    expect(takeVisualStates()).toEqual([]);
  });
});
