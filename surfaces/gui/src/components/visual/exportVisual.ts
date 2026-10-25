// Taking a visual out of the conversation: its data as a table a spreadsheet or a
// document pastes as cells, and its picture as a PNG to put in a report.
//
// The data is the block's own (labels and series, rows, figures), not text scraped from the
// screen, except a what-if's, whose figures exist only as the person left the sliders: those are
// read from the block as drawn. The picture is the block as drawn, styles inlined, rasterised
// through an SVG foreignObject at twice its size, so it is what the person saw.

import { formatValue, type NumberLocale } from "./format";
import type { VisualBlock } from "./visualModel";

export interface CopiedTable {
  columns: string[];
  rows: string[][];
}

/** A block's data as one table, or null for a block that has none (a form). */
export function blockTable(block: VisualBlock, locale: NumberLocale, drawn?: HTMLElement | null): CopiedTable | null {
  const num = (value: number, unit?: string) => formatValue(value, { unit, locale });
  switch (block.type) {
    case "table":
      return { columns: block.columns, rows: block.rows };
    case "chart": {
      const n = Math.min(block.labels.length, ...block.series.map((s) => s.values.length));
      return {
        columns: ["", ...block.series.map((s) => s.name)],
        rows: block.labels.slice(0, n).map((label, i) => [label, ...block.series.map((s) => num(s.values[i], block.unit))]),
      };
    }
    case "metrics":
      return {
        columns: ["", ""],
        rows: block.items.map((item) => [
          item.label,
          typeof item.value === "number" ? num(item.value, item.unit) : `${item.value}${item.unit ? ` ${item.unit}` : ""}`,
        ]),
      };
    case "compare":
      return { columns: ["", ...block.options.map((o) => o.name)], rows: block.rows.map((r) => [r.label, ...r.values]) };
    case "timeline":
      return {
        columns: ["", "Start", "End", "Group"],
        rows: block.items.map((item) => [item.label, item.start, item.end ?? "", item.group ?? ""]),
      };
    case "map":
      return {
        columns: ["", block.unit ? `Value (${block.unit})` : "Value"],
        rows: [
          ...block.regions.map((r) => [r.name, num(r.value)]),
          ...block.points.map((p) => [p.label, p.value === undefined ? `${p.lat}, ${p.lon}` : num(p.value)]),
        ],
      };
    case "what_if": {
      // The figures as the person left them: read from the block as drawn.
      if (!drawn) return null;
      const pairs = (selector: string, label: string, value: string) =>
        [...drawn.querySelectorAll(selector)].map((node) => [
          node.querySelector(label)?.textContent?.trim() ?? "",
          node.querySelector(value)?.textContent?.trim() ?? "",
        ]);
      return { columns: ["", ""], rows: [...pairs(".vz-slider", ".vz-slider-head > span", "output"), ...pairs(".vz-output", ".vz-output-label", ".vz-output-value")] };
    }
    default:
      return null;
  }
}

const escapeHtml = (text: string) => text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

/** Tab-separated text: what a spreadsheet pastes into cells from plain text. */
export function tableText(table: CopiedTable, title?: string): string {
  const clean = (cell: string) => cell.replace(/[\t\r\n]+/g, " ");
  const head = table.columns.some((c) => c) ? [table.columns.map(clean).join("\t")] : [];
  return [...(title ? [title] : []), ...head, ...table.rows.map((row) => row.map(clean).join("\t"))].join("\n");
}

/** An HTML table: what Word and Excel paste as a table with its cells. */
export function tableHtml(table: CopiedTable, title?: string): string {
  const cells = (row: string[], tag: string) => `<tr>${row.map((c) => `<${tag}>${escapeHtml(c)}</${tag}>`).join("")}</tr>`;
  const head = table.columns.some((c) => c) ? `<thead>${cells(table.columns, "th")}</thead>` : "";
  return `${title ? `<p><b>${escapeHtml(title)}</b></p>` : ""}<table>${head}<tbody>${table.rows.map((r) => cells(r, "td")).join("")}</tbody></table>`;
}

/** Puts both forms on the clipboard (a document pastes the table, a plain field the text);
 *  plain text alone where rich clipboard writes are refused. */
export async function copyTable(table: CopiedTable, title?: string): Promise<boolean> {
  const text = tableText(table, title);
  try {
    if (typeof ClipboardItem !== "undefined" && navigator.clipboard?.write) {
      await navigator.clipboard.write([
        new ClipboardItem({
          "text/plain": new Blob([text], { type: "text/plain" }),
          "text/html": new Blob([tableHtml(table, title)], { type: "text/html" }),
        }),
      ]);
      return true;
    }
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      return false;
    }
  }
}

