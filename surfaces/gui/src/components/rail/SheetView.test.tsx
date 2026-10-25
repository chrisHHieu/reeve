// A spreadsheet drawn as its sheets. The workbook is a real one, written part by part
// (sheet/book.fixture.ts) and read by the real reader on this thread (the test DOM has no
// worker); what is pinned is what a person sees and can do. Sizes on screen were looked at in
// the dev app: the test DOM lays nothing out.
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { columnName, SheetView } from "./SheetView";
import { makeBook } from "./sheet/book.fixture";
import * as source from "./sheet/source";
import { revealArtifact } from "../../api";
import { openExternal } from "../../desktop";

vi.mock("../../api", () => ({ revealArtifact: vi.fn() }));
vi.mock("../../desktop", () => ({ openExternal: vi.fn() }));

let dataUrl = "";
beforeAll(async () => {
  dataUrl = "data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64," + (await makeBook());
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

const open = async () => {
  render(<SheetView sessionId="s1" path="reports/budget.xlsx" name="budget.xlsx" dataUrl={dataUrl} />);
  return screen.findByTestId("sheet-grid");
};
const cell = (r: number, c: number) => document.querySelector(`[data-cell="${r}:${c}"]`) as HTMLElement;
const address = () => screen.getByTestId("sheet-address").textContent;
const holds = () => screen.getByTestId("sheet-holds").textContent;

describe("SheetView", () => {
  it("names columns as Excel does", () => {
    expect([0, 25, 26, 701, 702].map(columnName)).toEqual(["A", "Z", "AA", "ZZ", "AAA"]);
  });

  it("shows the shape of a sheet while the file is read, then the sheet", async () => {
    render(<SheetView sessionId="s1" path="reports/budget.xlsx" name="budget.xlsx" dataUrl={dataUrl} />);
    expect(screen.getByTestId("sheet-wait")).toBeTruthy();
    expect(screen.getByTestId("sheet-view").getAttribute("aria-busy")).toBe("true");
    const grid = await screen.findByTestId("sheet-grid");
    expect(screen.queryByTestId("sheet-wait")).toBeNull();
    expect(grid.getAttribute("role")).toBe("grid");
    expect(grid.getAttribute("aria-label")).toBe("Budget");
    expect(screen.getByTestId("sheet-dims").textContent).toBe("6 rows · 4 columns");
    // A well-formed sheet needs no note.
    expect(screen.queryByTestId("sheet-note")).toBeNull();
  });

  it("draws cells with the file's own look", async () => {
    await open();
    await waitFor(() => expect(cell(2, 2)).toBeTruthy());
    // The head: white bold type on the file's blue, centred.
    const head = cell(1, 0);
    expect(head.textContent).toBe("Item");
    expect(head.style.backgroundColor).toBe("rgb(31, 78, 121)");
    expect(head.style.color).toBe("rgb(255, 255, 255)");
    expect(head.style.fontWeight).toBe("700");
    expect(head.style.justifyContent).toBe("center");
    // A number sits to the right, with its format; a negative one in the format's red.
    expect(cell(2, 2).textContent).toBe("$26,400.00");
    expect(cell(2, 2).style.justifyContent).toBe("flex-end");
    expect(cell(2, 2).style.borderBottom).toContain("double");
    expect(cell(3, 2).textContent).toBe("($880.00)");
    expect(cell(3, 2).style.color).toBe("rgb(192, 0, 0)");
    // The merged title is one cell as wide as its three columns (145 + 64 + 89).
    expect(cell(0, 0).style.width).toBe("298px");
    expect(cell(0, 1)).toBeNull();
    expect(cell(0, 0).style.fontSize).toBe("21.33px"); // 16pt
  });

  it("keeps the frozen rows in a part of their own, above the rows that scroll", async () => {
    await open();
    await waitFor(() => expect(cell(2, 0)).toBeTruthy());
    const frozen = document.querySelector(".sheet-band.is-frozen") as HTMLElement;
    const body = document.querySelector(".sheet-band.is-body") as HTMLElement;
    expect(frozen.contains(cell(1, 0))).toBe(true);
    expect(frozen.contains(cell(0, 0))).toBe(true);
    expect(body.contains(cell(2, 0))).toBe(true);
    expect(frozen.contains(cell(2, 0))).toBe(false);
  });

  it("shows what the selected cell holds: a formula, not its result", async () => {
    await open();
    await waitFor(() => expect(holds()).toBe("Budget"));
    expect(address()).toBe("A1");
    fireEvent.click(cell(2, 2));
    await waitFor(() => expect(holds()).toBe("=B3*110"));
    expect(address()).toBe("C3");
    fireEvent.click(cell(3, 2));
    await waitFor(() => expect(holds()).toBe("-880"));
    expect(screen.getByTestId("sheet-selection")).toBeTruthy();
  });

  it("moves the selection with the arrow keys, a merged cell as one", async () => {
    const grid = await open();
    await waitFor(() => expect(holds()).toBe("Budget"));
    fireEvent.keyDown(grid, { key: "ArrowRight" }); // out of A1:C1 by its far side
    expect(address()).toBe("D1");
    fireEvent.keyDown(grid, { key: "ArrowLeft" }); // and back into it, which is its first cell
    expect(address()).toBe("A1");
    fireEvent.keyDown(grid, { key: "ArrowDown" });
    fireEvent.keyDown(grid, { key: "ArrowDown" });
    fireEvent.keyDown(grid, { key: "ArrowRight" });
    expect(address()).toBe("B3");
    await waitFor(() => expect(holds()).toBe("240"));
    fireEvent.keyDown(grid, { key: "Home", ctrlKey: true });
    expect(address()).toBe("A3"); // the first cell under the frozen rows
  });

  it("opens a cell's web link in the person's browser", async () => {
    await open();
    await waitFor(() => expect(cell(5, 0)).toBeTruthy());
    fireEvent.click(cell(5, 0).querySelector(".sheet-link") as HTMLElement);
    expect(openExternal).toHaveBeenCalledWith("https://example.com/plan");
    expect(address()).toBe("A6");
  });

  it("has a tab for each sheet a person sees, and says what a sheet has that is not drawn", async () => {
    await open();
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((tab) => tab.textContent)).toEqual(["Budget", "By month", "Orders"]); // the hidden sheet has none
    expect(tabs[0].getAttribute("aria-selected")).toBe("true");
    fireEvent.click(tabs[1]);
    const note = await screen.findByTestId("sheet-note");
    expect(note.textContent).toContain("This sheet has 1 chart, 1 picture and some conditional formatting, which are not drawn here.");
    expect(screen.getByTestId("sheet-grid").getAttribute("aria-label")).toBe("By month");
    await waitFor(() => expect(holds()).toBe("Month"));
    fireEvent.click(screen.getByText("Open in Excel"));
    expect(revealArtifact).toHaveBeenCalledWith("s1", "reports/budget.xlsx", "open");
    // Going back, the first sheet's selection is where it was left.
    fireEvent.click(screen.getAllByRole("tab")[0]);
    await waitFor(() => expect(screen.getByTestId("sheet-grid").getAttribute("aria-label")).toBe("Budget"));
    expect(screen.queryByTestId("sheet-note")).toBeNull();
  });

  it("draws a table in its style, a rule that is met, a data bar and a picture", async () => {
    await open();
    fireEvent.click(screen.getAllByRole("tab")[2]);
    await waitFor(() => expect(screen.getByTestId("sheet-grid").getAttribute("aria-label")).toBe("Orders"));
    await waitFor(() => expect(cell(3, 2)).toBeTruthy());
    expect(screen.queryByTestId("sheet-note")).toBeNull(); // all of it is drawn
    // The head row of Excel's default table style, which the file names and does not carry.
    expect(cell(0, 0).style.backgroundColor).toBe("rgb(68, 114, 196)");
    expect(cell(0, 0).style.color).toBe("rgb(255, 255, 255)");
    expect(cell(0, 0).style.fontWeight).toBe("700");
    expect(cell(2, 0).style.backgroundColor).toBe(""); // the row between two stripes
    // -4 is under zero: the rule's colours, over the stripe.
    expect(cell(3, 2).style.backgroundColor).toBe("rgb(255, 199, 206)");
    expect(cell(3, 2).style.color).toBe("rgb(156, 0, 6)");
    // The largest quantity has the longest bar.
    expect((cell(3, 1).querySelector(".sheet-bar") as HTMLElement).style.width).toBe("90%");
    expect((cell(4, 1).querySelector(".sheet-bar") as HTMLElement).style.width).toBe("10%");
    expect(cell(0, 1).querySelector(".sheet-bar")).toBeNull();
    // The picture: at the size the file gives, from the corner of E2 (40 + 64 * 4 across).
    const picture = screen.getByTestId("sheet-picture") as HTMLImageElement;
    expect(picture.getAttribute("src")).toContain("data:image/png;base64,");
    expect(picture.alt).toBe("A red square");
    expect(picture.style.width).toBe("100px");
    expect(picture.style.height).toBe("50px");
    expect(picture.style.left).toBe("256px");
    expect(picture.style.top).toBe("20px");
  });

  it("zooms the sheet, type and all, and back to its own size", async () => {
    await open();
    await waitFor(() => expect(cell(0, 0)).toBeTruthy());
    expect(screen.getByTestId("sheet-zoom").textContent).toBe("100%");
    fireEvent.click(screen.getByTestId("sheet-zoom-in"));
    expect(screen.getByTestId("sheet-zoom").textContent).toBe("125%");
    expect(cell(0, 0).style.fontSize).toBe("26.67px");
    fireEvent.click(screen.getByTestId("sheet-zoom"));
    expect(screen.getByTestId("sheet-zoom").textContent).toBe("100%");
    expect(cell(0, 0).style.fontSize).toBe("21.33px");
  });

  it("says why a file could not be drawn, with the way to open it outside", async () => {
    vi.spyOn(source, "openSource").mockReturnValue({
      open: () => Promise.reject(new Error("File is password-protected")),
      sheet: () => Promise.reject(new Error("closed")),
      cells: () => Promise.resolve([]),
      close: () => undefined,
    });
    render(<SheetView sessionId="s1" path="payroll.xlsx" name="payroll.xlsx" dataUrl={dataUrl} />);
    const refusal = await screen.findByTestId("sheet-undrawn");
    expect(refusal.textContent).toContain("payroll.xlsx is protected with a password, so it cannot be drawn here.");
    fireEvent.click(screen.getByText("Open in Excel"));
    expect(revealArtifact).toHaveBeenCalledWith("s1", "payroll.xlsx", "open");
  });

  it("an older format is shown as its values, and says so", async () => {
    const XLSX = await import("xlsx");
    const workbook = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(workbook, XLSX.utils.aoa_to_sheet([["Item", "Hours"], ["Build", 240]]), "Old");
    const old = "data:application/vnd.ms-excel;base64," + XLSX.write(workbook, { bookType: "biff8", type: "base64" });
    render(<SheetView sessionId="s1" path="old.xls" name="old.xls" dataUrl={old} />);
    await screen.findByTestId("sheet-grid");
    expect(screen.getByTestId("sheet-note").textContent).toContain("Older format: shown as its values, without its formatting.");
    await waitFor(() => expect(cell(1, 0)?.textContent).toBe("Build"));
  });
});
