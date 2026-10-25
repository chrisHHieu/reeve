// A visual under the answer: the blocks of one `show_visual` call, each in its own
// quiet frame with its title and, when the harness read the numbers from a file, which file.
//
// The same component draws a finished call (the harness's checked blocks, from the result card)
// and a call still being written (the arguments so far): a block appears as soon as it can be
// drawn, fading in once, and a block whose file has not been read yet holds its place with a
// calm placeholder until the result replaces it.

import "./visual.css";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Icon } from "../Icon";
import { ChartBlock } from "./ChartBlock";
import { blockPng, blockTable, copyImage, copyTable, fileName, saveBlob } from "./exportVisual";
import { FormBlock } from "./FormBlock";
import { VisualExpanded, useExpanded } from "./expanded";
import { formatValue, localeOf, useVisualLocale, VisualLocale, type NumberLocale } from "./format";
import { VisualTable } from "./VisualTable";
import { MapBlock } from "./MapBlock";
import { TimelineBlock } from "./TimelineBlock";
import type { CompareSpec, MetricsSpec, VisualBlock } from "./visualModel";
import { WhatIfBlock } from "./WhatIfBlock";

function titleOf(block: VisualBlock): string | undefined {
  return "title" in block ? block.title : undefined;
}

/** The file a block's numbers came from, by its name (the whole path on hover), and its rows. */
function sourceOf(block: VisualBlock, locale: string | undefined): { label: string; path: string } | undefined {
  if (block.type === "chart" || block.type === "table" || block.type === "timeline" || block.type === "map") {
    if (!block.source) return undefined;
    const name = block.source.file.split(/[\\/]/).pop() || block.source.file;
    const rows = block.source.rows !== undefined ? ` · ${block.source.rows.toLocaleString(locale)} rows` : "";
    return { label: `${name}${rows}`, path: block.source.file };
  }
  return undefined;
}

/** Blocks that can be read as a table of their numbers as well as drawn. */
const TABULAR = new Set<VisualBlock["type"]>(["chart", "map", "timeline"]);

