import { useEffect, useRef, useState } from "react";
import { announceModelsChanged, providerAuthStatus, providerSignin, providerSignout, type ProviderInfo } from "../api";
import { openExternal } from "../desktop";
import type { ProviderSetupState } from "./useProviderSetup";

// A provider paid for by a plan (auth === "oauth") signs in through the browser instead of taking a
// key. The server runs the sign-in in the background and this pane asks how it is going until it
// is signed in or has failed; the tokens never reach the app's window.

const POLL_EVERY_MS = 2000;
const POLL_TIMES = 150; // five minutes
const LINK = "text-muted underline decoration-line underline-offset-2 hover:text-ink";

export function OAuthSignIn({ info, ps, tp }: { info: ProviderInfo; ps: ProviderSetupState; tp: string }) {
  const [waiting, setWaiting] = useState(!!info.authorizing);
  const [error, setError] = useState<string | null>(info.last_error || null);
  const [pageAgain, setPageAgain] = useState<string | null>(null);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true; // StrictMode mounts twice; the cleanup must not stay in force
    return () => {
      mounted.current = false;
    };
  }, []);

  const follow = async () => {
    for (let tries = 0; tries < POLL_TIMES && mounted.current; tries++) {
      await new Promise((r) => setTimeout(r, POLL_EVERY_MS));
      const status = await providerAuthStatus(info.name).catch(() => null);
      if (!status) continue;
      if (status.authorize_url) setPageAgain(status.authorize_url);
      if (!status.signed_in && (status.authorizing || !status.last_error)) continue;
      // The sign-in ended on the server, where no save call happens to tell the rest of the app.
      if (status.signed_in) announceModelsChanged();
      if (mounted.current) {
        setWaiting(false);
        setError(status.last_error || null);
      }
      await ps.refreshProviders();
      return;
    }
    if (mounted.current) setWaiting(false);
  };

  const signIn = async () => {
    setWaiting(true);
    setError(null);
    await providerSignin(info.name).catch(() => setError("Couldn't start the sign-in."));
    void follow();
  };

  const signOut = async () => {
    await providerSignout(info.name).catch(() => {});
    await ps.refreshProviders();
  };

  if (info.signed_in) {
    return (
      <div className="mt-4">
        <div className="flex items-center gap-2.5 rounded-xl border border-okLine bg-okSoft px-3 py-2.5">
          <span className="text-[13px] text-ink min-w-0 flex-1 truncate" data-testid={`${tp}-oauth-account`}>
            ✓ Signed in{info.account ? ` as ${info.account}` : ""}
          </span>
          <button className="shrink-0 rounded-lg border border-line bg-panel px-3 py-1.5 text-[13px] text-ink hover:border-lineStrong" data-testid={`${tp}-oauth-signout`} onClick={() => void signOut()}>
            Sign out
          </button>
        </div>
        <p className="text-[12px] text-faint mt-2">Use counts against the plan's rolling window, not a bill per token. The sign-in stays on this computer.</p>
      </div>
    );
  }

  return (
    <div className="mt-4">
      <button
        className="rounded-lg border border-accent bg-accent px-4 py-2 text-[13px] font-medium text-[color:var(--canvas)] hover:brightness-105 disabled:opacity-40"
        onClick={() => void signIn()}
        disabled={waiting}
        data-testid={`${tp}-oauth-signin`}
      >
        {waiting ? "Waiting for the browser…" : info.name === "anthropic-subscription" ? "Sign in with Claude" : "Sign in with ChatGPT"}
      </button>
      {waiting && (
        <p className="text-[12px] text-faint mt-2">
          Finish signing in in the browser window.
          {pageAgain && (
            <>
              {" "}
              <button className={LINK} onClick={() => openExternal(pageAgain)}>
                Reopen the sign-in page
              </button>
            </>
          )}
        </p>
      )}
      <div className="mt-2 min-h-[19px] text-[13px]">{error && <span className="text-warnInk">{error}</span>}</div>
    </div>
  );
}
