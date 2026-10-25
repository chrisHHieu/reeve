import type { ConnectorVariant } from "../../api";

/**
 * Which endpoint of a multi-endpoint vendor MCP server to sign in to.
 *
 * Postman publishes the same server three times — Essentials (41 tools, none of which
 * delete), Full (140 tools, 17 of which delete) and Code generation (23, read-only).
 * The choice must be made BEFORE the browser opens, because each endpoint is its own
 * OAuth resource: the sign-in is for that endpoint specifically, and changing it later
 * means connecting again.
 *
 * Radios rather than a dropdown: the summaries ARE the decision — one option can delete
 * a whole workspace and another cannot — and a dropdown hides them behind a click at the
 * exact moment the choice is being made.
 *
 * It lives here, in its own file, because there are TWO one-click surfaces: the card in
 * Settings ▸ Connectors and the pane in the add-connection modal. The first version of
 * this shipped in only one of them, so the modal — the button most users actually press —
 * connected straight to the default with no choice offered at all.
 */
export function VariantPicker({
  name,
  variants,
  value,
  onChange,
  disabled,
}: {
  name: string;
  variants: ConnectorVariant[];
  value: string;
  onChange: (key: string) => void;
  disabled?: boolean;
}) {
  if (!variants.length) return null;
  return (
    <fieldset className="conn-variants" data-testid="mcp-variants">
      <legend>How much access?</legend>
      {variants.map((v) => (
        <label key={v.key} className="conn-variant">
          <input
            type="radio"
            name={`variant-${name}`}
            value={v.key}
            checked={value === v.key}
            disabled={disabled}
            onChange={() => onChange(v.key)}
          />
          <span>
            <b>{v.label}</b>
            <em>{v.summary}</em>
          </span>
        </label>
      ))}
    </fieldset>
  );
}

/** The key a picker should start on: what the connection is already using, else the
 *  first option — which descriptors order so that the safest one leads. */
export function initialVariant(
  current: string | undefined,
  variants: ConnectorVariant[] | undefined,
): string {
  return current || variants?.[0]?.key || "";
}
