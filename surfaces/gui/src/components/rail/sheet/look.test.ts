// How a workbook's cells look, read from its style sheet and its sheets' XML.
import { describe, expect, it } from "vitest";
import { BUDGET, STYLES } from "./book.fixture";
import { cellKey, colourOf, formatColour, marksEmpty, readLooks, readSheetFacts, readStyleSheet, tinted } from "./look";
import { child, find, parseXml } from "./xml";

// The workbook's theme, in the order a style names it: light 1, dark 1, light 2, dark 2, accents.
const THEME = ["FFFFFF", "000000", "E7E6E6", "44546A", "4472C4", "ED7D31", "A5A5A5", "FFC000", "5B9BD5", "70AD47", "0563C1", "954F72"];
const channels = (hex: string) => [1, 3, 5].map((at) => parseInt(hex.replace("#", "").padStart(6, "0").slice(at - 1, at + 1), 16));

describe("parseXml", () => {
  it("reads elements whatever prefix they carry, and their attributes as written", () => {
    const root = parseXml('<?xml version="1.0"?><!-- a note --><x:styleSheet xmlns:x="urn:x"><x:fonts count="1"><x:font><x:name val="A &amp; B"/><x:b/></x:font></x:fonts><x:sheet r:id="rId3"/></x:styleSheet>');
    const sheet = child(root, "styleSheet");
    expect(sheet?.attrs.x).toBe("urn:x");
    expect(find(root, "name")?.attrs.val).toBe("A & B");
    expect(child(child(child(sheet, "fonts"), "font"), "b")).toBeTruthy();
    expect(child(sheet, "sheet")?.attrs.id).toBe("rId3");
  });
});

describe("colours", () => {
  it("lightens and darkens a theme colour the way Excel names its swatches", () => {
    // "Blue, Accent 1, Lighter 80%" is D9E1F2 in Excel; its arithmetic rounds a step apart.
    const light = channels(tinted("4472C4", 0.79998168889431442));
    channels("D9E1F2").forEach((want, at) => expect(Math.abs(light[at] - want)).toBeLessThanOrEqual(2));
    const dark = channels(tinted("4472C4", -0.249977111117893));
    channels("2F5597").forEach((want, at) => expect(Math.abs(dark[at] - want)).toBeLessThanOrEqual(2));
    expect(tinted("FFFFFF", -0.5)).toBe("808080");
  });

  it("reads a colour given outright, by theme, or by number, and nothing for automatic", () => {
    const of = (attrs: string) => colourOf(parseXml(`<color ${attrs}/>`).kids[0], THEME);
    expect(of('rgb="FF1F4E79"')).toBe("#1F4E79");
    expect(of('theme="4"')).toBe("#4472C4");
    expect(of('theme="0"')).toBe("#FFFFFF");
    expect(of('indexed="10"')).toBe("#FF0000");
    expect(of('auto="1"')).toBeUndefined();
    expect(of('indexed="64"')).toBeUndefined();
    expect(of('rgb="red; background:url(x)"')).toBeUndefined();
    expect(colourOf(undefined, THEME)).toBeUndefined();
  });

  it("knows the colour a number format gives a value", () => {
    const money = '"$"#,##0.00;[Red]("$"#,##0.00)';
    expect(formatColour(money, -880)).toBe("#C00000");
    expect(formatColour(money, 880)).toBeUndefined();
    expect(formatColour("[Blue]0.0", 3)).toBe("#0000FF");
    expect(formatColour("0;[Color 3]-0;[Green]0", -1)).toBe("#FF0000");
    expect(formatColour("0;[Color 3]-0;[Green]0", 0)).toBe("#008000");
    expect(formatColour("General", -1)).toBeUndefined();
    expect(formatColour(undefined, -1)).toBeUndefined();
  });
});

describe("readLooks", () => {
  const looks = readLooks(STYLES, THEME);

  it("reads every cell style, in order", () => {
    expect(looks).toHaveLength(8);
    expect(looks[0]).toEqual({ font: "Calibri", size: 11 });
    expect(looks[1]).toEqual({ font: "Calibri", size: 16, bold: true, color: "#1F4E79" });
    // `<i val="0"/>` is not italic.
    expect(looks[2]).toEqual({ font: "Calibri", size: 11, bold: true, color: "#FFFFFF", fill: "#1F4E79", align: "center", valign: "middle" });
  });

  it("reads the edges, a theme fill with its tint, and how the text sits", () => {
    expect(looks[3].top).toEqual({ width: 1, style: "solid", color: "#000000" });
    expect(looks[3].bottom).toEqual({ width: 3, style: "double", color: "#C00000" });
    expect(looks[3].left).toBeUndefined();
    const band = channels(looks[4].fill as string);
    channels("D9E1F2").forEach((want, at) => expect(Math.abs(band[at] - want)).toBeLessThanOrEqual(2));
    expect(looks[7]).toMatchObject({ align: "left", valign: "top", wrap: true, indent: 1 });
    expect(marksEmpty(looks[4])).toBe(true);
    expect(marksEmpty(looks[1])).toBe(false);
  });

  it("does not put into a style a font name that is not one", () => {
    const hostile = STYLES.replace('<font><sz val="11"/><color theme="1"/><name val="Calibri"/></font>', '<font><sz val="11"/><name val="x&quot;; background:url(evil)"/></font>');
    expect(hostile).not.toBe(STYLES);
    expect(readLooks(hostile, THEME)[0]).toEqual({ size: 11 });
  });
});

