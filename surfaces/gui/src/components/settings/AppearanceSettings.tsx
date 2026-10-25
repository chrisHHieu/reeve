// Settings → Appearance: the mode, the sixteen palettes in their three groups, and the
// faces and sizes of the UI, code and terminal. Every choice is per-device and applies at once;
// the stores are src/theme.ts and src/fonts.ts, which this page only reads and writes.
import { useRef } from "react";
import {
  fontOption,
  getCodeSize,
  getLigatures,
  getTermCursor,
  getTermLine,
  getTermSize,
  getTextSize,
  getThemeFonts,
  MONO_FONTS,
  SAME_AS_CODE,
  setCodeSize,
  setLigatures,
  setTermCursor,
  setTermLine,
  setTermSize,
  setTextSize,
  setThemeFonts,
  TERM_SIZES,
  terminalOptions,
  UI_FONTS,
  useFont,
  useFontPrefs,
  type FontOption,
  type FontRole,
  type Size,
  type TermCursor,
  type TermLine,
} from "../../fonts";
import { PALETTE_GROUPS, PALETTES, usePalette, useThemePref, type PaletteId } from "../../theme";
import { announceSaved, Segmented, Select, SettingsCard, SettingsRow, Switch, type SelectOption } from "./primitives";

const SIZE_OPTIONS: { value: Size; label: string }[] = [
  { value: "small", label: "Small" },
  { value: "default", label: "Default" },
  { value: "large", label: "Large" },
];

// -- palettes ----------------------------------------------------------------------------------

