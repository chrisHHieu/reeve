// A ```mermaid fence, drawn — in a FILE preview only (owner, 2026-10-11). A chat message keeps
// the fence as code, as decided on 2026-10-03; `markdownBlocks.test.tsx` pins that.
//
// The library is loaded when the first diagram is shown, never with the app. The drawing is put
// on screen as an IMAGE (an SVG data URL), not as markup: an image cannot run a script or reach
// the page it sits in, whatever the diagram's source says.
import { useEffect, useId, useState } from "react";
import { CopyButton } from "../primitives/CopyButton";

type Drawn = { ok: true; url: string; width: number; height: number } | { ok: false; error: string };

let loading: Promise<typeof import("mermaid").default> | null = null;

/** Draw one diagram to an SVG string, in the app's current theme. */
async function draw(id: string, code: string): Promise<string> {
  loading ??= import("mermaid").then((module) => module.default);
  const mermaid = await loading;
  mermaid.initialize({
    startOnLoad: false,
    securityLevel: "strict",
    theme: document.documentElement.dataset.theme === "dark" ? "dark" : "neutral",
    // Text as SVG text: an HTML label inside an image is drawn differently by each engine.
    htmlLabels: false,
    flowchart: { htmlLabels: false },
    fontFamily: "Inter, 'Segoe UI', system-ui, sans-serif",
  });
  return (await mermaid.render(id, code)).svg;
}

export function MermaidDiagram({ code }: { code: string }) {
  const id = `mmd${useId().replace(/[^a-zA-Z0-9]/g, "")}`;
  const [drawn, setDrawn] = useState<Drawn | null>(null);
  const [source, setSource] = useState(false);

  useEffect(() => {
    let alive = true;
    setDrawn(null);
    draw(id, code)
      .then((svg) => {
        if (!alive) return;
        const size = /viewBox="[\d.\s-]*?([\d.]+)\s+([\d.]+)"/.exec(svg);
        setDrawn({
          ok: true,
          url: `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`,
          width: size ? Math.round(Number(size[1])) : 0,
          height: size ? Math.round(Number(size[2])) : 0,
        });
      })
      .catch((error: unknown) => {
        // The library leaves its own error drawing in the document; it is not ours to show.
        document.getElementById(`d${id}`)?.remove();
        if (alive) setDrawn({ ok: false, error: error instanceof Error ? error.message : String(error) });
      });
    return () => {
      alive = false;
    };
  }, [code, id]);

  const failure = drawn !== null && !drawn.ok ? drawn.error.split("\n")[0] : null;
  return (
    <div className="md-codeblock md-diagram" data-testid="mermaid-diagram">
      <div className="md-codeblock-head">
        <span className="md-codeblock-lang" data-testid="code-language">
          mermaid
        </span>
        <span className="md-codeblock-spacer" />
        {failure === null && (
          <button
            type="button"
            className="md-diagram-btn"
            aria-pressed={source}
            data-testid="mermaid-source"
            onClick={() => setSource((shown) => !shown)}
          >
            {source ? "Show diagram" : "Show source"}
          </button>
        )}
        <CopyButton text={code} iconOnly className="md-codeblock-copy" />
      </div>
      {failure !== null && (
        <div className="md-diagram-error" role="note" data-testid="mermaid-error">
          This diagram could not be drawn: {failure}
        </div>
      )}
      {source || failure !== null ? (
        <pre className="md-codeblock-pre">
          <code>{code.replace(/\n$/, "")}</code>
        </pre>
      ) : drawn === null ? (
        <div className="md-diagram-wait" role="status" aria-label="Drawing the diagram" />
      ) : (
        drawn.ok && (
          <div className="md-diagram-stage">
            <img
              src={drawn.url}
              alt="Diagram drawn from this block's mermaid source"
              width={drawn.width || undefined}
              height={drawn.height || undefined}
              data-testid="mermaid-image"
            />
          </div>
        )
      )}
    </div>
  );
}
