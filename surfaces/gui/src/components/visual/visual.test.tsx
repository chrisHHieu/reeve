import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { compile, evaluate } from "./formula";
import { parsePartialJson } from "./partialJson";
import { readBlocks } from "./visualModel";
import { VisualBlocks } from "./VisualBlocks";
import { SEND_PROMPT_EVENT, formMessage } from "./FormBlock";
import { axisFor, crossings, formatAxis, formatValue, localeOf } from "./format";

afterEach(cleanup);

describe("formula", () => {
  it("computes the grammar the harness checks, without eval", () => {
    expect(evaluate("2 + 3 * 4", {})).toBe(14);
    expect(evaluate("-2 ** 2", {})).toBe(-4);
    expect(evaluate("2 ^ 3 ^ 2", {})).toBe(512);
    expect(evaluate("min(a, 10) % 4", { a: 7 })).toBe(3);
    // 1.5 billion over 20 years at 9%: checked against the annuity formula computed separately.
    expect(evaluate("round(pmt(0.09/12, 240, 1500000000))", {})).toBe(13495889);
    expect(evaluate("x / 0", { x: 1 })).toBeNaN();
    expect(evaluate("missing + 1", {})).toBeNaN();
    expect(() => compile("a.b")).toThrow();
    expect(() => compile("alert(1)")).toThrow();
  });
});

describe("partial JSON", () => {
  it("reads a call's arguments up to their last complete member", () => {
    expect(parsePartialJson('{"blocks":[{"type":"chart","title":"Chi')).toEqual({ blocks: [{ type: "chart" }] });
    expect(parsePartialJson('{"blocks":[{"type":"metrics","items":[{"label":"A","value":12')).toEqual({
      blocks: [{ type: "metrics", items: [{ label: "A" }] }],
    });
    expect(parsePartialJson('{"blocks":[{"labels":["a","b"],"values":[1,2,3')).toEqual({
      blocks: [{ labels: ["a", "b"], values: [1, 2] }],
    });
    expect(parsePartialJson('{"blo')).toBeUndefined();
    expect(parsePartialJson('{"a":1}')).toEqual({ a: 1 });
  });
});

describe("axis", () => {
  it("lands on round steps and keeps zero", () => {
    expect(axisFor(3, 97)).toEqual({ min: 0, max: 100, ticks: [0, 20, 40, 60, 80, 100] });
    expect(axisFor(-30, 40).ticks).toContain(0);
  });
});

describe("numbers in the visual's own language", () => {
  it("writes a Vietnamese visual's numbers the Vietnamese way, whatever the machine's locale", () => {
    // The owner's report (2026-10-09): "13,495,889 đ", "10.5%" and "4B" beside prose that wrote "10,5%".
    const vi = localeOf([{ type: "what_if", title: "Gói A (9% cố định) và gói B" }]);
    expect(vi).toBe("vi-VN");
    expect(localeOf([{ type: "chart", title: "Spend by month" }])).toBeUndefined();
    expect(formatValue(13495889, { unit: "đ", locale: vi })).toBe("13.495.889 đ");
    expect(formatValue(10.5, { unit: "%", locale: vi })).toBe("10,5%");
    expect([4e9, 13.5e6, 250e3].map((v) => formatAxis(v, vi))).toEqual(["4 tỷ", "13,5 tr", "250k"]);
  });

  it("finds where two compared lines cross, not where they merely meet", () => {
    // Both start at zero (paid nothing yet): that is not a crossing. B is cheaper, then dearer.
    expect(crossings([0, 10, 20, 30], [0, 8, 22, 36], 4)).toEqual([1.5]);
    expect(crossings([0, 1, 2], [0, 2, 4], 3)).toEqual([]);
  });
});

