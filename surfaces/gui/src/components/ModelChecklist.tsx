import { useState } from "react";
import { addModel, getSettings, removeModel, setDefaultModel } from "../api";
import { modelName, useModelCatalog } from "../modelFacts";
import { ModelRow } from "./ModelRow";

// The cloud-account providers route by a family written into the model id (`bedrock:claude/…`,
// `vertex:openweight/…`). The add row offers the family as a choice so nobody has to remember the
// prefix; ids from the curated list have theirs already.
const MODEL_FAMILIES: Record<string, { value: string; label: string }[]> = {
  bedrock: [
    { value: "claude", label: "Claude family" },
    { value: "other", label: "Other models" },
  ],
  vertex: [
    { value: "gemini", label: "Gemini family" },
    { value: "claude", label: "Claude family" },
    { value: "openweight", label: "Open-weight" },
  ],
};

// One provider's models, for Onboarding and Settings ▸ Models. A model switched on is in the
// composer's picker (the curated list); Default marks the one new conversations start on, and any
// other row offers "Make default" on hover. Each row is the ModelRow Settings draws. The last row
// takes a model id typed by hand, so a model released today works without an app update.
export function ModelChecklist({
  provider,
  knownProviders,
  suggested,
  curated,
  defaultModel,
  labels,
  onChanged,
}: {
  provider: string; // decides the id's prefix; OpenAI's models have none
  knownProviders: string[]; // every provider name, to read the prefix of a curated id
  suggested: string[]; // model names the provider suggests, without a prefix
  curated: string[]; // the whole curated list, every provider, full ids
  defaultModel: string;
  labels?: Record<string, string>; // names for curated ids (full id → name); the id when absent
  onChanged: (next: { models: string[]; model: string }) => void;
}) {
  const [typed, setTyped] = useState("");
  const families = MODEL_FAMILIES[provider];
  const [family, setFamily] = useState(families?.[0]?.value || "");
  // The catalogue's name and facts for each row, when it has them; the id otherwise.
  const catalog = useModelCatalog();

  const providerOf = (id: string) => {
    const head = id.slice(0, Math.max(0, id.indexOf(":")));
    return head && knownProviders.includes(head) ? head : "openai";
  };
  const withPrefix = (model: string) => (provider === "openai" || providerOf(model) !== "openai" ? model : `${provider}:${model}`);
  const rows = [...new Set([...suggested.map(withPrefix), ...curated.filter((id) => providerOf(id) === provider)])];
  const isOn = (id: string) => curated.includes(id);
  const report = (next: { models: string[]; model: string }) => onChanged({ models: next.models, model: next.model });

  const toggle = async (id: string, on: boolean) => {
    const done = on ? await addModel(id) : await removeModel(id);
    if (done.ok) report(done);
  };
  const makeDefault = async (id: string) => {
    if (!isOn(id)) await addModel(id); // a model made default is switched on as well
    await setDefaultModel(id);
    report(await getSettings());
  };
  const add = async () => {
    const model = typed.trim();
    if (!model) return;
    // The family chosen goes into the id, unless the typed id starts with one already.
    const full = families && !families.some((f) => model.startsWith(`${f.value}/`)) ? `${family}/${model}` : model;
    const done = await addModel(withPrefix(full));
    if (done.ok) {
      setTyped("");
      report(done);
    }
  };

  return (
    <div className="mlist">
      {rows.map((id) => (
        <ModelRow
          key={id}
          id={id}
          name={modelName(id, labels, catalog[id])}
          row={catalog[id]}
          on={isOn(id)}
          isDefault={id === defaultModel}
          onToggle={(on) => void toggle(id, on)}
          onMakeDefault={() => void makeDefault(id)}
        />
      ))}
      <div className="mlist-add">
        {families && (
          <select value={family} onChange={(e) => setFamily(e.target.value)} aria-label="Model family" data-testid="mlist-family">
            {families.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        )}
        <input
          placeholder="Add another model…"
          value={typed}
          spellCheck={false}
          autoComplete="off"
          onChange={(e) => setTyped(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && add()}
        />
        <button className="btn-primary sm" onClick={add} disabled={!typed.trim()}>
          Add
        </button>
      </div>
    </div>
  );
}
