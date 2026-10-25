// The app beside its locked design (step 3), from the approved mockup's "Duyệt hai bên"
// board: the two as whole-page pictures at one width, side by side or one over the other with a
// slider, and what differs listed beside them (`compare_design`'s findings). For each difference the
// person asks the agent to fix it (an attachment in the message box, sent by them) or keeps it as
// the app has it (recorded on the lock; later compares say it as kept). "Looks good" records that
// the app was looked at and called done.
//
// Both pictures are drawn by Reeve's own browser, so what differs is the page and not the renderer.
import { useCallback, useEffect, useRef, useState } from "react";
import { designReviewAct, reviewDesign, type DesignDifference, type DesignReviewAnswer } from "../../api";
import type { Attachment } from "../../types";
import { Icon } from "../Icon";
import { attachToComposer } from "./designAsks";

const WIDTHS: { width: number; label: string }[] = [
  { width: 1440, label: "Desktop" },
  { width: 834, label: "Tablet" },
  { width: 390, label: "Mobile" },
];

const KIND_WORDS: Record<DesignDifference["kind"], string> = { fix: "To fix", check: "To check", accepted: "Kept" };

const when = (ms?: number | null) => (ms ? new Date(ms).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }) : "");

/** What asking the agent to fix one difference puts in the message box. */
export function fixAttachment(page: string, url: string, difference: DesignDifference): Attachment {
  const name = page.split(/[\\/]/).pop() || page;
  return {
    kind: "text",
    name: `${name} · difference`,
    text: [
      `In the app at ${url}, make this match the locked design ${page}:`,
      `- ${difference.text}`,
      "Then call compare_design again.",
    ].join("\n"),
  };
}

/** The review's heading: how many differences are open, in words. */
export function reviewHeading(findings: DesignDifference[]): string {
  const fix = findings.filter((finding) => finding.kind === "fix").length;
  const check = findings.filter((finding) => finding.kind === "check").length;
  if (!fix && !check) return "The same as the design in what was measured";
  const parts = [fix ? `${fix} to fix` : "", check ? `${check} to check` : ""].filter(Boolean);
  return parts.join(", ");
}