describe("visual blocks", () => {
  it("draws a ranked bar list from the harness's series, and names the file it read", () => {
    const blocks = readBlocks({
      blocks: [
        {
          type: "chart",
          kind: "bar",
          title: "Theo danh mục",
          labels: ["Nhà ở", "Ăn uống"],
          series: [{ name: "amount", values: [6500000, 2210000] }],
          source: { file: "spend.csv", rows: 214 },
        },
      ],
    });
    render(<VisualBlocks blocks={blocks} />);
    expect(screen.getByText("Theo danh mục")).toBeTruthy();
    expect(screen.getByText(/spend\.csv · 214 rows/)).toBeTruthy();
    const bars = document.querySelectorAll(".vz-bar");
    expect(bars).toHaveLength(2);
    expect(bars[0].textContent).toContain("Nhà ở");
    expect(bars[0].textContent).toContain("75%");
  });

  it("holds a file-backed block's place until the harness has read it", () => {
    const blocks = readBlocks({ blocks: [{ type: "chart", title: "Q3", source: { file: "q3.csv", x: "cat" } }] });
    render(<VisualBlocks blocks={blocks} streaming />);
    expect(screen.getByText("Reading q3.csv…")).toBeTruthy();
  });

  it("recomputes a what-if in the GUI as the slider moves", async () => {
    const blocks = readBlocks({
      blocks: [
        {
          type: "what_if",
          inputs: [{ name: "n", label: "Số kỳ", min: 1, max: 10, step: 1, value: 2 }],
          values: [{ name: "twice", formula: "n * 2" }],
          outputs: [
            { label: "Gấp đôi", formula: "twice" },
            { label: "Bình phương", formula: "n ** 2" },
          ],
          highlight: "max",
        },
      ],
    });
    render(<VisualBlocks blocks={blocks} />);
    const values = () => [...document.querySelectorAll(".vz-output-value")].map((n) => n.textContent);
    expect(values()).toEqual(["4", "4"]);
    fireEvent.change(document.querySelector('input[type="range"]')!, { target: { value: "5" } });
    await waitFor(() => expect(values()).toEqual(["10", "25"]));
    expect(document.querySelectorAll(".vz-output")[1].getAttribute("data-best")).toBe("true");
  });

  it("sends a form's answers as the person's next message, once", () => {
    const sent: string[] = [];
    const listener = (event: Event) => sent.push((event as CustomEvent).detail.text);
    window.addEventListener(SEND_PROMPT_EVENT, listener);
    const blocks = readBlocks({
      blocks: [
        {
          type: "form",
          title: "Báo cáo chi tiêu",
          fields: [
            { name: "month", label: "Tháng", kind: "select", options: ["Tháng 9", "Tháng 8"] },
            { name: "parts", label: "Gồm", kind: "checkbox", options: ["Biểu đồ", "Bảng"], value: ["Biểu đồ"] },
          ],
          submit: "Tạo báo cáo",
        },
      ],
    });
    render(<VisualBlocks blocks={blocks} />);
    fireEvent.click(screen.getByLabelText("Bảng"));
    fireEvent.click(screen.getByTestId("visual-form-submit"));
    expect(sent).toEqual(["[Báo cáo chi tiêu]\nTháng: Tháng 9\nGồm: Biểu đồ, Bảng"]);
    expect(screen.getByTestId("visual-form-sent")).toBeTruthy();
    window.removeEventListener(SEND_PROMPT_EVENT, listener);
  });

  it("writes a form message the way a reader would", () => {
    const [form] = readBlocks({ blocks: [{ type: "form", fields: [{ name: "ok", label: "Gửi kèm", kind: "checkbox" }] }] });
    expect(formMessage(form as never, { ok: true })).toBe("[Form]\nGửi kèm: yes");
  });

  it("sorts a visual's table by a numeric column", () => {
    const blocks = readBlocks({
      blocks: [{ type: "table", columns: ["Mục", "Số tiền"], rows: [["A", "1.250.000"], ["B", "780.000"], ["C", "6.500.000"]] }],
    });
    render(<VisualBlocks blocks={blocks} />);
    fireEvent.click(screen.getByText("Số tiền"));
    const first = () => [...document.querySelectorAll(".vz-table tbody tr")].map((r) => r.firstElementChild!.textContent);
    expect(first()).toEqual(["B", "A", "C"]);
    expect(document.querySelector(".vz-table-status")!.textContent).toMatch(/Sum 8.530.000|Sum 8,530,000/);
  });

  it("skips what it cannot read rather than drawing it wrong", () => {
    expect(readBlocks({ blocks: [{ type: "gauge" }, { type: "chart" }, "x", { type: "metrics", items: [] }] })).toEqual([]);
  });

  it("shows a long table's first rows and opens the rest on request", () => {
    const rows = Array.from({ length: 24 }, (_, i) => [`Mục ${i + 1}`, String((i + 1) * 1000)]);
    render(<VisualBlocks blocks={readBlocks({ blocks: [{ type: "table", columns: ["Mục", "Số tiền"], rows }] })} />);
    expect(document.querySelectorAll(".vz-table tbody tr")).toHaveLength(8);
    fireEvent.click(screen.getByText("Show all 24 rows"));
    expect(document.querySelectorAll(".vz-table tbody tr")).toHaveLength(24);
  });
});
