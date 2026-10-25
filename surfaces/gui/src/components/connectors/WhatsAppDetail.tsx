// Settings ▸ Connectors ▸ WhatsApp (personal), connected. WhatsApp gives a linked device
// no history to ask for later, so the device keeps what it receives in a store on this computer;
// this page says how much it keeps, lets the person choose how long, and clears it without
// unlinking. The generic page said none of it, and the store grew with every chat the phone
// synced (owner, 2026-10-20).
import { useEffect, useState } from "react";
import { clearWhatsAppKept, setWhatsAppKeep, whatsAppKept, type WhatsAppKept } from "../../api";
import { ConfirmDialog } from "../../ui";
import { ConnectorTools } from "./ConnectorTools";
import { announceSaved, Segmented, SettingsCard, SettingsRow, TextButton } from "../settings/primitives";
import type { DetailProps } from "./ConnectorsSection";
import { ConnectorHead } from "./DetailParts";
import { DisconnectRow } from "./DisconnectButton";

/** How often the page reads the store again while it is open: new messages keep arriving. */
const REFRESH_MS = 15_000;

type Keep = "30" | "90" | "365" | "forever";
const KEEP_OPTIONS: { value: Keep; label: string }[] = [
  { value: "30", label: "30 days" },
  { value: "90", label: "90 days" },
  { value: "365", label: "1 year" },
  { value: "forever", label: "Forever" },
];
const toKeep = (days: number | null): Keep => (days == null ? "forever" : (String(days) as Keep));
const toDays = (keep: Keep): number | null => (keep === "forever" ? null : Number(keep));

function size(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** "1,240 messages in 38 chats · since 3 Mar 2026 · 4.2 MB" */
export function keptLine(kept: WhatsAppKept): string {
  if (kept.messages === 0) return "Nothing yet. Recent chats arrive in the minutes after linking.";
  const since = kept.oldest
    ? new Date(kept.oldest * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })
    : "";
  return [
    `${kept.messages.toLocaleString("en-US")} message${kept.messages === 1 ? "" : "s"} in ${kept.chats} chat${kept.chats === 1 ? "" : "s"}`,
    since ? `since ${since}` : "",
    size(kept.bytes),
  ]
    .filter(Boolean)
    .join(" · ");
}

export function WhatsAppDetail({ c, onChanged, onGone }: DetailProps) {
  const [kept, setKept] = useState<WhatsAppKept | null>(null);
  const [error, setError] = useState("");
  const [clearing, setClearing] = useState(false);

  useEffect(() => {
    let live = true;
    const load = () =>
      whatsAppKept()
        .then((next) => live && setKept(next))
        .catch(() => live && setError("What is kept could not be read."));
    load();
    const timer = window.setInterval(load, REFRESH_MS);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, []);

  const choose = async (keep: Keep) => {
    const before = kept;
    if (kept) setKept({ ...kept, keep_days: toDays(keep) });
    const answer = await setWhatsAppKeep(toDays(keep)).catch(() => null);
    if (!answer?.ok) {
      setKept(before);
      setError(answer?.error || "The choice could not be saved.");
      return;
    }
    setError("");
    setKept(answer);
    announceSaved();
  };

  const clear = async () => {
    const answer = await clearWhatsAppKept();
    if (!answer.ok) throw new Error(answer.error || "The messages could not be cleared.");
    setKept(answer);
    setClearing(false);
    announceSaved("Cleared");
  };

  return (
    <div className="connector-page">
      <ConnectorHead c={c} state={c.account ? `Linked · ${c.account}` : "Linked"} />

      <SettingsCard title="Kept on this computer" testId="whatsapp-kept">
        <SettingsRow
          title="Messages"
          detail={kept ? keptLine(kept) : error || "Reading…"}
          testId="whatsapp-kept-count"
        >
          {kept && kept.messages > 0 ? (
            <TextButton tone="danger" testId="whatsapp-clear" onClick={() => setClearing(true)}>
              Clear…
            </TextButton>
          ) : null}
        </SettingsRow>
        <SettingsRow
          title="Keep messages for"
          detail="Older ones are deleted here. Your phone keeps its own copy."
          testId="whatsapp-keep"
        >
          <Segmented
            radio
            label="Keep messages for"
            value={toKeep(kept ? kept.keep_days : 90)}
            options={KEEP_OPTIONS}
            onChange={(next) => void choose(next)}
            optionTestId={(value) => `whatsapp-keep-${value}`}
          />
        </SettingsRow>
        {error && kept ? (
          <p className="settings-provider-note is-error" role="alert">
            {error}
          </p>
        ) : null}
      </SettingsCard>

      <ConnectorTools c={c} onChanged={onChanged} />

      <DisconnectRow c={c} onChanged={onChanged} onGone={onGone} />

      {clearing ? (
        <ConfirmDialog
          title="Clear kept WhatsApp messages?"
          body="Every message kept on this computer is deleted, and the agent can no longer read or search them. WhatsApp stays linked and keeps new messages from now on. Your phone keeps its own copy."
          confirmLabel="Clear messages"
          busyLabel="Clearing…"
          onConfirm={clear}
          onCancel={() => setClearing(false)}
        />
      ) : null}
    </div>
  );
}
