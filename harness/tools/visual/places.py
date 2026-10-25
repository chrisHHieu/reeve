"""The two blocks about when and where: a timeline and a map.

A timeline's dates are read here, in the forms people write them (`2026-10-09`, `9/10/2026`,
`2026-10`, `Q4 2026` is not one), and written as ISO dates, so the GUI lays out what the harness
checked. A map's countries are named the way a person or a file names them (`Việt Nam`, `Vietnam`,
`VN`, `VNM`) and resolved here to ISO 3166 codes from the bundled table (`countries.json`, built by
`scripts/build_world_map.mjs`); a name that matches no country is refused with the reason. Both
read a file the way a chart does.
"""

from __future__ import annotations

import datetime
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from .data import DataError, aggregate, column_of, parse_number

MAX_TIMELINE_ITEMS = 60
MAX_REGIONS = 250
MAX_POINTS = 300

#: Names people use that the ISO table does not carry.
_ALIASES = {"hoa ky": "USA", "my": "USA", "anh": "GBR", "lao": "LAO", "england": "GBR", "korea": "KOR",
            "han": "KOR", "trieu tien": "PRK", "uae": "ARE", "a rap xe ut": "SAU", "chau phi": "", "eu": ""}


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFD", str(text).replace("đ", "d").replace("Đ", "D"))
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


@lru_cache(maxsize=1)
def _countries() -> tuple[dict[str, str], dict[str, bool]]:
    """A folded name or code -> ISO alpha-3, and which codes have an outline on the map."""
    table = json.loads((Path(__file__).with_name("countries.json")).read_text(encoding="utf-8"))
    names: dict[str, str] = {}
    drawn: dict[str, bool] = {}
    for country in table:
        drawn[country["a3"]] = bool(country.get("drawn"))
        for name in [country["a3"], country["a2"], *country.get("names", [])]:
            names.setdefault(_fold(name), country["a3"])
    for alias, code in _ALIASES.items():
        if code:
            names.setdefault(alias, code)
    return names, drawn


def country_code(name: Any) -> str | None:
    """The ISO alpha-3 code a country's name, alpha-2 or alpha-3 code stands for, or None."""
    folded = _fold(name) if isinstance(name, (str, int)) else ""
    return _countries()[0].get(folded) if folded else None


_DMY = re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$")
_YMD = re.compile(r"^(\d{4})-(\d{1,2})(?:-(\d{1,2}))?(?:[ T].*)?$")


def iso_date(value: Any) -> str | None:
    """`value` as an ISO date: `2026-10-09`, `2026-10` (its first day), `2026`, or day first as
    Vietnamese and most of the world write it (`9/10/2026`). None for anything else."""
    text = str(value or "").strip()
    try:
        if match := _YMD.match(text):
            return datetime.date(int(match[1]), int(match[2]), int(match[3] or 1)).isoformat()
        if match := _DMY.match(text):
            return datetime.date(int(match[3]), int(match[2]), int(match[1])).isoformat()
        if re.fullmatch(r"\d{4}", text):
            return f"{text}-01-01"
    except ValueError:
        return None
    return None


Source = Callable[[dict[str, Any]], tuple[str, list[str], list[dict[str, Any]]]]


class PlaceError(ValueError):
    pass


def _column(columns: list[str], wanted: Any, what: str) -> str:
    try:
        return column_of(columns, wanted, what)
    except DataError as exc:
        raise PlaceError(str(exc)) from None


def timeline(block: dict[str, Any], source: Source) -> tuple[dict[str, Any], str]:
    out: dict[str, Any] = {"type": "timeline"}
    if isinstance(block.get("title"), str) and block["title"].strip():
        out["title"] = block["title"][:200]
    if "source" in block:
        name, columns, rows = source(block)
        spec = block["source"]
        label = _column(columns, spec.get("label"), "source.label")
        start = _column(columns, spec.get("start"), "source.start")
        end = _column(columns, spec["end"], "source.end") if spec.get("end") else None
        group = _column(columns, spec["group"], "source.group") if spec.get("group") else None
        raw = [{"label": row.get(label), "start": row.get(start), "end": row.get(end) if end else None,
                "group": row.get(group) if group else None} for row in rows[:MAX_TIMELINE_ITEMS]]
        out["source"] = {"file": name, "rows": len(rows)}
    else:
        raw = block.get("items")
        if not isinstance(raw, list) or not raw:
            raise PlaceError("`items` must be a non-empty list of {label, start, end?, group?, note?, done?}")
        if len(raw) > MAX_TIMELINE_ITEMS:
            raise PlaceError(f"`items` has {len(raw)} entries; at most {MAX_TIMELINE_ITEMS}")
    items = []
    for index, entry in enumerate(raw, 1):
        if not isinstance(entry, dict) or not str(entry.get("label") or "").strip():
            raise PlaceError(f"item {index} needs a `label`")
        start = iso_date(entry.get("start"))
        if start is None:
            raise PlaceError(f"item {index} ({entry['label']!r}): `start` {entry.get('start')!r} is not a date (2026-10-09 or 9/10/2026)")
        item: dict[str, Any] = {"label": str(entry["label"])[:80], "start": start}
        if entry.get("end") not in (None, ""):
            end = iso_date(entry.get("end"))
            if end is None or end < start:
                raise PlaceError(f"item {index} ({entry['label']!r}): `end` {entry.get('end')!r} is not a date on or after its start")
            if end > start:
                item["end"] = end
        for key in ("group", "note"):
            if str(entry.get(key) or "").strip():
                item[key] = str(entry[key])[:80]
        if entry.get("done") is True:
            item["done"] = True
        items.append(item)
    out["items"] = items
    first = min(i["start"] for i in items)
    last = max(i.get("end", i["start"]) for i in items)
    return out, f"timeline {out.get('title', '')!r}: {len(items)} items from {first} to {last}"