export function DesignReview({ sessionId, path, url }: { sessionId: string; path: string; url: string }) {
  const name = path.split(/[\\/]/).pop() || path;
  const [width, setWidth] = useState(1440);
  const [mode, setMode] = useState<"side" | "over">("side");
  const [mix, setMix] = useState(50);
  const [data, setData] = useState<DesignReviewAnswer | null>(null);
  const [findings, setFindings] = useState<DesignDifference[] | null>(null);
  const [measuredAt, setMeasuredAt] = useState<number | null>(null);
  const [busy, setBusy] = useState<"compare" | "picture" | "act" | null>(null);
  const [error, setError] = useState("");
  const asked = useRef(0);

  const load = useCallback(
    (at: number, compare: boolean) => {
      const ask = ++asked.current;
      setBusy(compare ? "compare" : "picture");
      setError("");
      void reviewDesign(sessionId, { path, url, width: at, compare })
        .catch(() => ({ ok: false, error: "the server did not answer" }) as DesignReviewAnswer)
        .then((answer) => {
          if (ask !== asked.current) return;
          setBusy(null);
          if (!answer.ok) {
            setError(answer.error || "the app could not be opened");
            return;
          }
          setData(answer);
          if (compare && answer.findings) {
            setFindings(answer.findings);
            setMeasuredAt(Date.now());
          }
        });
    },
    [sessionId, path, url],
  );

  useEffect(() => {
    load(1440, true);
  }, [load]);

  const pick = (next: number) => {
    if (next === width) return;
    setWidth(next);
    load(next, false);
  };

  const keep = async (difference: DesignDifference) => {
    setBusy("act");
    const answer = await designReviewAct(sessionId, "keep", { path, id: difference.id, text: difference.text }).catch(
      () => ({ ok: false, error: "the server did not answer" }) as DesignReviewAnswer,
    );
    setBusy(null);
    if (!answer.ok) {
      setError(answer.error || "it could not be kept");
      return;
    }
    setFindings((now) => (now ?? []).map((item) => (item.id === difference.id ? { ...item, kind: "accepted" } : item)));
  };

  const done = async () => {
    setBusy("act");
    const open = (findings ?? []).filter((finding) => finding.kind === "fix").length;
    const answer = await designReviewAct(sessionId, "done", { path, url, issues: open }).catch(
      () => ({ ok: false, error: "the server did not answer" }) as DesignReviewAnswer,
    );
    setBusy(null);
    if (!answer.ok) {
      setError(answer.error || "it could not be recorded");
      return;
    }
    setData((now) => (now ? { ...now, state: answer.state } : now));
  };

  const reviewed = data?.state?.reviewed ?? null;
  const shown = findings ?? [];
  const label = `Locked design · ${name}`;
  const appLabel = `App · ${url.replace(/^https?:\/\//, "")}`;

  return (
    <div className="design-review" data-testid="design-review">
      <div className="dr-bar">
        <div className="dr-seg" role="group" aria-label="How to look">
          <button type="button" className={mode === "side" ? "on" : ""} aria-pressed={mode === "side"} onClick={() => setMode("side")}>
            Side by side
          </button>
          <button type="button" className={mode === "over" ? "on" : ""} aria-pressed={mode === "over"} onClick={() => setMode("over")}>
            Overlay
          </button>
        </div>
        <div className="dr-seg" role="group" aria-label="Width">
          {WIDTHS.map((entry) => (
            <button
              key={entry.width}
              type="button"
              className={width === entry.width ? "on" : ""}
              aria-pressed={width === entry.width}
              title={`${entry.width} px`}
              onClick={() => pick(entry.width)}
            >
              {entry.label}
            </button>
          ))}
        </div>
        {mode === "over" && (
          <label className="dr-mix">
            Design
            <input type="range" min={0} max={100} value={mix} aria-label="How much of the app shows" onChange={(event) => setMix(Number(event.target.value))} />
            App
          </label>
        )}
        <span className="dr-grow" />
        <span className="dr-when">{busy === "compare" ? "Comparing…" : measuredAt ? `Compared ${when(measuredAt)}` : ""}</span>
        <button type="button" className="dr-icon" aria-label="Compare again" title="Compare again" disabled={busy !== null} onClick={() => load(width, true)}>
          <Icon name={busy === "compare" ? "loader" : "refresh"} size={15} running={busy === "compare"} />
        </button>
      </div>
      <div className="dr-body">
        <div className="dr-stage" aria-busy={busy === "picture" || busy === "compare"}>
          {error && (
            <div className="dr-error" role="alert">
              {error}
            </div>
          )}
          {!data && !error && (
            <div className="dr-wait">
              <Icon name="loader" size={14} running /> Opening the design and the app at {width} px…
            </div>
          )}
          {data && mode === "side" && (
            <div className="dr-side" data-testid="design-review-side">
              <figure>
                <figcaption>{label}</figcaption>
                {data.design_png ? <img src={data.design_png} alt={`${name} at ${width} px`} /> : <div className="dr-none">The design could not be drawn.</div>}
              </figure>
              <figure>
                <figcaption>{appLabel}</figcaption>
                {data.app_png ? (
                  <img src={data.app_png} alt={`The app at ${width} px`} />
                ) : (
                  <div className="dr-none">The app could not be drawn{data.app_error ? `: ${data.app_error}` : ""}.</div>
                )}
              </figure>
            </div>
          )}
          {data && mode === "over" && (
            <figure className="dr-over" data-testid="design-review-over">
              <figcaption>
                {label} under, the app over it
              </figcaption>
              <div className="dr-stack">
                {data.design_png && <img src={data.design_png} alt={`${name} at ${width} px`} />}
                {data.app_png && <img className="dr-top" src={data.app_png} alt={`The app at ${width} px`} style={{ opacity: mix / 100 }} />}
              </div>
            </figure>
          )}
        </div>
        <aside className="dr-list" aria-label="What differs" data-testid="design-review-list">
          <h2>{findings ? reviewHeading(shown) : "Comparing…"}</h2>
          {data?.changed_since_lock && <p className="dr-note">The design changed after it was locked; the app is held to the locked version.</p>}
          {data && data.locked === false && <p className="dr-note">This design is not locked: the app is held to the page as it is now.</p>}
          <ul>
            {shown.map((difference) => (
              <li key={difference.id} className={`dr-item is-${difference.kind}`} data-testid="design-review-item">
                <span className="dr-kind">{KIND_WORDS[difference.kind]}</span>
                <span className="dr-text">{difference.text}</span>
                {difference.kind !== "accepted" && (
                  <span className="dr-acts">
                    <button type="button" onClick={() => attachToComposer([fixAttachment(path, url, difference)], `Fix this on ${name}.`)}>
                      Ask to fix
                    </button>
                    <button type="button" disabled={busy === "act"} onClick={() => void keep(difference)}>
                      Keep as the app has it
                    </button>
                  </span>
                )}
              </li>
            ))}
          </ul>
          <div className="dr-foot">
            <button type="button" className="dr-done" disabled={!findings || busy !== null} data-testid="design-review-done" onClick={() => void done()}>
              <Icon name="check" size={14} /> Looks good
            </button>
            <p>
              {reviewed
                ? `Reviewed ${when(reviewed.at)}${reviewed.issues ? `, with ${reviewed.issues} still to fix` : ""}.`
                : "Records that you looked at the app beside the design. It needs the design locked."}
            </p>
          </div>
        </aside>
      </div>
    </div>
  );
}