describe("readStyleSheet", () => {
  it("reads the differential styles, and a table style the workbook defines itself", () => {
    const sheet = readStyleSheet(
      '<styleSheet><dxfs count="2">' +
        '<dxf><font><b/><color rgb="FFFFFFFF"/></font><fill><patternFill patternType="solid"><fgColor rgb="FF112233"/><bgColor rgb="FF445566"/></patternFill></fill></dxf>' +
        '<dxf><border><top style="thin"><color rgb="FF000000"/></top><horizontal style="hair"/></border></dxf></dxfs>' +
        '<tableStyles count="2"><tableStyle name="Mine" pivot="0" count="2"><tableStyleElement type="headerRow" dxfId="0"/><tableStyleElement type="firstRowStripe" size="2" dxfId="1"/></tableStyle>' +
        '<tableStyle name="ForPivots" table="0" count="1"><tableStyleElement type="headerRow" dxfId="0"/></tableStyle></tableStyles></styleSheet>',
      THEME,
    );
    // A differential style's fill is its pattern's BACKGROUND colour; a cell style's is the foreground.
    expect(sheet.parts[0]).toEqual({ bold: true, color: "#FFFFFF", fill: "#445566" });
    expect(sheet.tableStyles.get("Mine")?.headerRow).toEqual(sheet.parts[0]);
    expect(sheet.tableStyles.get("Mine")?.firstRowStripe).toEqual({
      size: 2,
      top: { width: 1, style: "solid", color: "#000000" },
      horizontal: { width: 1, style: "dotted", color: "#000000" },
    });
    expect(sheet.tableStyles.has("ForPivots")).toBe(false);
  });
});

describe("readSheetFacts", () => {
  it("reads which style each cell names, the frozen rows, and the styles of whole rows and columns", () => {
    const facts = readSheetFacts(BUDGET);
    expect(facts.frozenRows).toBe(2);
    expect(facts.frozenCols).toBe(0);
    expect(facts.gridlines).toBe(true);
    expect(facts.defaultRowPt).toBe(15);
    expect(facts.styleAt.get(cellKey(0, 0))).toBe(1);
    expect(facts.styleAt.get(cellKey(0, 2))).toBe(1); // an empty cell of the merge is styled too
    expect(facts.styleAt.get(cellKey(2, 2))).toBe(3);
    expect(facts.styleAt.has(cellKey(2, 1))).toBe(false); // the default style is not kept
    expect(facts.rowStyle.get(4)).toBe(4);
    expect(facts.colStyle).toEqual([[3, 3, 6]]);
    expect(facts.drawing).toBeNull();
    expect(facts.conditional).toBe(0);
  });

  it("reads what a sheet has that is not drawn, and a sheet that hides its gridlines", () => {
    const facts = readSheetFacts(
      '<worksheet><sheetViews><sheetView showGridLines="0"><pane xSplit="1" ySplit="3" state="frozenSplit"/></sheetView></sheetViews><sheetData><row r="1"><c r="A1"/></row></sheetData>' +
        '<conditionalFormatting sqref="A1"/><conditionalFormatting sqref="B1"/><drawing r:id="rId7"/><tableParts count="1"><tablePart r:id="rId8"/></tableParts>' +
        "<extLst><ext><x14:sparklineGroups><x14:sparklineGroup/></x14:sparklineGroups></ext></extLst></worksheet>",
    );
    expect(facts).toMatchObject({ gridlines: false, frozenRows: 3, frozenCols: 1, conditional: 2, tables: 1, sparklines: true, slicers: false, drawing: "rId7" });
  });

  it("a pane that is split but not frozen freezes nothing", () => {
    expect(readSheetFacts('<worksheet><sheetViews><sheetView><pane xSplit="2400" ySplit="1800" state="split"/></sheetView></sheetViews><sheetData/></worksheet>')).toMatchObject({ frozenRows: 0, frozenCols: 0 });
  });
});