def map_block(block: dict[str, Any], source: Source) -> tuple[dict[str, Any], str]:
    out: dict[str, Any] = {"type": "map"}
    for key, limit in (("title", 200), ("unit", 20)):
        if isinstance(block.get(key), str) and block[key].strip():
            out[key] = block[key][:limit]
    regions_in: list[Any] = []
    points_in: list[Any] = []
    if "source" in block:
        name, columns, rows = source(block)
        spec = block["source"]
        if spec.get("region"):
            region = _column(columns, spec["region"], "source.region")
            value = _column(columns, spec["value"], "source.value") if spec.get("value") else None
            how = spec.get("agg", "sum" if value else "count")
            try:
                labels, series = aggregate(rows, region, [value] if value else [], how)
            except DataError as exc:
                raise PlaceError(str(exc)) from None
            regions_in = [{"name": label, "value": series[0][i]} for i, label in enumerate(labels)]
        elif spec.get("lat") and spec.get("lon"):
            lat, lon = _column(columns, spec["lat"], "source.lat"), _column(columns, spec["lon"], "source.lon")
            label = _column(columns, spec["label"], "source.label") if spec.get("label") else None
            value = _column(columns, spec["value"], "source.value") if spec.get("value") else None
            points_in = [{"lat": row.get(lat), "lon": row.get(lon), "label": row.get(label) if label else "",
                          "value": row.get(value) if value else None} for row in rows[:MAX_POINTS]]
        else:
            raise PlaceError("a map's `source` names `region` (and `value`), or `lat` and `lon`")
        out["source"] = {"file": name, "rows": len(rows)}
    else:
        regions_in = block.get("regions") or []
        points_in = block.get("points") or []
        if not isinstance(regions_in, list) or not isinstance(points_in, list) or not (regions_in or points_in):
            raise PlaceError("a map needs `regions` [{name, value}] or `points` [{lat, lon, label, value?}]")
    if len(regions_in) > MAX_REGIONS or len(points_in) > MAX_POINTS:
        raise PlaceError(f"at most {MAX_REGIONS} regions and {MAX_POINTS} points")
    _, drawn = _countries()
    regions, unknown, small = [], [], []
    for entry in regions_in:
        named = entry.get("name", entry.get("id")) if isinstance(entry, dict) else None
        code = country_code(named)
        if code is None:
            unknown.append(str(named))
            continue
        number = parse_number(entry.get("value"))
        if number is None:
            raise PlaceError(f"region {named!r}: `value` must be a number")
        regions.append({"id": code, "name": str(named)[:60], "value": number})
        if not drawn.get(code):
            small.append(str(named))
    if unknown:
        raise PlaceError(f"no country named {', '.join(repr(u) for u in unknown[:5])}; name countries by their "
                         "name (English or Vietnamese) or ISO code, and places inside one as `points` with lat/lon")
    points = []
    for entry in points_in:
        lat = parse_number(entry.get("lat")) if isinstance(entry, dict) else None
        lon = parse_number(entry.get("lon")) if isinstance(entry, dict) else None
        if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise PlaceError(f"a point needs `lat` (-90..90) and `lon` (-180..180), got {entry!r:.80}")
        point: dict[str, Any] = {"lat": lat, "lon": lon, "label": str(entry.get("label") or "")[:60]}
        if (value := parse_number(entry.get("value"))) is not None:
            point["value"] = value
        points.append(point)
    out["regions"], out["points"] = regions, points
    shown = ", ".join(f"{r['name']}={r['value']:g}" for r in regions[:12])
    line = f"map {out.get('title', '')!r}: {len(regions)} countries" + (f" ({shown})" if shown else "")
    if points:
        line += f", {len(points)} points"
    if small:
        line += f"; {', '.join(small[:5])} too small to shade at this scale, listed under the map"
    return out, line


__all__ = ["PlaceError", "country_code", "iso_date", "map_block", "timeline"]
