import { useEffect, useState, type ReactNode } from "react";
import { getConnectors, setOnboarded, type Connector } from "../api";
import { ConnectorBadge } from "../connectors/ConnectorIcon";
import { ProviderCards, ProviderForm, useProviderSetup } from "../providers/ProviderSetup";
import { AddConnectionModal } from "./connectors/AddConnectionModal";

// The first run: a model, then your tools, then go. Settings ▸
// Appearance ▸ "Run setup again" plays it again.
//
// Step one is the provider gallery (the owner's design of 2026-07-18): the real marks, two to
// a row, each card showing its own state; picking one turns the same box into its key form. The
// gallery and the form are providers/ProviderSetup.tsx, which Settings ▸ Models uses too,
// so the two cannot drift apart. Step two lists a few tools to connect in one click. Every step
// keeps its heading and its footer where they are and the box at one height; only the middle
// changes.

// The tools step's rows, each named for the job it does. A detail stays on one line, Connect pill
// and all: a line that wrapped made the row jump when its state changed. Connect opens the sheet
// the Connectors page uses, so the sign-in happens on this computer.
const TOOL_ROWS = [
  { name: "slack", benefit: "Keep up with Slack", detail: "Slack — catch up, answer mentions, post updates." },
  { name: "github", benefit: "Ship code", detail: "GitHub — review PRs, watch issues, read CI." },
  { name: "notion", benefit: "Keep your notes in reach", detail: "Notion — search pages, query databases, draft docs." },
];

const NEXT = "ml-auto px-6 py-2 rounded-full bg-ink text-panel text-[13px]";
const START = "w-full flex items-start gap-3 rounded-xl2 border border-line hover:border-accent bg-panel px-4 py-3.5";

function Steps({ at }: { at: number }) {
  return (
    <div className="flex justify-center gap-2 mb-6">
      {[0, 1, 2].map((step) => (
        <span key={step} className={"w-1.5 h-1.5 rounded-full " + (step <= at ? "bg-accent" : "bg-line")} />
      ))}
    </div>
  );
}

function StartChoice({ mark, title, children, testId, onPick, className = "" }: { mark: string; title: string; children: ReactNode; testId: string; onPick: () => void; className?: string }) {
  return (
    <button className={`${START} ${className}`.trim()} onClick={onPick} data-testid={testId}>
      <span className="w-9 h-9 rounded-lg bg-accentSoft text-accent grid place-items-center text-[14px] shrink-0">{mark}</span>
      <span className="flex-1 min-w-0 text-left">
        <b className="block text-[13px]">{title}</b>
        <span className="text-[12px] text-muted">{children}</span>
      </span>
      <span className="text-faint self-center">›</span>
    </button>
  );
}

