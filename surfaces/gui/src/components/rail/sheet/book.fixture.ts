// A small workbook, written part by part, for the tests of the sheet reader and the sheet
// view: the styles, the theme and the sheet XML are spelled out here so a test can say exactly
// what was in the file. Invented data.
import JSZip from "jszip";

const MAIN = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"';
const REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships";
const text = (value: string) => `t="inlineStr"><is><t>${value}</t></is></c>`;

const CONTENT_TYPES =
  '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">' +
  '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>' +
  '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>' +
  '<Default Extension="png" ContentType="image/png"/>' +
  [1, 2, 3, 4].map((n) => `<Override PartName="/xl/worksheets/sheet${n}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`).join("") +
  '<Override PartName="/xl/tables/table1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.table+xml"/>' +
  '<Override PartName="/xl/drawings/drawing2.xml" ContentType="application/vnd.openxmlformats-officedocument.drawing+xml"/>' +
  '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>' +
  '<Override PartName="/xl/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>' +
  '<Override PartName="/xl/drawings/drawing1.xml" ContentType="application/vnd.openxmlformats-officedocument.drawing+xml"/></Types>';

const WORKBOOK =
  `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook ${MAIN}><sheets>` +
  '<sheet name="Budget" sheetId="1" r:id="rId1"/><sheet name="By month" sheetId="2" r:id="rId2"/><sheet name="Scratch" sheetId="3" state="hidden" r:id="rId3"/><sheet name="Orders" sheetId="4" r:id="rId6"/>' +
  "</sheets></workbook>";

const WORKBOOK_RELS =
  '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
  [1, 2, 3].map((n) => `<Relationship Id="rId${n}" Type="${REL}/worksheet" Target="worksheets/sheet${n}.xml"/>`).join("") +
  `<Relationship Id="rId4" Type="${REL}/styles" Target="styles.xml"/><Relationship Id="rId5" Type="${REL}/theme" Target="theme/theme1.xml"/>` +
  `<Relationship Id="rId6" Type="${REL}/worksheet" Target="worksheets/sheet4.xml"/></Relationships>`;

export const THEME =
  '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Office"><a:themeElements><a:clrScheme name="Office">' +
  '<a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1><a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>' +
  '<a:dk2><a:srgbClr val="44546A"/></a:dk2><a:lt2><a:srgbClr val="E7E6E6"/></a:lt2>' +
  '<a:accent1><a:srgbClr val="4472C4"/></a:accent1><a:accent2><a:srgbClr val="ED7D31"/></a:accent2><a:accent3><a:srgbClr val="A5A5A5"/></a:accent3>' +
  '<a:accent4><a:srgbClr val="FFC000"/></a:accent4><a:accent5><a:srgbClr val="5B9BD5"/></a:accent5><a:accent6><a:srgbClr val="70AD47"/></a:accent6>' +
  '<a:hlink><a:srgbClr val="0563C1"/></a:hlink><a:folHlink><a:srgbClr val="954F72"/></a:folHlink></a:clrScheme>' +
  '<a:fontScheme name="Office"><a:majorFont><a:latin typeface="Calibri Light"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont><a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont></a:fontScheme>' +
  '<a:fmtScheme name="Office"><a:fillStyleLst/><a:lnStyleLst/><a:effectStyleLst/><a:bgFillStyleLst/></a:fmtScheme></a:themeElements></a:theme>';

/** The cell styles, in order: 0 plain, 1 title, 2 head, 3 money with edges, 4 band, 5 red when negative, 6 a column's fill, 7 a date. */
export const STYLES =
  `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet ${MAIN}>` +
  '<numFmts count="2"><numFmt numFmtId="164" formatCode="&quot;$&quot;#,##0.00"/><numFmt numFmtId="165" formatCode="&quot;$&quot;#,##0.00;[Red](&quot;$&quot;#,##0.00)"/></numFmts>' +
  '<fonts count="3"><font><sz val="11"/><color theme="1"/><name val="Calibri"/></font>' +
  '<font><b/><sz val="16"/><color rgb="FF1F4E79"/><name val="Calibri"/></font>' +
  '<font><b/><i val="0"/><sz val="11"/><color theme="0"/><name val="Calibri"/></font></fonts>' +
  '<fills count="4"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>' +
  '<fill><patternFill patternType="solid"><fgColor rgb="FF1F4E79"/><bgColor indexed="64"/></patternFill></fill>' +
  '<fill><patternFill patternType="solid"><fgColor theme="4" tint="0.79998168889431442"/><bgColor indexed="64"/></patternFill></fill></fills>' +
  '<borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border>' +
  '<border><left/><right/><top style="thin"><color auto="1"/></top><bottom style="double"><color rgb="FFC00000"/></bottom><diagonal/></border></borders>' +
  '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>' +
  '<cellXfs count="8"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>' +
  '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>' +
  '<xf numFmtId="0" fontId="2" fillId="2" borderId="0" xfId="0"><alignment horizontal="center" vertical="center"/></xf>' +
  '<xf numFmtId="164" fontId="0" fillId="0" borderId="1" xfId="0"/>' +
  '<xf numFmtId="0" fontId="0" fillId="3" borderId="0" xfId="0"/>' +
  '<xf numFmtId="165" fontId="0" fillId="3" borderId="0" xfId="0"/>' +
  '<xf numFmtId="0" fontId="0" fillId="2" borderId="0" xfId="0"/>' +
  '<xf numFmtId="14" fontId="0" fillId="0" borderId="0" xfId="0"><alignment horizontal="left" vertical="top" wrapText="1" indent="1"/></xf>' +
  // One differential style, for the rule on the Orders sheet: dark red type on a light red ground.
  '</cellXfs><dxfs count="1"><dxf><font><color rgb="FF9C0006"/></font><fill><patternFill><bgColor rgb="FFFFC7CE"/></patternFill></fill></dxf></dxfs></styleSheet>';

