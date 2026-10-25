// One search engine's account in Settings ▸ Web search (Exa's, Tavily's): the key's state with Add,
// Replace and a Remove the page confirms, and a field that opens only while a key is entered. The
// key is never read back; the server says only whether one is set and where it comes from.

import { useEffect, useState } from "react";
import { openExternal } from "../../desktop";
import { FoldBody, SETTINGS_INPUT, SettingsCard, TextButton } from "./primitives";

export function EngineKeyCard({
  title,
  testPrefix,
  label,
  keyTitle,
  keyDetail,
  hasKey,
  open,
  busy,
  note,
  error,
  keysUrl,
  keysLabel,
  onOpen,
  onClose,
  onSave,
  onRemove,
}: {
  title: string;
  /** Test ids hang off it: `${testPrefix}-key`, `-save`, `-remove`, … */
  testPrefix: string;
  /** The field's accessible name ("Exa API key"). */
  label: string;
  keyTitle: string;
  keyDetail: string;
  hasKey: boolean;
  open: boolean;
  busy: boolean;
  note?: string;
  error?: string;
  keysUrl: string;
  keysLabel: string;
  onOpen: () => void;
  onClose: () => void;
  onSave: (key: string) => void;
  onRemove: () => void;
}) {
  const [key, setKey] = useState("");
  // A closed field forgets what was typed: a key left in the DOM is a key someone can read.
  useEffect(() => {
    if (!open) setKey("");
  }, [open]);
  const save = () => {
    if (key.trim()) onSave(key.trim());
  };

  return (
    <SettingsCard title={title} testId={`${testPrefix}-account`}>
      <div className="ws-key">
        <div className="ws-key-row">
          <div className="ws-key-text">
            <div className="ws-key-title">{keyTitle}</div>
            <div className="ws-key-line" data-testid={`${testPrefix}-key-status`}>
              {keyDetail}
              {note ? <span className="text-ok"> {note}</span> : null}
            </div>
          </div>
          <div className="ws-key-actions">
            {hasKey ? (
              <>
                <TextButton testId={`${testPrefix}-replace`} disabled={busy || open} onClick={onOpen}>
                  Replace
                </TextButton>
                <TextButton testId={`${testPrefix}-remove`} tone="danger" disabled={busy} onClick={onRemove}>
                  Remove
                </TextButton>
              </>
            ) : open ? null : (
              <TextButton testId={`${testPrefix}-add`} tone="accent" disabled={busy} onClick={onOpen}>
                Add key
              </TextButton>
            )}
          </div>
        </div>
        <FoldBody open={open} testId={`${testPrefix}-key-form`}>
          <div className="ws-key-form">
            <input
              className={SETTINGS_INPUT + " flex-1"}
              type="password"
              autoComplete="off"
              spellCheck={false}
              aria-label={label}
              placeholder={`Paste ${/^[AEIOU]/.test(label) ? "an" : "a"} ${label}`}
              value={key}
              disabled={busy}
              data-testid={`${testPrefix}-key`}
              onChange={(event) => setKey(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") save();
                if (event.key === "Escape") onClose();
              }}
            />
            <TextButton testId={`${testPrefix}-save`} tone="accent" disabled={busy || !key.trim()} onClick={save}>
              {busy ? "Saving…" : "Save"}
            </TextButton>
            <TextButton testId={`${testPrefix}-cancel`} disabled={busy} onClick={onClose}>
              Cancel
            </TextButton>
          </div>
          <div className="ws-key-help">
            Stored only on this computer.{" "}
            <button type="button" className="ws-link inline" onClick={() => openExternal(keysUrl)}>
              {keysLabel}
            </button>
          </div>
          {error ? (
            <div className="ws-key-error" role="alert" data-testid={`${testPrefix}-key-error`}>
              {error}
            </div>
          ) : null}
        </FoldBody>
      </div>
    </SettingsCard>
  );
}
