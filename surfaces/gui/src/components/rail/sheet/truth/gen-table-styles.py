"""Turn what Excel said its built-in table styles are (dump-table-styles.ps1) into the data
file the sheet view reads. Usage: gen-table-styles.py <table_styles.json> <../tableStyles.data.ts>"""
import io
import json
import sys

src, out = sys.argv[1], sys.argv[2]
d = json.load(io.open(src, encoding="utf-8-sig"))

KEYS = {"left": "L", "top": "T", "bottom": "B", "right": "R", "vertical": "V", "horizontal": "H"}


def colour(c):
    """Excel's object model counts theme colours from 1 (light 1, dark 1, light 2, dark 2,
    accents); a file counts the same list from 0."""
    if not c:
        return None
    if c.get("theme"):
        tint = round(c.get("tint") or 0, 4)
        return f"t{c['theme'] - 1}" + (f"@{tint:g}" if tint else "")
    if c.get("rgb") and c.get("index") is not None:
        return "#" + c["rgb"]
    return None


def edge(e):
    line, weight = e["line"], e["weight"]
    if line == -4119:
        kind = "d"
    elif line == 1:
        kind = {1: "h", 2: "1", -4138: "2", 4: "3"}[weight]
    elif line in (-4115, 4, 5, 13):
        kind = "-" if weight == 2 else "="
    elif line == -4118:
        kind = "h"
    else:
        raise SystemExit(f"an edge this does not know: {e}")
    return kind + (colour(e["colour"]) or "#000000")


styles = {}
for name, parts in d["styles"].items():
    spec = {}
    for part, look in parts.items():
        words = []
        if look.get("bold"):
            words.append("b")
        if look.get("italic"):
            words.append("i")
        font = colour(look.get("font"))
        # Black type is what a cell has anyway.
        if font and font != "t1":
            words.append("c:" + font)
        fill = colour(look.get("fill")) if look.get("pattern") == 1 else None
        if look.get("fill") and colour(look.get("fill")) and look.get("pattern") not in (1, None):
            raise SystemExit(f"a fill that is not solid: {name} {part} {look}")
        if fill:
            words.append("f:" + fill)
        for key, letter in KEYS.items():
            if key in look:
                words.append(letter + ":" + edge(look[key]))
        if look.get("size"):
            words.append(f"n:{look['size']}")
        if words:
            spec[part] = " ".join(words)
    styles[name] = spec


def order(name):
    family = {"Light": 0, "Medium": 1, "Dark": 2}
    for prefix, rank in family.items():
        if name.startswith("TableStyle" + prefix):
            return (rank, int(name[len("TableStyle" + prefix) :]))
    return (9, 0)


lines = [
    "// Excel's built-in table styles (Format as Table): the sixty a workbook names without",
    "// carrying, as Excel " + d["excel"] + " itself reports them through its object model (2026-10-11).",
    "// Generated; the reader of this notation is tables.ts.",
    "//",
    "// A part is a list of words: b bold, i italic, c:<colour> type, f:<colour> fill, and an edge",
    "// as <side>:<line><colour>, where the side is L T B R, or V H for the lines between cells,",
    "// and the line is 1 thin, 2 medium, 3 thick, d double, h hair, - dashed. A colour is",
    "// t<theme index>, with @<tint> when it is lightened or darkened, or #RRGGBB.",
    "export const BUILTIN_TABLE_STYLES: Record<string, Record<string, string>> = {",
]
for name in sorted(styles, key=order):
    lines.append(f"  {name}: {{")
    for part, words in styles[name].items():
        lines.append(f'    {part}: "{words}",')
    lines.append("  },")
lines.append("};")
io.open(out, "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")
print("styles", len(styles), "bytes", sum(len(line) + 1 for line in lines))
print(json.dumps(styles["TableStyleMedium2"], indent=1))
print(json.dumps(styles["TableStyleDark1"], indent=1))
