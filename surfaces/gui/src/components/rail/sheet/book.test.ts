// A workbook read from its bytes: what each cell holds, joined to how it looks.
import { describe, expect, it } from "vitest";
import * as XLSX from "xlsx";
import { readBook, readFailure, type CellView } from "./book";
import { makeBook, RED_PIXEL } from "./book.fixture";

const at = (cells: CellView[], r: number, c: number) => cells.find((cell) => cell.r === r && cell.c === c);

describe("readBook", () => {
  it("lists the sheets a person sees, and reads the look with the values", async () => {
    const book = await readBook(await makeBook());
    expect(book.info.sheets).toEqual(["Budget", "By month", "Orders"]); // the hidden one is left out
    expect(book.info.styled).toBe(true);
    expect(book.info.looks).toHaveLength(8);

    const sheet = book.sheet(0);
    expect(sheet).toMatchObject({ name: "Budget", rows: 6, cols: 4, frozenRows: 2, frozenCols: 0, gridlines: true, merges: [[0, 0, 0, 2]], notDrawn: [] });
    expect(sheet.widths[0]).toBe(145); // 20.71 characters at seven pixels each, as Excel counts
    expect(sheet.widths[1]).toBe(64);
    expect(sheet.widths[2]).toBe(89);
    expect(sheet.heights[0]).toBe(32); // 24pt
    expect(sheet.heights[1]).toBe(20);
  });

  it("gives the cells of a part of a sheet: text as Excel shows it, formula, colour, merge", async () => {
    const book = await readBook(await makeBook());
    const cells = book.cells(0, [[0, 5, 0, 3]]);

    const title = at(cells, 0, 0);
    expect(title).toMatchObject({ text: "Budget", kind: "text", look: 1, end: [0, 2], lookEnd: 1 });
    // The rest of a merge is its first cell: asked for or not, it is given once.
    expect(at(cells, 0, 1)).toBeUndefined();
    expect(cells.filter((cell) => cell.r === 0)).toHaveLength(2); // the title, and the filled column's cell

    expect(at(cells, 2, 2)).toMatchObject({ text: "$26,400.00", kind: "number", look: 3, held: "=B3*110" });
    expect(at(cells, 2, 1)).toMatchObject({ text: "240", kind: "number", look: 0 });
    expect(at(cells, 2, 1)?.held).toBeUndefined(); // the bar would show the same
    expect(at(cells, 3, 2)).toMatchObject({ text: "($880.00)", color: "#C00000", held: "-880", look: 5 });
    // A date is shown as its date, in the cell and in the bar.
    const date = at(cells, 5, 1);
    expect(date?.text).toMatch(/^1\/26\/(20)?26$/);
    expect(date?.held).toBeUndefined();
    expect(at(cells, 5, 0)).toMatchObject({ text: "Start", link: "https://example.com/plan" });
  });

  it("gives an empty cell only when its style can be seen, by cell, row or column", async () => {
    const book = await readBook(await makeBook());
    const cells = book.cells(0, [[0, 5, 0, 3]]);
    expect(at(cells, 1, 3)).toMatchObject({ text: "", look: 6 }); // the column's fill
    expect(at(cells, 4, 1)).toMatchObject({ text: "", look: 4 }); // the row's
    expect(at(cells, 4, 3)).toMatchObject({ look: 4 }); // a row's style is over a column's
    expect(at(cells, 5, 2)).toBeUndefined(); // nothing in it, nothing on it
    // Text runs over what is empty after it, and stops at what is not.
    expect(at(cells, 4, 0)?.until).toBeGreaterThan(4);
    expect(at(cells, 2, 0)?.until).toBeUndefined();
  });

  it("gives a merged cell when any part of it is asked for", async () => {
    const book = await readBook(await makeBook());
    const cells = book.cells(0, [[0, 0, 2, 2]]);
    expect(cells).toHaveLength(1);
    expect(cells[0]).toMatchObject({ r: 0, c: 0, text: "Budget", end: [0, 2] });
  });

  it("says what a sheet has that is not drawn", async () => {
    const book = await readBook(await makeBook());
    // A picture with no file behind it and a rule with no style of its own cannot be drawn.
    expect(book.sheet(1)).toMatchObject({ name: "By month", gridlines: false, notDrawn: ["1 chart", "1 picture", "some conditional formatting"] });
  });

  it("lays a table's style under its cells: the head row, the stripes, the lines between rows", async () => {
    const book = await readBook(await makeBook());
    expect(book.sheet(2)).toMatchObject({ name: "Orders", notDrawn: [] });
    const cells = book.cells(2, [[0, 4, 0, 2]]);
    // Excel's default style in this workbook's theme: white bold type on accent 1.
    expect(at(cells, 0, 0)?.own).toMatchObject({ fill: "#4472C4", color: "#FFFFFF", bold: true });
    // Every other row of the data is striped; the rows between are not.
    expect(at(cells, 1, 0)?.own?.fill).toMatch(/^#D[9A]E[1-3]F[23]$/);
    expect(at(cells, 2, 0)?.own?.fill).toBeUndefined();
    expect(at(cells, 3, 0)?.own?.fill).toMatch(/^#D[9A]E[1-3]F[23]$/);
    // A line between two rows is given to the upper cell only, so it is drawn once.
    expect(at(cells, 2, 0)?.own?.bottom).toMatchObject({ width: 1, style: "solid" });
    expect(at(cells, 2, 0)?.own?.top).toBeUndefined();
    expect(at(cells, 0, 0)?.own?.top).toMatchObject({ width: 1 }); // the table's own edge
    expect(at(cells, 0, 0)?.own?.left).toMatchObject({ width: 1 });
    expect(at(cells, 0, 1)?.own?.left).toBeUndefined();
  });

  it("lays a rule of conditional formatting over the cell that meets it, and gives a data bar its length", async () => {
    const book = await readBook(await makeBook());
    const cells = book.cells(2, [[0, 4, 0, 2]]);
    // Under zero: the rule's red, over the stripe the table gave the row.
    expect(at(cells, 3, 2)?.own).toMatchObject({ fill: "#FFC7CE", color: "#9C0006" });
    expect(at(cells, 1, 2)?.own?.fill).toMatch(/^#D/); // 3 is not under zero: the stripe stays
    // The longest bar is the largest value, the shortest the smallest (Excel's 90% and 10%).
    expect(at(cells, 3, 1)?.bar).toEqual({ width: 0.9, color: "#638EC6" });
    expect(at(cells, 4, 1)?.bar).toEqual({ width: 0.1, color: "#638EC6" });
    expect(at(cells, 1, 1)?.bar?.width).toBeCloseTo(0.1 + (10 / 18) * 0.8, 5);
    expect(at(cells, 0, 1)?.bar).toBeUndefined(); // the head is not in the rule
  });

  it("gives a picture with the cell it is anchored to, and a grid long enough to reach it", async () => {
    const book = await readBook(await makeBook());
    const sheet = book.sheet(2);
    expect(sheet.pictures).toHaveLength(1);
    expect(sheet.pictures[0]).toMatchObject({ r: 1, c: 4, dx: 0, dy: 0, w: 100, h: 50, alt: "A red square" });
    expect(sheet.pictures[0].src).toBe("data:image/png;base64," + RED_PIXEL);
    expect(sheet.cols).toBe(3); // the cells that are used
    expect(sheet.widths.length).toBeGreaterThanOrEqual(6); // the picture ends in column F
  });

  it("finds the style sheet where the package says it is, and reads a sheet that is UTF-16", async () => {
    // Both are valid workbooks; both were read as having no look before the parts were looked
    // up in the package and a part's first two bytes were read.
    const moved = await readBook(await makeBook({ stylesAt: "xl/look/sheet-styles.xml" }));
    expect(moved.info.styled).toBe(true);
    expect(moved.info.looks).toHaveLength(8);
    expect(at(moved.cells(0, [[1, 1, 0, 0]]), 1, 0)).toMatchObject({ text: "Item", look: 2 });

    const wide = await readBook(await makeBook({ utf16Sheet: true }));
    expect(wide.sheet(0)).toMatchObject({ frozenRows: 2, rows: 6 });
    expect(at(wide.cells(0, [[1, 1, 0, 0]]), 1, 0)).toMatchObject({ text: "Item", look: 2 });
  });

  it("reads an older format as its values, and says it has no look", async () => {
    const sheet = XLSX.utils.aoa_to_sheet([
      ["Item", "Hours"],
      ["Build", 240],
    ]);
    const workbook = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(workbook, sheet, "Old");
    const book = await readBook(XLSX.write(workbook, { bookType: "biff8", type: "base64" }));
    expect(book.info.styled).toBe(false);
    expect(book.info.sheets).toEqual(["Old"]);
    const cells = book.cells(0, [[0, 1, 0, 1]]);
    expect(at(cells, 1, 0)).toMatchObject({ text: "Build", look: 0 });
    expect(at(cells, 1, 1)).toMatchObject({ text: "240", kind: "number" });
    expect(book.sheet(0).notDrawn).toEqual([]);
  });
});

describe("readFailure", () => {
  it("says a locked file is locked, and anything else as it was said", () => {
    expect(readFailure(new Error("File is password-protected"), "payroll.xlsx")).toBe("payroll.xlsx is protected with a password, so it cannot be drawn here.");
    expect(readFailure(new Error("Unsupported ZIP encryption"), "a.xlsx")).toContain("protected with a password");
    expect(readFailure(new Error("bad zip"), "a.xlsx")).toBe("a.xlsx could not be drawn here: bad zip");
  });
});
