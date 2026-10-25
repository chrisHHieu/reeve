// The question before something that cannot be taken back (disconnect an account, remove a
// server). ui-skills baseline-ui: a destructive or irreversible action asks in a dialog, not a
// browser `confirm()` and not a bare × that acts on the first click.
//
// Focus starts on Cancel: Enter on a dialog that just appeared keeps what is there.

import { useId, useState } from "react";
import { Dialog } from "./Dialog";

export function ConfirmDialog({
  title,
  body,
  confirmLabel,
  busyLabel,
  onConfirm,
  onCancel,
}: {
  title: string;
  body: string;
  confirmLabel: string;
  /** Shown on the confirm button while `onConfirm` runs. */
  busyLabel?: string;
  onConfirm: () => Promise<void> | void;
  onCancel: () => void;
}) {
  const headingId = useId();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <Dialog onClose={busy ? () => {} : onCancel} labelledBy={headingId} testId="confirm-dialog">
      <div className="ui-confirm">
        <h2 className="ui-confirm-title" id={headingId}>
          {title}
        </h2>
        <p className="ui-confirm-body">{body}</p>
        {error ? (
          <p className="ui-confirm-error" role="alert">
            {error}
          </p>
        ) : null}
        <div className="ui-confirm-actions">
          <button type="button" className="ui-confirm-btn" data-autofocus onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button
            type="button"
            className="ui-confirm-btn danger"
            data-testid="confirm-dialog-ok"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              setError(null);
              try {
                await onConfirm();
              } catch (e) {
                setError(e instanceof Error ? e.message : "That did not work. Try again.");
                setBusy(false);
              }
            }}
          >
            {busy ? busyLabel || confirmLabel : confirmLabel}
          </button>
        </div>
      </div>
    </Dialog>
  );
}
