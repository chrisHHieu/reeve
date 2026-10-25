// What Reeve measured on a design page a call just wrote, under the call's row. The
// page's latest change in a turn leaves its card: the page itself drawn small, its name, what the
// check said ("Checked at 1440, 834 and 390 px: nothing to fix", or "…: 2 to fix" with the findings
// one click away) and Open. Its earlier changes keep only the words (`compact`, `designCards.ts`),
// so a page edited ten times is one card (owner, 2026-10-20). The model read the same findings with
// the write's result; this is the person's copy, said in words and kept quiet (no badge, no colour
// for a clean page).
import { useContext, useState } from "react";
import type { DesignCheck } from "../types";
import { Disclosure } from "../ui/Disclosure";
import { Icon } from "./Icon";
import { PagePeek } from "./rail/PagePeek";
import { attachToComposer } from "./rail/designAsks";
import { TranscriptSession } from "./session/transcriptSession";
import { openArtifact } from "./Markdown";

const fileName = (path: string) => path.replace(/\\/g, "/").split("/").pop() || path;

export function DesignCheckLine({ checks, compact = false }: { checks: DesignCheck[]; compact?: boolean }) {
  const sessionId = useContext(TranscriptSession);
  const [open, setOpen] = useState(false);
  const lines = checks.flatMap((check) => check.lines);
  const changed = checks.flatMap((check) => check.changes ?? []);
  const unasked = checks.flatMap((check) => (check.unasked ?? []).map((line) => ({ line, path: check.path ?? "the page" })));
  const issues = checks.reduce((sum, check) => sum + (check.issues || 0), 0);
  const summary = checks.length === 1 ? checks[0].summary : checks.map((check) => check.summary).join(" · ");
  // The page the write made, to open in its own tab: the way into a design when the panel is shut
  // (it opens only when the person opens it).
  const page = checks.find((check) => check.path);
  const more = lines.length > 0 || changed.length > 0;
  const toggle = more ? (
    <button
      type="button"
      className="design-check-head"
      aria-expanded={open}
      data-testid="design-check-toggle"
      onClick={() => setOpen((value) => !value)}
    >
      <span className="design-check-summary">{summary}</span>
      <span className="design-check-more">{open ? "Hide" : "Show"}</span>
    </button>
  ) : (
    <span className="design-check-summary">{summary}</span>
  );
  const picture = page?.thumb ? <img className="design-peek-thumb" src={page.thumb} alt="" /> : null;
  return (
    <div className="design-check" data-testid="design-check" data-issues={issues || undefined}>
      {page?.path && !compact ? (
        <div className="design-peek" data-testid="design-peek">
          <button
            type="button"
            className="design-peek-shot"
            data-testid="design-check-open"
            aria-label={`Open ${page.path}`}
            onClick={() => openArtifact(page.path!)}
          >
            {sessionId ? (
              <PagePeek
                sessionId={sessionId}
                path={page.path}
                title={fileName(page.path)}
                fallback={picture ?? <span className="design-peek-wait">Drawing the page…</span>}
              />
            ) : (
              picture ?? (
                <span className="design-peek-wait">
                  <Icon name="appWindow" size={22} />
                </span>
              )
            )}
          </button>
          <div className="design-peek-foot">
            <div className="design-peek-words">
              <span className="design-peek-name">{fileName(page.path)}</span>
              {toggle}
            </div>
            <button type="button" className="btn sm design-peek-open" onClick={() => openArtifact(page.path!)}>
              Open
            </button>
          </div>
        </div>
      ) : compact ? (
        // An earlier change of a page whose card is further down: what the check said, in words.
        toggle
      ) : (
        <>
          {page?.path && (
            <button
              type="button"
              className="design-check-open"
              data-testid="design-check-open"
              title={`Open ${page.path}`}
              onClick={() => openArtifact(page.path!)}
            >
              {page.thumb ? <img src={page.thumb} alt="" /> : null}
              <span>Open</span>
            </button>
          )}
          {toggle}
        </>
      )}
      <Disclosure open={open} className="fold-fade" testId="design-check-lines">
        <ul className="design-check-lines">
          {lines.map((line, index) => (
            <li key={index}>{line}</li>
          ))}
        </ul>
        {changed.length > 0 && (
          <div className="design-check-changed" data-testid="design-check-changed">
            <span className="design-check-label">This write changed</span>
            <ul className="design-check-lines">
              {changed.map((line, index) => (
                <li key={index}>{line}</li>
              ))}
            </ul>
          </div>
        )}
        {unasked.length > 0 && (
          <button
            type="button"
            className="btn sm design-check-putback"
            data-testid="design-check-putback"
            onClick={() =>
              attachToComposer(
                [],
                `Put back what changed in ${unasked[0].path} that I did not point at: ${unasked.map((u) => u.line).join("; ")}.`,
              )
            }
          >
            Ask to put back what I did not point at
          </button>
        )}
      </Disclosure>
    </div>
  );
}