/** Budget: a merged title, a head, two frozen rows, a banded row styled as a whole, a filled column (with one number too long for it), a link. */
export const BUDGET =
  `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet ${MAIN}><dimension ref="A1:D6"/>` +
  '<sheetViews><sheetView workbookViewId="0"><pane ySplit="2" topLeftCell="A3" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>' +
  '<sheetFormatPr defaultRowHeight="15"/><cols><col min="1" max="1" width="20.7109375" customWidth="1"/><col min="3" max="3" width="12.7109375" customWidth="1"/><col min="4" max="4" width="9.140625" style="6"/></cols><sheetData>' +
  `<row r="1" ht="24" customHeight="1"><c r="A1" s="1" ${text("Budget")}<c r="B1" s="1"/><c r="C1" s="1"/></row>` +
  `<row r="2"><c r="A2" s="2" ${text("Item")}<c r="B2" s="2" ${text("Hours")}<c r="C2" s="2" ${text("Cost")}</row>` +
  `<row r="3"><c r="A3" ${text("Build")}<c r="B3"><v>240</v></c><c r="C3" s="3"><f>B3*110</f><v>26400</v></c><c r="D3" s="6"><v>123456789012</v></c></row>` +
  `<row r="4"><c r="A4" s="4" ${text("Design")}<c r="B4" s="4"><v>80</v></c><c r="C4" s="5"><v>-880</v></c></row>` +
  `<row r="5" s="4" customFormat="1"><c r="A5" s="4" ${text("A note that runs on")}</row>` +
  `<row r="6"><c r="A6" ${text("Start")}<c r="B6" s="7"><v>46048</v></c></row>` +
  '</sheetData><mergeCells count="1"><mergeCell ref="A1:C1"/></mergeCells><hyperlinks><hyperlink ref="A6" r:id="rId1"/></hyperlinks></worksheet>';

const BUDGET_RELS =
  '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
  `<Relationship Id="rId1" Type="${REL}/hyperlink" Target="https://example.com/plan" TargetMode="External"/></Relationships>`;

/** By month: no gridlines, a rule of conditional formatting that names no style, and a drawing with a chart and a picture that names no file. */
const MONTH =
  `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet ${MAIN}><dimension ref="A1:B2"/>` +
  '<sheetViews><sheetView showGridLines="0" workbookViewId="0"/></sheetViews><sheetData>' +
  `<row r="1"><c r="A1" ${text("Month")}<c r="B1" ${text("Planned")}</row><row r="2"><c r="A2" ${text("January")}<c r="B2"><v>4000</v></c></row>` +
  '</sheetData><conditionalFormatting sqref="B2"><cfRule type="cellIs" priority="1" operator="lessThan"><formula>0</formula></cfRule></conditionalFormatting>' +
  '<drawing r:id="rId1"/></worksheet>';

const MONTH_RELS =
  '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
  `<Relationship Id="rId1" Type="${REL}/drawing" Target="../drawings/drawing1.xml"/></Relationships>`;

const DRAWING =
  '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing">' +
  "<xdr:twoCellAnchor><xdr:graphicFrame macro=\"\"></xdr:graphicFrame></xdr:twoCellAnchor><xdr:twoCellAnchor><xdr:pic></xdr:pic></xdr:twoCellAnchor></xdr:wsDr>";

/**
 * Orders: a table in Excel's default style, a rule that marks what is under zero, a data bar on
 * the quantities, and a picture whose corner is in E2.
 */
const ORDERS =
  `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet ${MAIN}><dimension ref="A1:C5"/><sheetData>` +
  `<row r="1"><c r="A1" ${text("Item")}<c r="B1" ${text("Qty")}<c r="C1" ${text("Left")}</row>` +
  `<row r="2"><c r="A2" ${text("Apples")}<c r="B2"><v>12</v></c><c r="C2"><v>3</v></c></row>` +
  `<row r="3"><c r="A3" ${text("Pears")}<c r="B3"><v>7</v></c><c r="C3"><v>9</v></c></row>` +
  `<row r="4"><c r="A4" ${text("Plums")}<c r="B4"><v>20</v></c><c r="C4"><v>-4</v></c></row>` +
  `<row r="5"><c r="A5" ${text("Figs")}<c r="B5"><v>2</v></c><c r="C5"><v>6</v></c></row>` +
  '</sheetData><conditionalFormatting sqref="C2:C5"><cfRule type="cellIs" dxfId="0" priority="1" operator="lessThan"><formula>0</formula></cfRule></conditionalFormatting>' +
  '<conditionalFormatting sqref="B2:B5"><cfRule type="dataBar" priority="2"><dataBar><cfvo type="min"/><cfvo type="max"/><color rgb="FF638EC6"/></dataBar></cfRule></conditionalFormatting>' +
  '<drawing r:id="rId2"/><tableParts count="1"><tablePart r:id="rId1"/></tableParts></worksheet>';

