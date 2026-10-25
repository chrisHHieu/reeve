// One model in Settings, drawn the same on a provider's page and on Settings ▸ Models: its name
// and Free or Retiring tag, its id and facts under it in the words the composer's picker uses, then the
// Default tag (or Make default) and the switch that puts it in the picker.

import type { CatalogModel } from "../api";
import { modelFacts, retiring, RETIRING_TIP } from "../modelFacts";
import { Switch } from "./settings/primitives";

export function ModelRow({
  id,
  name,
  row,
  on,
  isDefault,
  onToggle,
  onMakeDefault,
}: {
  id: string;
  name: string;
  row?: CatalogModel;
  on: boolean;
  isDefault: boolean;
  onToggle: (next: boolean) => void;
  /** Offered on a row that is not the default; absent where the default is chosen elsewhere. */
  onMakeDefault?: () => void;
}) {
  const bare = id.slice(id.indexOf(":") + 1) || id;
  const facts = modelFacts(row);
  return (
    <div className={"model-row" + (on ? "" : " off")} data-testid={`model-setting-${id}`}>
      <div className="model-row-copy">
        <div className="model-row-name">
          <span className="model-row-title" title={id}>
            {name}
          </span>
          {row?.free && <span className="mp-free">Free</span>}
          {retiring(row) && (
            <span className="mp-retiring" title={RETIRING_TIP} data-testid={`model-retiring-${id}`}>
              Retiring
            </span>
          )}
        </div>
        <div className="model-row-sub" data-testid={`model-facts-${id}`}>
          {name !== bare && <span className="model-row-id">{bare}</span>}
          {facts && <span>{facts}</span>}
        </div>
      </div>
      <div className="model-row-end">
        {isDefault ? (
          <span className="settings-tag">Default</span>
        ) : onMakeDefault ? (
          <button type="button" className="model-row-make" onClick={onMakeDefault}>
            Make default
          </button>
        ) : null}
        <Switch
          checked={on}
          disabled={isDefault}
          label={`${name} in the composer's picker`}
          testId={`model-switch-${id}`}
          onChange={onToggle}
        />
      </div>
    </div>
  );
}