/** The palettes in their groups, as one radio group: arrows move through all sixteen in order. */
function PalettePicker() {
  const [palette, setPalette] = usePalette();
  const refs = useRef<Partial<Record<PaletteId, HTMLButtonElement | null>>>({});
  const ordered = PALETTE_GROUPS.flatMap((g) => PALETTES.filter((p) => p.group === g.id));

  const pick = (id: PaletteId) => {
    setPalette(id);
    announceSaved();
  };

  const move = (event: React.KeyboardEvent, id: PaletteId) => {
    const at = ordered.findIndex((p) => p.id === id);
    let next = -1;
    if (event.key === "Home") next = 0;
    else if (event.key === "End") next = ordered.length - 1;
    else if (event.key === "ArrowRight" || event.key === "ArrowDown") next = (at + 1) % ordered.length;
    else if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = (at - 1 + ordered.length) % ordered.length;
    if (next < 0) return;
    event.preventDefault();
    const target = ordered[next].id;
    pick(target);
    refs.current[target]?.focus();
  };

  return (
    <div className="settings-palette-groups" role="radiogroup" aria-label="Theme">
      {PALETTE_GROUPS.map((group) => (
        <div key={group.id} className="settings-palette-group">
          <div className="settings-palette-group-name" aria-hidden="true">
            {group.name}
          </div>
          <div className="settings-palettes">
            {PALETTES.filter((p) => p.group === group.id).map((p) => {
              const on = palette === p.id;
              return (
                <button
                  key={p.id}
                  ref={(el) => {
                    refs.current[p.id] = el;
                  }}
                  type="button"
                  role="radio"
                  aria-checked={on}
                  aria-label={p.name}
                  title={p.intent}
                  tabIndex={on ? 0 : -1}
                  data-testid={`palette-${p.id}`}
                  onClick={() => pick(p.id)}
                  onKeyDown={(e) => move(e, p.id)}
                  className={"settings-palette" + (on ? " on" : "")}
                >
                  <span aria-hidden="true" className="settings-palette-swatch">
                    {(["light", "dark"] as const).map((m) => (
                      <span key={m} style={{ background: p[m].canvas }}>
                        <span className="settings-palette-chrome" style={{ background: p[m].chrome }} />
                        <span className="settings-palette-dot" style={{ background: p[m].accent }} />
                      </span>
                    ))}
                  </span>
                  <span className="settings-palette-name">{p.name}</span>
                </button>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

function PaletteNote() {
  const [palette] = usePalette();
  return <>{PALETTES.find((p) => p.id === palette)?.intent ?? ""}</>;
}

// -- faces -------------------------------------------------------------------------------------

/** A face as a Select option, drawn in itself. */
const faceOption = (o: FontOption, hint?: string): SelectOption<string> => ({
  value: o.id,
  label: o.name,
  style: { fontFamily: o.stack },
  hint,
});

function FaceSelect({ role, label }: { role: FontRole; label: string }) {
  const [font, setFont] = useFont(role);
  const [code] = useFont("code");
  const themeFonts = useFontPrefs(getThemeFonts);
  const options: SelectOption<string>[] =
    role === "terminal"
      ? [
          { value: SAME_AS_CODE, label: "Same as code font", hint: fontOption("code", code).name },
          ...MONO_FONTS.map((o) => faceOption(o)),
        ]
      : (role === "ui" ? UI_FONTS : MONO_FONTS).map((o) =>
          // While the theme's pair is in force, the face it chose says so.
          faceOption(o, themeFonts && o.id === font ? "Theme" : undefined),
        );
  return (
    <Select
      label={label}
      testId={`font-picker-${role}`}
      value={font}
      options={options}
      optionTestId={(value) => `font-${role}-${value}`}
      onChange={(next) => {
        setFont(next);
        announceSaved();
      }}
    />
  );
}

function SizeSegment({ label, value, onChange, testId }: { label: string; value: Size; onChange: (s: Size) => void; testId: string }) {
  return (
    <Segmented
      radio
      label={label}
      value={value}
      options={SIZE_OPTIONS}
      testId={testId}
      optionTestId={(v) => `${testId}-${v}`}
      onChange={(next) => {
        onChange(next);
        announceSaved();
      }}
    />
  );
}

function FontsCard() {
  const themeFonts = useFontPrefs(getThemeFonts);
  const textSize = useFontPrefs(getTextSize);
  const codeSize = useFontPrefs(getCodeSize);
  const ligatures = useFontPrefs(getLigatures);
  return (
    <SettingsCard
      title="Fonts"
      testId="fonts-card"
      action={
        <label className="settings-inline-switch">
          <span>Use the theme's fonts</span>
          <Switch
            label="Use the theme's fonts"
            checked={themeFonts}
            testId="theme-fonts"
            onChange={(on) => {
              setThemeFonts(on);
              announceSaved();
            }}
          />
        </label>
      }
    >
      <SettingsRow title="UI font" detail="Everything except code." testId="font-row-ui">
        <div className="settings-control-pair">
          <FaceSelect role="ui" label="UI font" />
          <SizeSegment label="Text size" value={textSize} onChange={setTextSize} testId="text-size" />
        </div>
      </SettingsRow>
      <SettingsRow title="Code font" detail="Code blocks, diffs and files." testId="font-row-code">
        <div className="settings-control-pair">
          <FaceSelect role="code" label="Code font" />
          <SizeSegment label="Code size" value={codeSize} onChange={setCodeSize} testId="code-size" />
        </div>
      </SettingsRow>
      <SettingsRow title="Ligatures" detail="Draw -> => != as single glyphs, in the faces that have them.">
        <Switch
          label="Ligatures"
          checked={ligatures}
          testId="code-ligatures"
          onChange={(on) => {
            setLigatures(on);
            announceSaved();
          }}
        />
      </SettingsRow>
      <TerminalRows />
    </SettingsCard>
  );
}

function TerminalRows() {
  const size = useFontPrefs(getTermSize);
  const line = useFontPrefs(getTermLine);
  const cursor = useFontPrefs(getTermCursor);
  return (
    <>
      <SettingsRow title="Terminal font" detail="The terminal panel and command output." testId="font-row-terminal">
        <div className="settings-control-pair">
          <FaceSelect role="terminal" label="Terminal font" />
          <Select
            label="Terminal size"
            testId="term-size"
            value={String(size)}
            options={TERM_SIZES.map((n) => ({ value: String(n), label: `${n} px` }))}
            onChange={(next) => {
              setTermSize(Number(next));
              announceSaved();
            }}
          />
        </div>
      </SettingsRow>
      <SettingsRow title="Terminal lines and cursor" testId="font-row-terminal-shape">
        <div className="settings-control-pair">
          <Segmented<TermLine>
            radio
            label="Line height"
            value={line}
            testId="term-line"
            options={[
              { value: "tight", label: "Tight" },
              { value: "normal", label: "Normal" },
              { value: "roomy", label: "Roomy" },
            ]}
            onChange={(next) => {
              setTermLine(next);
              announceSaved();
            }}
          />
          <Segmented<TermCursor>
            radio
            label="Cursor"
            value={cursor}
            testId="term-cursor"
            options={[
              { value: "block", label: "Block" },
              { value: "bar", label: "Bar" },
              { value: "underline", label: "Underline" },
            ]}
            onChange={(next) => {
              setTermCursor(next);
              announceSaved();
            }}
          />
        </div>
      </SettingsRow>
    </>
  );
}

/** The page's preview, beside the choices and in view while they are made (settings redesign,
 *  2026-10-20): a small window in the palette in force, then the three faces, each line in the
 *  face and size it shows. Drawn from the live tokens, so every change shows at once. */
function AppearancePreview() {
  const [ui] = useFont("ui");
  const [code] = useFont("code");
  const term = useFontPrefs(terminalOptions);
  return (
    <aside className="appearance-preview" aria-label="Preview" data-testid="font-preview">
      <div className="appearance-preview-title">Preview</div>
      <div className="appearance-preview-window" aria-hidden="true">
        <div className="appearance-preview-rail">
          <span className="appearance-preview-mark" />
          <span className="appearance-preview-line" />
          <span className="appearance-preview-line" />
        </div>
        <div className="appearance-preview-body">
          <span className="appearance-preview-ask">Why did the build fail?</span>
          <span className="appearance-preview-answer" />
          <span className="appearance-preview-answer short" />
          <span className="appearance-preview-actions">
            <span className="appearance-preview-primary">Approve</span>
            <span className="appearance-preview-secondary">Deny</span>
          </span>
        </div>
      </div>
      <div className="settings-font-preview">
        <div data-testid="font-preview-ui" style={{ fontFamily: fontOption("ui", ui).stack, fontSize: "var(--fs-body)" }}>
          The receipt now counts 2 files changed, not 19. <span className="text-faint">0O 1lI · 1,234,567 · “Tiếng Việt có dấu”</span>
        </div>
        <div
          data-testid="font-preview-code"
          style={{
            fontFamily: fontOption("code", code).stack,
            fontSize: "calc(12px + var(--code-delta))",
            fontVariantLigatures: "var(--code-liga)" as React.CSSProperties["fontVariantLigatures"],
          }}
        >
          {"if n != 0O1lI and x >= 0x1F:  # -> => ├─┤"}
        </div>
        <div data-testid="font-preview-terminal" style={{ fontFamily: term.fontFamily, fontSize: term.fontSize, lineHeight: term.lineHeight }}>
          <span className="text-accent">D:\work\app&gt;</span> npm test <span className="text-ok">✓ 128 passed</span>
        </div>
      </div>
      <p className="appearance-preview-note">Changes apply at once, in every window.</p>
    </aside>
  );
}

/** Light, Dark or Auto as three small windows, so the choice is seen, not read. */
function ModeCards() {
  const [theme, setTheme] = useThemePref();
  const refs = useRef<Array<HTMLButtonElement | null>>([]);
  const modes = [
    { value: "light", label: "Light" },
    { value: "dark", label: "Dark" },
    { value: "auto", label: "Auto" },
  ] as const;
  const pick = (next: (typeof modes)[number]["value"]) => {
    setTheme(next);
    announceSaved();
  };
  return (
    <div className="appearance-modes" role="radiogroup" aria-label="Appearance">
      {modes.map((mode, index) => {
        const on = theme === mode.value;
        return (
          <button
            key={mode.value}
            ref={(el) => {
              refs.current[index] = el;
            }}
            type="button"
            role="radio"
            aria-checked={on}
            aria-label={mode.label}
            tabIndex={on ? 0 : -1}
            className={"appearance-mode" + (on ? " active" : "")}
            data-testid={`appearance-mode-${mode.value}`}
            onClick={() => pick(mode.value)}
            onKeyDown={(event) => {
              const delta =
                event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1 : 0;
              if (!delta) return;
              event.preventDefault();
              const next = (index + delta + modes.length) % modes.length;
              pick(modes[next].value);
              refs.current[next]?.focus();
            }}
          >
            <span className={"appearance-mode-pic is-" + mode.value} aria-hidden="true">
              <span className="appearance-mode-half light" />
              <span className="appearance-mode-half dark" />
            </span>
            <span className="appearance-mode-name">
              {mode.label}
              {mode.value === "auto" ? <span className="appearance-mode-hint"> · follows the system</span> : null}
            </span>
          </button>
        );
      })}
    </div>
  );
}

// -- the page ----------------------------------------------------------------------------------

export function AppearanceSettings() {
  return (
    // The choices on the left, the preview on the right and in view while they are made; one
    // column with the preview first where the page is narrow.
    <div className="appearance-layout">
      <div className="appearance-choices">
        <section className="settings-card-block">
          <div className="settings-card-heading-row">
            <h2 className="settings-card-heading" data-settings-title="Mode">
              Mode
            </h2>
          </div>
          <ModeCards />
        </section>
        <SettingsCard testId="theme-card">
          <SettingsRow title="Theme" detail={<PaletteNote />} wide testId="palette-card">
            <PalettePicker />
          </SettingsRow>
        </SettingsCard>
        <FontsCard />
      </div>
      <AppearancePreview />
    </div>
  );
}
