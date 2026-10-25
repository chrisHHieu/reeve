import { useState } from "react";
import { splitVisualStates } from "../visual/visualState";

// The words of a person's bubble.

// A long paste would fill the transcript (owner, 2026-07-30), so past this many characters the
// bubble shows its start and a more…/less… toggle. A typed message never reaches it, and copying
// and the model always get the whole text.
const CLAMP_AT = 1200;

function Clamped({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  if (text.length <= CLAMP_AT) return <>{text}</>;
  return (
    <>
      {open ? text : text.slice(0, CLAMP_AT).trimEnd() + "…"}
      <button type="button" onClick={() => setOpen(!open)} className="block ml-auto mt-1.5 text-[13px] font-medium opacity-75 hover:opacity-100">
        {open ? "less…" : "more…"}
      </button>
    </>
  );
}

/** What the person typed, and as chips the visuals they had moved, which the message carried to
 *  the model as lines of its text (`visual/visualState.ts`). */
export function UserText({ text }: { text: string }) {
  const { text: typed, states } = splitVisualStates(text);
  if (states.length === 0) return <Clamped text={text} />;
  return (
    <>
      {typed && <Clamped text={typed} />}
      <span className="bubble-visual-states">
        {states.map((s, i) => (
          <span key={i} className="bubble-visual-state" title={`The model read: ${s.state}`}>
            <b>{s.id}</b> {s.state.split(". It shows ")[0]}
          </span>
        ))}
      </span>
    </>
  );
}
