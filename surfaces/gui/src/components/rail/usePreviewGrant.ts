// An address at the preview door for one file, held for as long as the view that
// asked for it is on screen: a new file or a new answer to "internet?" is a new key, and the
// key no longer needed is handed back.
import { useEffect, useState } from "react";
import { previewGrant, previewRevoke, type PreviewGrant } from "../../api";

export function usePreviewGrant(
  sessionId: string,
  path: string,
  options: { internet?: boolean; single?: boolean; nonce?: number } = {},
): PreviewGrant | null {
  const { internet = false, single = false, nonce = 0 } = options;
  const [grant, setGrant] = useState<PreviewGrant | null>(null);

  useEffect(() => {
    let alive = true;
    let key: string | undefined;
    setGrant(null);
    // Through a promise, so a server that cannot answer at all is an answer too.
    Promise.resolve()
      .then(() => previewGrant(sessionId, path, { internet, single }))
      .catch((): PreviewGrant => ({ ok: false, error: "Reeve's server did not answer." }))
      .then((answer) => {
        if (!alive) {
          if (answer.key) previewRevoke(answer.key);
          return;
        }
        key = answer.key;
        setGrant(answer);
      });
    return () => {
      alive = false;
      if (key) previewRevoke(key);
    };
  }, [sessionId, path, internet, single, nonce]);

  return grant;
}
