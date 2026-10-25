import { useEffect, useState } from "react";
import {
  startWhatsAppPairing,
  whatsAppPairStatus,
  type Connector,
  type WhatsAppPairStatus,
} from "../../api";
import { PILL_ACCENT, PILL_LINE } from "./ui";

// Linking a personal WhatsApp number. Connecting is pairing, not a form: the
// risk is said and acknowledged first, then the phone links the device by scanning a QR
// code or by typing a code the server asked WhatsApp for. The status is polled until the
// phone has linked it; the QR arrives already drawn (a data: URI), so nothing here needs a
// QR library and the pairing string itself never reaches the page.

const INPUT =
  "w-full px-3 py-2 rounded-lg border border-line bg-paper text-[13px] text-ink outline-none focus:border-accent";

type Step = "risk" | "qr" | "phone" | "code" | "linked";

export function WhatsAppPairing({ c, onConnected }: { c: Connector; onConnected: () => void }) {
  const [step, setStep] = useState<Step>("risk");
  const [ack, setAck] = useState(false);
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [status, setStatus] = useState<WhatsAppPairStatus | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (step !== "qr" && step !== "code") return;
    let alive = true;
    const tick = async () => {
      try {
        const s = await whatsAppPairStatus();
        if (!alive) return;
        setStatus(s);
        if (s.paired) setStep("linked");
      } catch {
        /* the next tick asks again */
      }
    };
    void tick();
    const t = setInterval(tick, 1500);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [step]);

  const begin = async (mode: "qr" | "phone") => {
    setBusy(true);
    setError("");
    try {
      const res = await startWhatsAppPairing(ack, mode === "phone" ? phone : "");
      if (!res.ok) {
        setError(res.error || "WhatsApp could not start");
        return;
      }
      if (mode === "phone") {
        setCode(res.code || "");
        setStep("code");
      } else {
        setStep("qr");
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="px-5 pt-4 pb-5 text-[13px]" data-testid="whatsapp-pairing">
      {step === "risk" && (
        <>
          <div className="rounded-lg border border-danger/40 bg-danger/5 px-3 py-2.5 leading-relaxed" data-testid="whatsapp-risk">
            {c.risk_notice}
          </div>
          <label className="flex items-center gap-2 mt-3 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={ack}
              onChange={(e) => setAck(e.target.checked)}
              data-testid="whatsapp-ack"
            />
            I understand, and I am linking a number I can afford to lose.
          </label>
          <div className="flex gap-2 mt-4">
            <button
              className={PILL_ACCENT}
              disabled={!ack || busy}
              onClick={() => void begin("qr")}
              data-testid="whatsapp-qr-start"
            >
              {busy ? "Starting WhatsApp…" : "Show a QR code"}
            </button>
            <button
              className={PILL_LINE}
              disabled={!ack || busy}
              onClick={() => setStep("phone")}
              data-testid="whatsapp-phone-start"
            >
              Link with a phone number
            </button>
          </div>
        </>
      )}

      {step === "phone" && (
        <>
          <div className="text-muted mb-2">The number of the phone that has WhatsApp, with its country code.</div>
          <input
            className={INPUT}
            placeholder="+84 901 234 567"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            data-testid="whatsapp-phone"
          />
          <div className="flex gap-2 mt-3">
            <button
              className={PILL_ACCENT}
              disabled={busy || phone.replace(/\D/g, "").length < 8}
              onClick={() => void begin("phone")}
              data-testid="whatsapp-code-start"
            >
              {busy ? "Asking WhatsApp…" : "Get a code"}
            </button>
            <button className={PILL_LINE} onClick={() => setStep("risk")}>
              Back
            </button>
          </div>
        </>
      )}

      {step === "qr" && (
        <div className="flex flex-col items-center">
          {status?.qr_svg ? (
            <img src={status.qr_svg} alt="WhatsApp link code" className="w-[220px] h-[220px] rounded-md bg-white" data-testid="whatsapp-qr" />
          ) : (
            <div className="w-[220px] h-[220px] grid place-items-center text-muted">
              {status?.qr_error || "Waiting for WhatsApp…"}
            </div>
          )}
          <div className="text-muted mt-3 text-center leading-relaxed">
            On your phone: WhatsApp ▸ Settings ▸ Linked devices ▸ Link a device, then scan this code.
            It renews itself every few seconds.
          </div>
        </div>
      )}

      {step === "code" && (
        <div className="text-center">
          <div className="text-[26px] font-semibold tracking-[0.2em] my-2" data-testid="whatsapp-code">
            {code}
          </div>
          <div className="text-muted leading-relaxed">
            On your phone: WhatsApp ▸ Settings ▸ Linked devices ▸ Link a device ▸ Link with phone number
            instead, then type this code.
          </div>
        </div>
      )}

      {step === "linked" && (
        <>
          <div className="leading-relaxed" data-testid="whatsapp-linked">
            Linked{status?.number ? ` as ${status.number}` : ""}. Your recent chats are arriving
            {status?.history_chunks ? ` (${status.history_chunks} batches so far)` : ""}; the phone
            decides how far back. The device shows as “Reeve” in Linked devices.
          </div>
          <button className={PILL_ACCENT + " mt-4"} onClick={onConnected} data-testid="whatsapp-done">
            Done
          </button>
        </>
      )}

      {error && <div className="text-danger mt-3" data-testid="whatsapp-error">{error}</div>}
    </div>
  );
}
