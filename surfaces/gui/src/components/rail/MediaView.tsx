// A video or an audio file, played by the webview itself (workspace panel redesign,
// 2026-10-11). The bytes come from the preview door, which answers range requests, so the file
// is streamed and can be scrubbed without being read into memory first. Nothing plays until
// the person presses play.
import { useState } from "react";
import { revealArtifact } from "../../api";
import { formatBytes } from "./fileViewers";
import { usePreviewGrant } from "./usePreviewGrant";

export function MediaView({
  sessionId,
  path,
  name,
  kind,
  size,
}: {
  sessionId: string;
  path: string;
  name: string;
  kind: "video" | "audio";
  size?: number;
}) {
  const grant = usePreviewGrant(sessionId, path, { single: true });
  // The file the webview could not play (a codec it lacks), so a new file starts clean.
  const [unplayable, setUnplayable] = useState<string | null>(null);

  if (!grant) return <div className="fv-media-wait" role="status" aria-label={`Opening ${name}`} />;
  const refusal =
    !grant.ok || !grant.url
      ? grant.error || "This file could not be opened."
      : unplayable === path
        ? `This ${kind} is in a format that cannot be played here.`
        : null;
  if (refusal !== null) {
    return (
      <div className="artifact-open-prompt" data-testid="fv-media-unplayable">
        <p>{refusal}</p>
        <button type="button" className="btn sm" onClick={() => revealArtifact(sessionId, path, "open")}>
          Open in default app
        </button>
      </div>
    );
  }
  if (kind === "audio") {
    return (
      <div className="fv-media is-audio" data-testid="fv-audio">
        <div className="fv-media-name">
          <span>{name}</span>
          {size ? <span className="fv-media-size">{formatBytes(size)}</span> : null}
        </div>
        <audio controls preload="metadata" src={grant.url} onError={() => setUnplayable(path)} />
      </div>
    );
  }
  return (
    <div className="fv-media" data-testid="fv-video">
      <video controls preload="metadata" src={grant.url} onError={() => setUnplayable(path)} />
    </div>
  );
}
