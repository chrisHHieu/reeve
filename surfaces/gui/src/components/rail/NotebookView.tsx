// A Jupyter notebook, read: its cells in order with the outputs SAVED in the file (workspace
// panel redesign, 2026-10-11). Nothing is run here, and the head line says so, because a
// notebook that looks live and is not is worse than one that says what it is.
//
// An output's HTML is never put on the page as markup. A table in it (a pandas DataFrame) is
// read cell by cell as text and drawn as a table of ours; everything else falls back to the
// output's plain-text form.
import { Fragment, useMemo } from "react";
import { Markdown } from "../Markdown";
import { highlightLines, useGrammars } from "../primitives/highlight";

type Text = string | string[] | undefined;

interface Output {
  output_type?: string;
  name?: string;
  text?: Text;
  data?: Record<string, Text>;
  ename?: string;
  evalue?: string;
  traceback?: string[];
}

interface Cell {
  cell_type?: string;
  source?: Text;
  outputs?: Output[];
  execution_count?: number | null;
}

interface Notebook {
  cells?: Cell[];
  worksheets?: unknown;
  metadata?: { kernelspec?: { display_name?: string; language?: string }; language_info?: { name?: string } };
}

const MAX_CELLS = 300;
const MAX_OUTPUT_CHARS = 20_000;
const MAX_TABLE_ROWS = 200;

const join = (text: Text) => (Array.isArray(text) ? text.join("") : typeof text === "string" ? text : "");
// eslint-disable-next-line no-control-regex
const plain = (text: string) => text.replace(/\u001b\[[0-9;]*[A-Za-z]/g, "");

function clip(text: string): { text: string; cut: boolean } {
  return text.length > MAX_OUTPUT_CHARS ? { text: text.slice(0, MAX_OUTPUT_CHARS), cut: true } : { text, cut: false };
}

/** The first table in an output's HTML, as rows of text. Parsed inert: nothing in it runs. */
export function tableFromHtml(html: string): string[][] | null {
  if (!/<table[\s>]/i.test(html)) return null;
  const table = new DOMParser().parseFromString(html, "text/html").querySelector("table");
  if (!table) return null;
  const rows = Array.from(table.querySelectorAll("tr"))
    .slice(0, MAX_TABLE_ROWS + 1)
    .map((row) => Array.from(row.querySelectorAll("th,td")).slice(0, 50).map((cell) => (cell.textContent || "").trim()));
  return rows.length > 1 && rows.some((row) => row.length > 1) ? rows : null;
}

function Code({ text, lang }: { text: string; lang?: string }) {
  const grammars = useGrammars(lang);
  const spans = useMemo(() => highlightLines(text, lang), [text, lang, grammars]);
  return (
    <pre className="nb-src">
      <code>
        {spans
          ? spans.map((line, index) => (
              <Fragment key={index}>
                {line.map((span, at) => (
                  <span key={at} style={span.style}>
                    {span.text}
                  </span>
                ))}
                {index < spans.length - 1 ? "\n" : null}
              </Fragment>
            ))
          : text}
      </code>
    </pre>
  );
}

function OutputView({ output }: { output: Output }) {
  if (output.output_type === "error") {
    const trace = plain((output.traceback || []).join("\n")) || `${output.ename || "Error"}: ${output.evalue || ""}`;
    return <pre className="nb-out is-error">{clip(trace).text}</pre>;
  }
  if (output.output_type === "stream") {
    const shown = clip(plain(join(output.text)));
    return (
      <pre className={"nb-out" + (output.name === "stderr" ? " is-stderr" : "")}>
        {shown.text}
        {shown.cut ? "\n… the rest of this output is in the file" : ""}
      </pre>
    );
  }
  const data = output.data || {};
  for (const type of ["image/png", "image/jpeg"]) {
    const bytes = join(data[type]).replace(/\s+/g, "");
    if (bytes) return <img className="nb-img" src={`data:${type};base64,${bytes}`} alt="An image this cell produced" />;
  }
  const svg = join(data["image/svg+xml"]);
  if (svg) {
    return (
      <img
        className="nb-img"
        src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`}
        alt="A drawing this cell produced"
      />
    );
  }
  const rows = tableFromHtml(join(data["text/html"]));
  if (rows) {
    const [head, ...body] = rows;
    return (
      <div className="nb-table-wrap">
        <table className="nb-table">
          <thead>
            <tr>
              {head.map((cell, index) => (
                <th key={index}>{cell}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {body.slice(0, MAX_TABLE_ROWS).map((row, index) => (
              <tr key={index}>
                {row.map((cell, at) => (at === 0 && head.length === row.length && head[0] === "" ? <th key={at}>{cell}</th> : <td key={at}>{cell}</td>))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  const text = clip(plain(join(data["text/plain"])));
  if (text.text) return <pre className="nb-out">{text.text}</pre>;
  if (data["text/html"]) return <div className="nb-note">This output is HTML, which is not drawn here.</div>;
  return null;
}

export function NotebookView({ text }: { text: string }) {
  const parsed = useMemo((): { notebook?: Notebook; error?: string } => {
    try {
      const notebook = JSON.parse(text) as Notebook;
      if (!notebook || typeof notebook !== "object") return { error: "This file is not a notebook." };
      if (!Array.isArray(notebook.cells)) {
        return {
          error: notebook.worksheets
            ? "This notebook is in an older format (before nbformat 4). Open it in Jupyter to convert it."
            : "This file has no cells.",
        };
      }
      return { notebook };
    } catch (error) {
      return { error: `This notebook could not be read: ${String((error as Error)?.message || error)}` };
    }
  }, [text]);

  if (!parsed.notebook) {
    return (
      <div className="rail-error" data-testid="nb-unreadable">
        {parsed.error}
      </div>
    );
  }
  const { cells = [], metadata } = parsed.notebook;
  const lang = (metadata?.language_info?.name || metadata?.kernelspec?.language || "python").toLowerCase();
  const kernel = metadata?.kernelspec?.display_name;
  const shown = cells.slice(0, MAX_CELLS);
  return (
    <div className="nb" data-testid="notebook">
      <div className="fv-note nb-head">
        {cells.length} {cells.length === 1 ? "cell" : "cells"}
        {kernel ? ` · ${kernel}` : ""} · the outputs shown are the ones saved in the file. Reeve does not run cells
        here.
      </div>
      <div className="nb-cells">
        {shown.map((cell, index) => {
          const source = join(cell.source);
          if (cell.cell_type === "markdown") {
            return (
              <div className="nb-cell is-text" key={index}>
                <Markdown text={source} />
              </div>
            );
          }
          if (cell.cell_type !== "code") {
            return (
              <pre className="nb-cell nb-out" key={index}>
                {source}
              </pre>
            );
          }
          return (
            <div className="nb-cell is-code" key={index} data-testid="nb-code-cell">
              <span className="nb-count" aria-label={cell.execution_count ? `Run ${cell.execution_count}` : "Not run"}>
                [{cell.execution_count ?? " "}]
              </span>
              <div className="nb-body">
                <Code text={source} lang={lang} />
                {(cell.outputs || []).map((output, at) => (
                  <OutputView output={output} key={at} />
                ))}
              </div>
            </div>
          );
        })}
        {cells.length > shown.length && (
          <div className="nb-note">
            {cells.length - shown.length} more {cells.length - shown.length === 1 ? "cell is" : "cells are"} in the
            file.
          </div>
        )}
      </div>
    </div>
  );
}