function Metrics({ spec }: { spec: MetricsSpec }) {
  const locale = useVisualLocale();
  return (
    <div className="vz-metrics">
      {spec.items.map((item, i) => (
        <div key={i} className="vz-metric">
          <span className="vz-metric-label">{item.label}</span>
          <span className="vz-metric-value">
            {typeof item.value === "number" ? formatValue(item.value, { unit: item.unit, locale }) : `${item.value}${item.unit ? ` ${item.unit}` : ""}`}
          </span>
          {(item.delta || item.note) && (
            <span className="vz-metric-note">
              {item.delta && <span className="vz-metric-delta">{item.delta}</span>}
              {item.note}
            </span>
          )}
          {item.source && (
            <span className="vz-metric-source" title={item.source}>
              from {item.source}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}

function Compare({ spec }: { spec: CompareSpec }) {
  return (
    <div className="vz-compare-scroll">
      <table className="vz-compare">
        <thead>
          <tr>
            <th />
            {spec.options.map((option, i) => (
              <th key={i} data-best={option.highlight || undefined}>
                <span className="vz-compare-name">{option.name}</span>
                {option.note && <span className="vz-compare-note">{option.note}</span>}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {spec.rows.map((row, r) => (
            <tr key={r}>
              <th scope="row">{row.label}</th>
              {spec.options.map((option, i) => (
                <td key={i} data-best={option.highlight || undefined}>
                  {row.values[i] ?? ""}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Block({ block }: { block: VisualBlock }) {
  switch (block.type) {
    case "chart":
      return <ChartBlock spec={block} />;
    case "table":
      return <VisualTable spec={block} />;
    case "metrics":
      return <Metrics spec={block} />;
    case "compare":
      return <Compare spec={block} />;
    case "what_if":
      return <WhatIfBlock spec={block} />;
    case "form":
      return <FormBlock spec={block} />;
    case "timeline":
      return <TimelineBlock spec={block} />;
    case "map":
      return <MapBlock spec={block} />;
    case "pending":
      return (
        <div className="vz-pending" aria-busy="true">
          {block.file ? `Reading ${block.file}…` : "Drawing…"}
        </div>
      );
  }
}

/** Taking one block out: its data for a sheet or a document, its picture for a report. Quiet
 *  until the block is hovered; a done copy says so in place for a moment. */
function ExportBar({ block, title, frame }: { block: VisualBlock; title?: string; frame: () => HTMLElement | null }) {
  const locale = useVisualLocale();
  const [said, setSaid] = useState<string | null>(null);
  const say = (text: string) => {
    setSaid(text);
    window.setTimeout(() => setSaid(null), 1400);
  };
  const hasData = block.type !== "form" && block.type !== "pending";
  if (block.type === "pending") return null;
  const picture = async () => {
    const node = frame();
    return node ? blockPng(node) : null;
  };
  return (
    <span className="vz-tools" data-export-skip>
      {said ? (
        <span className="vz-tools-said" role="status">
          {said}
        </span>
      ) : (
        <>
          {hasData && (
            <button
              type="button"
              title="Copy the data, to paste into a sheet or a document"
              data-testid="visual-copy"
              onClick={async () => {
                const table = blockTable(block, locale, frame());
                if (table && (await copyTable(table, title))) say("Copied");
              }}
            >
              <Icon name="copy" size={12} />
              <span>Copy</span>
            </button>
          )}
          <button
            type="button"
            title="Copy as an image"
            data-testid="visual-copy-image"
            onClick={async () => {
              const png = await picture();
              if (png && (await copyImage(png))) say("Image copied");
            }}
          >
            <Icon name="image" size={12} />
            <span>Image</span>
          </button>
          <button
            type="button"
            title="Save as PNG"
            data-testid="visual-png"
            onClick={async () => {
              const png = await picture();
              if (png) saveBlob(png, fileName(title, "png"));
            }}
          >
            <Icon name="save" size={12} />
            <span>PNG</span>
          </button>
        </>
      )}
    </span>
  );
}

/** The block larger, over the conversation: Escape, the close button or a click outside closes it. */
function Expanded({ block, locale, onClose }: { block: VisualBlock; locale: NumberLocale; onClose: () => void }) {
  const close = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    close.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return createPortal(
    <div className="vz-expanded" data-testid="visual-expanded" role="dialog" aria-label={titleOf(block) ?? "Visual"} onClick={onClose}>
      <div className="vz vz-expanded-stage" onClick={(event) => event.stopPropagation()}>
        <VisualLocale.Provider value={locale}>
          <VisualExpanded.Provider value={true}>
            <BlockFrame block={block} locale={locale} />
          </VisualExpanded.Provider>
        </VisualLocale.Provider>
        <button ref={close} type="button" className="vz-expanded-close" aria-label="Close" onClick={onClose}>
          <Icon name="x" size={16} />
        </button>
      </div>
    </div>,
    document.body,
  );
}

function BlockFrame({ block, locale }: { block: VisualBlock; locale: NumberLocale }) {
  const frame = useRef<HTMLElement>(null);
  const expanded = useExpanded();
  const [asTable, setAsTable] = useState(false);
  const [open, setOpen] = useState(false);
  const title = titleOf(block);
  const source = sourceOf(block, locale);
  const table = asTable && TABULAR.has(block.type) ? blockTable(block, locale) : null;
  return (
    <section ref={frame} className="vz-block" data-type={block.type} data-view={table ? "table" : undefined}>
      <header className="vz-head" data-bare={!title && !source ? "" : undefined}>
        {title && <h4>{title}</h4>}
        <span className="vz-head-end">
          {source && (
            <span className="vz-source" title={source.path}>
              {source.label}
            </span>
          )}
          {block.type !== "pending" && (
            <span className="vz-views" data-export-skip>
              {TABULAR.has(block.type) && (
                <button
                  type="button"
                  title={asTable ? "Show the visual" : "Show the numbers as a table"}
                  aria-pressed={asTable}
                  data-testid="visual-as-table"
                  onClick={() => setAsTable((v) => !v)}
                >
                  <Icon name={asTable ? "chart" : "table"} size={12} />
                </button>
              )}
              {!expanded && (
                <button type="button" title="Open larger" aria-label="Open larger" data-testid="visual-expand" onClick={() => setOpen(true)}>
                  <Icon name="expand" size={12} />
                </button>
              )}
            </span>
          )}
          <ExportBar block={block} title={title} frame={() => frame.current} />
        </span>
      </header>
      {table ? (
        <div className="vz-table-view">
          <VisualTable spec={{ type: "table", columns: table.columns, rows: table.rows }} />
        </div>
      ) : (
        <Block block={block} />
      )}
      {open && <Expanded block={block} locale={locale} onClose={() => setOpen(false)} />}
    </section>
  );
}

export function VisualBlocks({ blocks, streaming }: { blocks: VisualBlock[]; streaming?: boolean }) {
  // One locale for the whole visual, read from its own words (see `format.ts`).
  const locale = localeOf(blocks);
  if (blocks.length === 0) return streaming ? <div className="vz-pending vz-pending-first" aria-busy="true" /> : null;
  return (
    <VisualLocale.Provider value={locale}>
      <div className="vz" data-testid="visual" data-streaming={streaming || undefined}>
        {blocks.map((block, i) => (
          <BlockFrame key={i} block={block} locale={locale} />
        ))}
      </div>
    </VisualLocale.Provider>
  );
}
