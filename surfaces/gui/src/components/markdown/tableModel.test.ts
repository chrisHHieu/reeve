import { describe, expect, it } from "vitest";
import { columnStats, fold, isTotalRow, numericColumn, parseNumber, viewOrder } from "./tableModel";

describe("parseNumber", () => {
  it("reads the numbers tables write, with their units and grouping", () => {
    expect(parseNumber("6.500.000đ")).toBe(6500000);
    expect(parseNumber("1,250,000")).toBe(1250000);
    expect(parseNumber("$1,200.50")).toBe(1200.5);
    expect(parseNumber("6.500,25 VND")).toBe(6500.25);
    expect(parseNumber("12%")).toBe(12);
    expect(parseNumber("-3,5")).toBe(-3.5);
    expect(parseNumber("−42")).toBe(-42);
    expect(parseNumber("0.75")).toBe(0.75);
    expect(parseNumber("1.2 tr")).toBe(1.2);
  });

  it("leaves text, dates and versions alone", () => {
    for (const text of ["Nhà ở", "01/09", "v1.2.3", "2026-10-09", "", "1.2.3.4", "abc12def"]) {
      expect(parseNumber(text)).toBeNull();
    }
  });
});

describe("viewOrder", () => {
  const body = [
    ["Tiền nhà", "Nhà ở", "6.500.000"],
    ["Đi chợ", "Ăn uống", "1.250.000"],
    ["Điện", "Hóa đơn", "780.000"],
    ["Cà phê", "Ăn uống", ""],
    ["Học phí", "Học tập", "2.400.000"],
  ];

  it("sorts a money column by value, not by text, and sinks empty cells either way", () => {
    expect(viewOrder(body, { column: 2, direction: "ascending", query: "" })).toEqual([2, 1, 4, 0, 3]);
    expect(viewOrder(body, { column: 2, direction: "descending", query: "" })).toEqual([0, 4, 1, 2, 3]);
  });

  it("keeps the written order with no sort, and for ties", () => {
    expect(viewOrder(body, { column: null, direction: "ascending", query: "" })).toEqual([0, 1, 2, 3, 4]);
    // Ăn uống (1, 3 — tied, kept in written order), Hóa đơn, Học tập, Nhà ở.
    expect(viewOrder(body, { column: 1, direction: "ascending", query: "" })).toEqual([1, 3, 2, 4, 0]);
  });

  it("filters across every cell, ignoring case and Vietnamese accents", () => {
    expect(viewOrder(body, { column: null, direction: "ascending", query: "an uong" })).toEqual([1, 3]);
    expect(fold("Đi chợ")).toBe("di cho");
  });
});

describe("column stats", () => {
  it("sum, mean and range over the rows in view, only for a column that is all numbers", () => {
    const body = [["a", "10"], ["b", "30"], ["c", "20"]];
    expect(columnStats(body, [0, 2], 1)).toEqual({ count: 2, sum: 30, mean: 15, min: 10, max: 20 });
    expect(columnStats(body, [0, 1, 2], 0)).toBeNull();
    expect(numericColumn([["1"], ["x"]], 0)).toBe(false);
  });

  it("knows a closing total row", () => {
    expect(isTotalRow(["**Tổng**", "9.000"])).toBe(true);
    expect(isTotalRow(["Total", "9"])).toBe(true);
    expect(isTotalRow(["Tổ chức", "1"])).toBe(false);
  });
});