export function Onboarding({ onDone }: { onDone: (next?: "work" | "automations") => void }) {
  const [step, setStep] = useState(0);

  // -- step one: a model ---------------------------------------------------------------------
  const setup = useProviderSetup();
  const [confirmSkip, setConfirmSkip] = useState(false);
  const someProviderWorks = setup.providers.some((p) => p.configured && p.needs_key) || setup.keylessOk.size > 0;
  // A key typed into the form and not yet tested: Next tests and saves it first (a tester,
  // 2026-07-12: Test and then Continue, as two steps, read as a puzzle).
  const typedUntested = !!setup.sel && setup.dirty && setup.secretFilled;
  const testing = setup.verify.state === "testing";

  const toTools = async () => {
    if (typedUntested && !setup.credentialed) {
      setup.cancelBackTimer();
      if (!(await setup.runTestAndSave())) return;
    }
    setStep(1);
  };

  // -- step two: your tools ------------------------------------------------------------------
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [connecting, setConnecting] = useState<string | null>(null); // the connector whose sheet is open
  const reloadConnectors = () =>
    getConnectors()
      .then(setConnectors)
      .catch(() => {});
  // Read again and again while this step shows: a sign-in finishes in the system browser, and
  // more often while a sheet is open.
  useEffect(() => {
    if (step !== 1) return;
    reloadConnectors();
    const timer = setInterval(reloadConnectors, connecting ? 750 : 3000);
    return () => clearInterval(timer);
  }, [step, connecting]);

  const finish = async (next?: "work" | "automations") => {
    await setOnboarded(true).catch(() => {});
    onDone(next);
  };

  return (
    <div className="fixed inset-0 z-50 bg-ink/30 grid place-items-center" data-testid="onboarding">
      {/* One height for all three steps (the owner's call of 2026-07-12, kept since): the box
          never resizes; the gallery and the form take turns inside it. */}
      <div className="w-[600px] max-w-[92vw] h-[560px] max-h-[88vh] rounded-2xl border border-line bg-panel shadow-2xl p-8 flex flex-col">
        <Steps at={step} />

        {step === 0 && (
          <section data-testid="ob-step-model" className="flex-1 min-h-0 flex flex-col">
            <h1 className="text-[20px] font-semibold">Welcome to Reeve</h1>
            <p className="text-[13px] text-muted mt-0.5 mb-4">
              Choose where the model comes from. Reeve works with your own key, and the key and your
              data stay on this computer.
            </p>
            <div className="flex-1 min-h-0 overflow-y-auto pr-1" data-testid={setup.sel ? undefined : "ob-provider-gallery"}>
              {setup.sel ? <ProviderForm ps={setup} tp="ob" /> : <ProviderCards ps={setup} tp="ob" />}
            </div>
            <div className="flex items-center gap-3 pt-5">
              {confirmSkip ? (
                <span className="text-[13px] text-muted">
                  Nothing works without a model —{" "}
                  <button className="text-accent" onClick={() => finish()}>
                    skip anyway
                  </button>
                </span>
              ) : (
                <button className="text-[13px] text-faint hover:text-muted" onClick={() => setConfirmSkip(true)}>
                  Skip setup
                </button>
              )}
              <button className={NEXT + " disabled:opacity-40"} disabled={!(someProviderWorks || typedUntested) || testing} onClick={toTools} data-testid="ob-continue">
                {testing ? "Checking…" : "Next"}
              </button>
            </div>
            <p className="text-[11px] text-faint mt-3">Settings ▸ Models switches models on and off whenever you like.</p>
          </section>
        )}

        {step === 1 && (
          <section data-testid="ob-step-tools" className="flex-1 min-h-0 flex flex-col">
            <h1 className="text-[20px] font-semibold">Connect your everyday tools</h1>
            <p className="text-[13px] text-muted mt-0.5 mb-3">Chat can only advise. With your tools connected, Reeve does the work itself:</p>
            <div className="flex-1 min-h-0 overflow-y-auto pr-1" data-testid="ob-tool-gallery">
              {TOOL_ROWS.map(({ name, benefit, detail }) => {
                const connector = connectors.find((c) => c.name === name);
                if (!connector) return null;
                return (
                  <div key={name} className="flex items-center gap-3 py-2 border-b border-paper last:border-0" data-testid={`ob-tool-${name}`}>
                    <ConnectorBadge connector={connector} size={34} title={connector.title} />
                    <span className="min-w-0 flex-1">
                      <span className="block text-[13px] font-semibold leading-tight">{benefit}</span>
                      <span className="block text-[12px] text-muted truncate">{detail}</span>
                    </span>
                    {connector.connected ? (
                      <span className="text-[12px] text-ok font-medium shrink-0">✓ Connected</span>
                    ) : (
                      <button className="shrink-0 rounded-full border border-line px-4 py-1.5 text-[13px] font-medium hover:border-lineStrong" onClick={() => setConnecting(name)}>
                        Connect
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
            <div className="flex items-center mt-3.5">
              <button className={NEXT + " shrink-0"} onClick={() => setStep(2)} data-testid="ob-continue-tools">
                Next
              </button>
            </div>
            <p className="text-[11px] text-faint mt-3">The Connectors page has 30 more, to add or remove whenever you like. Tokens stay on this computer.</p>
            {connecting && (
              <div className="relative z-[60]">
                <AddConnectionModal c={connectors.find((c) => c.name === connecting)!} onClose={() => setConnecting(null)} onChanged={reloadConnectors} />
              </div>
            )}
          </section>
        )}

        {step === 2 && (
          <section data-testid="ob-step-done" className="flex-1 min-h-0 flex flex-col overflow-y-auto">
            <div className="text-center">
              <div className="w-12 h-12 rounded-full bg-okSoft text-ok grid place-items-center mx-auto mb-3 text-[22px]">✓</div>
              <h1 className="text-[20px] font-semibold mb-1">You're set up</h1>
              <p className="text-[13px] text-muted mb-5">Two good ways to begin:</p>
            </div>
            <StartChoice mark="◷" title="Create your first automation" testId="ob-cta-automation" onPick={() => finish("automations")}>
              A weekly digest or a morning brief: pick a template and it runs within minutes.
            </StartChoice>
            <StartChoice mark="✦" title="Start working" testId="ob-start" className="mt-2.5" onPick={() => finish("work")}>
              Open a conversation and ask: go through files, write, research, build.
            </StartChoice>
            <p className="text-[11px] text-faint text-center mt-auto pt-5">To see this again: Settings ▸ Appearance ▸ Run setup again.</p>
          </section>
        )}
      </div>
    </div>
  );
}
