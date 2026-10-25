// Labelled lines — a built-in tool's call and result read as UI, not as JSON (fields.ts).

import type { FieldRow } from "./fields";

export function FieldsBlock({ rows, testId = "fields-block" }: { rows: FieldRow[]; testId?: string }) {
  if (!rows.length) return null;
  return (
    <dl className="fields-block" data-testid={testId}>
      {rows.map((row, index) => (
        <div className="fields-row" key={index}>
          <dt className="fields-label">{row.label}</dt>
          <dd className={"fields-value" + (row.mono ? " mono" : "")} data-tone={row.tone}>
            {row.value}
          </dd>
        </div>
      ))}
    </dl>
  );
}