/** Every computed style of `from` and its descendants written onto `to`, a clone of it: an image
 *  of the node carries no stylesheet, so the styles travel inline. */
function inlineStyles(from: Element, to: Element): void {
  const style = getComputedStyle(from);
  let text = "";
  for (let i = 0; i < style.length; i++) {
    const name = style[i];
    text += `${name}:${style.getPropertyValue(name)};`;
  }
  // An <svg> sized by CSS (a width from its viewBox, `height: auto`) reads back unresolved, and
  // an image of it drew the map half again too large: its drawn box is written as pixels.
  if (from instanceof SVGSVGElement) {
    const box = from.getBoundingClientRect();
    text += `width:${box.width}px;height:${box.height}px;`;
    // Its `margin: auto` reads back as 0 too, so a centred map came out at the left edge.
    const parent = from.parentElement;
    if (parent) {
      const outer = parent.getBoundingClientRect();
      const inner = getComputedStyle(parent);
      const left = box.left - outer.left - parseFloat(inner.paddingLeft || "0") - parseFloat(inner.borderLeftWidth || "0");
      text += `margin-left:${Math.max(0, left)}px;`;
    }
  }
  (to as HTMLElement).setAttribute("style", text);
  const a = from.children;
  const b = to.children;
  for (let i = 0; i < a.length && i < b.length; i++) inlineStyles(a[i], b[i]);
}

/** The block as drawn, as a PNG at twice its size, without the export bar. */
export async function blockPng(node: HTMLElement): Promise<Blob | null> {
  const box = node.getBoundingClientRect();
  if (box.width === 0 || box.height === 0) return null;
  const clone = node.cloneNode(true) as HTMLElement;
  inlineStyles(node, clone);
  // A canvas clones empty (a map is drawn in WebGL): it goes into the picture as an image of what
  // it shows, which the map keeps readable by drawing with a preserved buffer.
  const drawn = node.querySelectorAll("canvas");
  clone.querySelectorAll("canvas").forEach((copy, i) => {
    const source = drawn[i];
    if (!source) return;
    try {
      const image = document.createElement("img");
      image.src = source.toDataURL("image/png");
      image.setAttribute("style", copy.getAttribute("style") ?? "");
      const size = source.getBoundingClientRect();
      image.style.width = `${size.width}px`;
      image.style.height = `${size.height}px`;
      copy.replaceWith(image);
    } catch {
      /* a canvas tainted by a source without CORS: left blank rather than failing the picture */
    }
  });
  // Not the export bar, and not what the pointer was showing when the button was pressed.
  clone.querySelectorAll("[data-export-skip], .vz-readout, .maplibregl-ctrl-top-right").forEach((el) => el.remove());
  // A card has no lift in a picture, and its corners are cut by the image edge anyway.
  clone.style.boxShadow = "none";
  clone.style.margin = "0";
  const width = Math.ceil(box.width);
  const height = Math.ceil(box.height);
  const markup = new XMLSerializer().serializeToString(clone);
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}">` +
    `<foreignObject x="0" y="0" width="100%" height="100%">` +
    `<div xmlns="http://www.w3.org/1999/xhtml">${markup}</div></foreignObject></svg>`;
  const image = new Image();
  image.decoding = "sync";
  const loaded = new Promise<void>((resolve, reject) => {
    image.onload = () => resolve();
    image.onerror = () => reject(new Error("image"));
  });
  image.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
  try {
    await loaded;
  } catch {
    return null;
  }
  const scale = 2;
  const canvas = document.createElement("canvas");
  canvas.width = width * scale;
  canvas.height = height * scale;
  const context = canvas.getContext("2d");
  if (!context) return null;
  context.scale(scale, scale);
  context.drawImage(image, 0, 0);
  return new Promise((resolve) => canvas.toBlob((blob) => resolve(blob), "image/png"));
}

/** A file name from a title: its words, nothing a file system refuses. */
export function fileName(title: string | undefined, extension: string): string {
  const stem = (title ?? "")
    .replace(/[\\/:*?"<>|]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 80);
  return `${stem || "visual"}.${extension}`;
}

export function saveBlob(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export async function copyImage(blob: Blob): Promise<boolean> {
  try {
    if (typeof ClipboardItem === "undefined" || !navigator.clipboard?.write) return false;
    await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
    return true;
  } catch {
    return false;
  }
}