const ORDERS_RELS =
  '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
  `<Relationship Id="rId1" Type="${REL}/table" Target="../tables/table1.xml"/><Relationship Id="rId2" Type="${REL}/drawing" Target="../drawings/drawing2.xml"/></Relationships>`;

const ORDERS_TABLE =
  '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><table xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" id="1" name="Orders" displayName="Orders" ref="A1:C5" totalsRowShown="0">' +
  '<autoFilter ref="A1:C5"/><tableColumns count="3"><tableColumn id="1" name="Item"/><tableColumn id="2" name="Qty"/><tableColumn id="3" name="Left"/></tableColumns>' +
  '<tableStyleInfo name="TableStyleMedium2" showFirstColumn="0" showLastColumn="0" showRowStripes="1" showColumnStripes="0"/></table>';

// 100 by 50 pixels (9525 EMU each), from the corner of E2.
const ORDERS_DRAWING =
  '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">' +
  "<xdr:oneCellAnchor><xdr:from><xdr:col>4</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>1</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>" +
  '<xdr:ext cx="952500" cy="476250"/><xdr:pic><xdr:nvPicPr><xdr:cNvPr id="2" name="Picture 1" descr="A red square"/><xdr:cNvPicPr/></xdr:nvPicPr>' +
  '<xdr:blipFill><a:blip r:embed="rId1"/><a:stretch><a:fillRect/></a:stretch></xdr:blipFill>' +
  '<xdr:spPr><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></xdr:spPr></xdr:pic><xdr:clientData/></xdr:oneCellAnchor></xdr:wsDr>';

const ORDERS_DRAWING_RELS =
  '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
  `<Relationship Id="rId1" Type="${REL}/image" Target="../media/image1.png"/></Relationships>`;

/** A PNG of one red pixel. */
export const RED_PIXEL = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";

const SCRATCH = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet ${MAIN}><sheetData><row r="1"><c r="A1" ${text("not shown")}</row></sheetData></worksheet>`;

function utf16(xml: string): Uint8Array {
  const text = xml.replace('encoding="UTF-8"', 'encoding="UTF-16"');
  const bytes = new Uint8Array(2 + text.length * 2);
  bytes.set([0xff, 0xfe]);
  for (let i = 0; i < text.length; i++) {
    bytes[2 + i * 2] = text.charCodeAt(i) & 0xff;
    bytes[3 + i * 2] = text.charCodeAt(i) >> 8;
  }
  return bytes;
}

/**
 * The workbook as base64, the form the app is handed a file in. `odd` makes it one of the
 * workbooks that are valid and unusual: its style sheet at another path, its first sheet in
 * UTF-16.
 */
export async function makeBook(odd: { stylesAt?: string; utf16Sheet?: boolean } = {}): Promise<string> {
  const zip = new JSZip();
  const stylesAt = odd.stylesAt ?? "xl/styles.xml";
  zip.file("[Content_Types].xml", CONTENT_TYPES.replace("/xl/styles.xml", "/" + stylesAt));
  zip.file("_rels/.rels", `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="${REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>`);
  zip.file("xl/workbook.xml", WORKBOOK);
  zip.file("xl/_rels/workbook.xml.rels", WORKBOOK_RELS.replace('Target="styles.xml"', `Target="/${stylesAt}"`));
  zip.file(stylesAt, STYLES);
  zip.file("xl/theme/theme1.xml", THEME);
  zip.file("xl/worksheets/sheet1.xml", odd.utf16Sheet ? utf16(BUDGET) : BUDGET);
  zip.file("xl/worksheets/_rels/sheet1.xml.rels", BUDGET_RELS);
  zip.file("xl/worksheets/sheet2.xml", MONTH);
  zip.file("xl/worksheets/_rels/sheet2.xml.rels", MONTH_RELS);
  zip.file("xl/worksheets/sheet3.xml", SCRATCH);
  zip.file("xl/drawings/drawing1.xml", DRAWING);
  zip.file("xl/worksheets/sheet4.xml", ORDERS);
  zip.file("xl/worksheets/_rels/sheet4.xml.rels", ORDERS_RELS);
  zip.file("xl/tables/table1.xml", ORDERS_TABLE);
  zip.file("xl/drawings/drawing2.xml", ORDERS_DRAWING);
  zip.file("xl/drawings/_rels/drawing2.xml.rels", ORDERS_DRAWING_RELS);
  zip.file("xl/media/image1.png", RED_PIXEL, { base64: true });
  return zip.generateAsync({ type: "base64" });
}
