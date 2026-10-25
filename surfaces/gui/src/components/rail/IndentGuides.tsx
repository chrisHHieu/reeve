// The thin lines under a line's indentation (owner, 2026-10-15: "like VS Code"). One per level the
// line sits in, at the column that level starts; the one for the block the reader picked is
// stronger. Drawn behind the text and holding none, so selecting and copying are unchanged.

export function IndentGuides({ level, unit, active }: { level: number; unit: number; active?: number }) {
  if (level <= 0) return null;
  return (
    <span className="ig" aria-hidden="true">
      {Array.from({ length: level }, (_, k) => (
        <span key={k} className={"ig-line" + (k === active ? " on" : "")} style={{ left: `${k * unit}ch` }} />
      ))}
    </span>
  );
}
