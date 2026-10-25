// One place for "that worked" / "that did not", for actions whose result is not on screen.
//
// The app had exactly one toast, written inline for a scheduled run, and everything else was
// silent: `undoToolCall` had its `{ok, restored, failed, error}` response thrown away by the
// caller, so a refused undo looked exactly like a successful one. A toast is the right weight
// for those moments — lighter than a dialog, more durable than a colour change, and it can
// carry the reason the action failed without taking over the transcript.
//
// Deliberately not used for things the UI already shows inline (a copy button that flips to
// "Copied", a row that turns red): two confirmations for one action is noise, not feedback.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { Icon } from "../components/Icon";

export type ToastTone = "info" | "success" | "error";

export interface ToastOptions {
  title: string;
  /** The second line: what failed, how many files, why. Keep it a fact, not a sentence. */
  description?: string;
  tone?: ToastTone;
  /** An optional way out ("Retry", "Show me"). Dismisses when pressed. */
  action?: { label: string; onClick: () => void };
  /** Override the default lifetime. Errors already outlive successes. */
  durationMs?: number;
}

interface ToastItem extends ToastOptions {
  id: number;
}

/** Successes get out of the way; failures stay long enough to be read and acted on. */
const DURATION: Record<ToastTone, number> = { info: 4000, success: 4000, error: 8000 };

const ToastContext = createContext<((options: ToastOptions) => void) | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const nextId = useRef(1);
  const toast = useCallback((options: ToastOptions) => {
    const id = nextId.current++;
    // Three is the cap: a stack taller than that stops being a notification and becomes a
    // panel. The oldest goes, because the newest is the one about what just happened.
    setItems((current) => [...current.slice(-2), { ...options, id }]);
  }, []);
  const dismiss = useCallback(
    (id: number) => setItems((current) => current.filter((item) => item.id !== id)),
    [],
  );
  return (
    <ToastContext.Provider value={toast}>
      {children}
      {createPortal(
        <div className="ui-toasts" role="region" aria-label="Notifications">
          {items.map((item) => (
            <ToastCard key={item.id} item={item} onDismiss={() => dismiss(item.id)} />
          ))}
        </div>,
        document.body,
      )}
    </ToastContext.Provider>
  );
}

/** No provider → a no-op, so a component can use it and still render standalone (tests). */
export function useToast(): (options: ToastOptions) => void {
  return useContext(ToastContext) ?? noop;
}

function noop() {}

function ToastCard({ item, onDismiss }: { item: ToastItem; onDismiss: () => void }) {
  const [paused, setPaused] = useState(false);
  useEffect(() => {
    if (paused) return;
    const timer = window.setTimeout(
      onDismiss,
      item.durationMs ?? DURATION[item.tone ?? "info"],
    );
    return () => window.clearTimeout(timer);
  }, [paused, item.id, item.durationMs, item.tone, onDismiss]);

  return (
    <div
      className="ui-toast"
      data-tone={item.tone ?? "info"}
      data-testid="toast"
      role={item.tone === "error" ? "alert" : "status"}
      // Reading is a reason to keep it on screen. The timer restarts rather than resumes —
      // tracking the remaining time would mean a clock ticking behind a toast nobody is
      // looking at, for a difference the user cannot perceive.
      onPointerEnter={() => setPaused(true)}
      onPointerLeave={() => setPaused(false)}
    >
      <span className="ui-toast-mark" aria-hidden>
        <Icon
          name={item.tone === "error" ? "warning" : item.tone === "success" ? "check" : "sparkle"}
          size={13}
        />
      </span>
      <div className="ui-toast-body">
        <div className="ui-toast-title">{item.title}</div>
        {item.description !== undefined && item.description !== "" && (
          <div className="ui-toast-description">{item.description}</div>
        )}
      </div>
      {item.action !== undefined && (
        <button
          type="button"
          className="ui-toast-action"
          onClick={() => {
            item.action?.onClick();
            onDismiss();
          }}
        >
          {item.action.label}
        </button>
      )}
      <button
        type="button"
        className="ui-toast-close"
        aria-label="Dismiss"
        onClick={onDismiss}
      >
        <Icon name="x" size={12} />
      </button>
    </div>
  );
}
