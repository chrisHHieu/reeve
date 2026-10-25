// A design page's versions (phase 3d): one per write the check saw, newest first, each
// with a small picture, what it found and what that write changed. Two picked are drawn side by
// side. Nothing is put back from here: a write is taken back with Undo on its row, or by asking.
import { useState } from "react";
import type { DesignVersion } from "../../api";

const when = (ts?: number) => {
  if (!ts) return "";
  const seconds = Math.max(0, Date.now() / 1000 - ts);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  return new Date(ts * 1000).toLocaleDateString();
};

export function DesignVersions({ versions }: { versions: DesignVersion[] }) {
  const [picked, setPicked] = useState<number[]>([]);
  const newestFirst = [...versions].reverse();
  const toggle = (n: number) =>
    setPicked((current) => (current.includes(n) ? current.filter((x) => x !== n) : [...current.slice(-1), n]));
  const pair = picked.length === 2 ? versions.filter((v) => picked.includes(v.n)).sort((a, b) => a.n - b.n) : [];
  return (
    <div className="page-versions" data-testid="page-versions">
      {pair.length === 2 && (
        <div className="page-versions-compare" data-testid="page-versions-compare">
          {pair.map((version) => (
            <figure key={version.n}>
              {version.thumb ? <img src={version.thumb} alt={`Version ${version.n}`} /> : <div className="page-version-nothumb" />}
              <figcaption>v{version.n}</figcaption>
            </figure>
          ))}
          <div className="page-versions-said">
            <b>v{pair[0].n} → v{pair[1].n}</b>
            {pair[1].changes.length > 0 ? (
              <ul>
                {pair[1].changes.map((line, index) => (
                  <li key={index}>{line}</li>
                ))}
              </ul>
            ) : (
              <span>{pair[1].n - pair[0].n > 1 ? "What changed is told write by write below." : "No part changed that the check could see."}</span>
            )}
          </div>
        </div>
      )}
      <ol className="page-versions-list">
        {newestFirst.map((version) => (
          <li key={version.n}>
            <button
              type="button"
              className={picked.includes(version.n) ? "page-version on" : "page-version"}
              aria-pressed={picked.includes(version.n)}
              data-testid={`page-version-${version.n}`}
              onClick={() => toggle(version.n)}
            >
              {version.thumb ? <img src={version.thumb} alt="" /> : <span className="page-version-nothumb" />}
              <span className="page-version-text">
                <b>
                  v{version.n}
                  <span className="page-version-when"> · {when(version.ts)}</span>
                </b>
                <span>{version.summary}</span>
                {version.changes.length > 0 && <span className="page-version-changes">{version.changes.slice(0, 3).join("; ")}</span>}
              </span>
            </button>
          </li>
        ))}
      </ol>
      <p className="page-versions-hint">Pick two to see them side by side.</p>
    </div>
  );
}
