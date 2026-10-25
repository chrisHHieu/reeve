"""Generate `benchmarks/tasks-long/*/task.json` — multi-file tasks that grow the context.

The default bench (`benchmarks/tasks`) is six small tasks: none of them gets past a few
thousand tokens, so nothing a context-window policy does (prepare, prune, summarize)
ever runs in it. These tasks are built to: each needs many files read, edited and
re-verified, so a run crosses tens of thousands of tokens and a capped window has to
compact mid-task. The check is still deterministic (pytest / JSON), never a model.

Generated, not hand-written, so the corpus is large without being a wall of copy-paste
in the repo's history, and reproducible from the seed. Re-run after editing:

    .venv\\Scripts\\python.exe benchmarks\\make_long_tasks.py
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from textwrap import dedent

HERE = Path(__file__).parent
OUT = HERE / "tasks-long"
SEED = 20260924

_NOUNS = [
    "order", "invoice", "shipment", "ledger", "account", "vendor", "receipt", "parcel",
    "quote", "refund", "voucher", "payout", "tariff", "batch", "rebate", "claim",
    "coupon", "deposit", "transfer", "statement", "budget", "forecast", "audit", "levy",
]


def _filler(rng: random.Random, noun: str, count: int) -> str:
    """Plausible, self-contained helper functions: the bulk a real module carries and a
    reader has to get through. Pure and deterministic; some are exercised by tests."""
    parts = []
    for i in range(count):
        a, b = rng.randint(2, 9), rng.randint(10, 99)
        kind = i % 4
        if kind == 0:
            parts.append(dedent(f'''
                def normalize_{noun}_code_{i}(code: str) -> str:
                    """Upper-case a {noun} code, strip separators, and pad to {a + 6} chars."""
                    cleaned = "".join(ch for ch in code if ch.isalnum()).upper()
                    if len(cleaned) > {a + 6}:
                        cleaned = cleaned[: {a + 6}]
                    return cleaned.rjust({a + 6}, "0")
            '''))
        elif kind == 1:
            parts.append(dedent(f'''
                def bucket_{noun}_amounts_{i}(amounts: list[float]) -> dict[str, float]:
                    """Split {noun} amounts into small (< {b}) and large buckets."""
                    small = sum(x for x in amounts if x < {b})
                    large = sum(x for x in amounts if x >= {b})
                    return {{"small": round(small, 2), "large": round(large, 2)}}
            '''))
        elif kind == 2:
            parts.append(dedent(f'''
                def {noun}_window_{i}(values: list[int], size: int = {a}) -> list[int]:
                    """Rolling sums of the {noun} values over a window of `size`."""
                    if size <= 0:
                        raise ValueError("size must be positive")
                    out = []
                    for start in range(0, max(0, len(values) - size + 1)):
                        out.append(sum(values[start : start + size]))
                    return out
            '''))
        else:
            parts.append(dedent(f'''
                def describe_{noun}_{i}(record: dict) -> str:
                    """One-line human description of a {noun} record."""
                    name = str(record.get("name") or "unnamed")
                    status = str(record.get("status") or "draft").lower()
                    total = float(record.get("total") or 0)
                    flag = " (large)" if total >= {b * 10} else ""
                    return f"{{name}} [{{status}}] {{total:.2f}}{{flag}}"
            '''))
    return "\n".join(parts)


# -- task 1: a bug that surfaces far from where it lives ------------------------------


def bugfix_chain(rng: random.Random) -> dict:
    files: dict[str, str] = {"shop/__init__.py": ""}
    # Four chains. The test exercises the TOP module; the bug lives in the BOTTOM one,
    # two imports down, so the fix needs the chain read, not just the failing file.
    chains = [
        ("pricing", "discounts", "rounding", "round_half_up", "return math.floor(value * 100 + 0.5) / 100", "return math.floor(value * 100) / 100"),
        ("checkout", "totals", "summing", "sum_lines", "return sum(qty * price for qty, price in lines)", "return sum(qty * price for qty, price in lines[1:])"),
        ("shipping", "zones", "distance", "clamp_km", "return max(0.0, min(km, 5000.0))", "return max(0.0, min(km, 500.0))"),
        ("tax", "rates", "lookup", "rate_for", "return RATES.get(region.upper(), RATES['DEFAULT'])", "return RATES.get(region.lower(), RATES['DEFAULT'])"),
    ]
    tests = ["import pytest\n"]
    for top, mid, low, fn, good, bad in chains:
        noun_a, noun_b, noun_c = rng.sample(_NOUNS, 3)
        param = {"round_half_up": "value", "sum_lines": "lines", "clamp_km": "km", "rate_for": "region"}[fn]
        low_body = "\n".join(
            [
                f'"""Low-level helpers for {low}."""',
                "import math",
                "",
                'RATES = {"EU": 0.21, "US": 0.07, "VN": 0.10, "DEFAULT": 0.15}',
                "",
                "",
                f"def {fn}({param}):",
                f'    """The primitive the {mid} layer builds on."""',
                f"    {bad}",
                "",
            ]
        )
        files[f"shop/{low}.py"] = low_body + _filler(rng, noun_c, 14)
        files[f"shop/{mid}.py"] = dedent(f'''
            """The {mid} layer: business rules on top of `{low}`."""
            from . import {low}


            def apply_{mid}(*args):
                """Apply the {mid} rule and hand back the primitive's result."""
                return {low}.{fn}(*args)
        ''') + _filler(rng, noun_b, 14)
        files[f"shop/{top}.py"] = dedent(f'''
            """Public {top} API — what callers and tests use."""
            from . import {mid}


            def {top}_value(*args):
                """The {top} figure for the given inputs."""
                return {mid}.apply_{mid}(*args)
        ''') + _filler(rng, noun_a, 14)
        del good  # the fixed line is what the check (pytest) proves, not stored here
    tests.append(dedent('''
        from shop import checkout, pricing, shipping, tax


        def test_pricing_rounds_half_up():
            # Binary-exact inputs: 1.125 and 0.375 sit exactly on the half cent.
            assert pricing.pricing_value(1.125) == 1.13
            assert pricing.pricing_value(0.375) == 0.38


        def test_checkout_sums_every_line():
            assert checkout.checkout_value([(2, 3.0), (1, 4.0), (3, 1.5)]) == 14.5


        def test_shipping_clamps_to_the_real_maximum():
            assert shipping.shipping_value(1200.0) == 1200.0
            assert shipping.shipping_value(9000.0) == 5000.0
            assert shipping.shipping_value(-3.0) == 0.0


        def test_tax_rate_is_case_insensitive():
            assert tax.tax_value("eu") == 0.21
            assert tax.tax_value("Vn") == 0.10
            assert tax.tax_value("mars") == 0.15
    '''))
    # Filler modules the model may wander through: part of a real package.
    for i in range(8):
        noun = rng.choice(_NOUNS)
        files[f"shop/util_{i:02d}_{noun}.py"] = f'"""Utilities for {noun} handling."""\n' + _filler(rng, noun, 12)
    files["tests/test_shop.py"] = "\n".join(tests)
    files["conftest.py"] = "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent))\n"
    return {
        "id": "bugfix-chain",
        "prompt": (
            "pytest fails in this workspace. Fix the SOURCE code under shop/ (never the "
            "tests) until the whole suite passes, then run pytest to verify before you "
            "finish. Each failing test may be caused by code several imports away from "
            "the module it calls."
        ),
        "setup": files,
        "check": {"type": "pytest"},
    }


# -- task 2: read everything, report everything -----------------------------------------


def settings_audit(rng: random.Random) -> dict:
    files: dict[str, str] = {}
    expected: dict[str, object] = {}
    for i in range(24):
        noun = _NOUNS[i]
        style = i % 3
        keys = [f"{noun}_{suffix}" for suffix in rng.sample(["timeout", "retries", "limit", "region", "enabled", "batch_size", "currency", "threshold"], 3)]
        values: list[object] = []
        for key in keys:
            if key.endswith(("timeout", "threshold")):
                values.append(round(rng.uniform(0.5, 90.0), 1))
            elif key.endswith(("retries", "limit", "batch_size")):
                values.append(rng.randint(1, 500))
            elif key.endswith("enabled"):
                values.append(rng.choice([True, False]))
            else:
                values.append(rng.choice(["EU", "US", "VN", "USD", "EUR", "VND"]))
        for key, value in zip(keys, values):
            expected[key] = value
        if style == 0:
            decl = "SETTINGS = {\n" + "".join(f"    {key!r}: {value!r},\n" for key, value in zip(keys, values)) + "}\n"
        elif style == 1:
            decl = "from dataclasses import dataclass\n\n\n@dataclass\nclass Settings:\n" + "".join(
                f"    {key}: {type(value).__name__} = {value!r}\n" for key, value in zip(keys, values)
            )
        else:
            decl = "from app.registry import register\n\n" + "".join(
                f"register({key!r}, default={value!r})\n" for key, value in zip(keys, values)
            )
        body = f'"""The {noun} service."""\n' + _filler(rng, noun, 6) + "\n\n# -- settings --\n" + decl + _filler(rng, noun, 6)
        files[f"app/services/{noun}.py"] = body
    files["app/__init__.py"] = ""
    files["app/services/__init__.py"] = ""
    files["app/registry.py"] = '"""Setting registry."""\n\n_REGISTRY = {}\n\n\ndef register(name, default=None):\n    _REGISTRY[name] = default\n    return default\n'
    return {
        "id": "settings-audit",
        "prompt": (
            "Every module under app/services/ declares its configuration settings — as a "
            "SETTINGS dict, as fields of a Settings dataclass, or as register(name, "
            "default=...) calls. Write settings.json at the workspace root: one JSON object "
            "mapping EVERY setting name in app/services/ to its default value, with the "
            "value's JSON type preserved (numbers as numbers, booleans as booleans)."
        ),
        "setup": files,
        "check": {"type": "json_matches", "path": "settings.json", "expected": expected},
    }


# -- task 3: a signature change across the codebase --------------------------------------


def api_migration(rng: random.Random) -> dict:
    files: dict[str, str] = {
        "store/__init__.py": "",
        "store/records.py": dedent('''
            """Record access."""

            _DB = {i: {"id": i, "name": f"record-{i}", "total": i * 10.0} for i in range(1, 200)}


            def fetch_record(id, strict):
                """Return the record `id`; with `strict`, a missing record raises KeyError."""
                if id not in _DB:
                    if strict:
                        raise KeyError(id)
                    return None
                return dict(_DB[id])
        '''),
    }
    styles = [
        "fetch_record({rid}, True)",
        "fetch_record({rid}, False)",
        "fetch_record({rid}, strict=True)",
        "fetch_record(id={rid}, strict=False)",
        "records.fetch_record({rid}, True)",
    ]
    test_lines = ["from store.records import load_record\n", "import importlib, pathlib\n"]
    for i in range(20):
        noun = _NOUNS[i]
        rid = rng.randint(1, 199)
        call = styles[i % len(styles)].format(rid=rid)
        header = "from .records import fetch_record\nfrom . import records\n" if "records." in call else "from .records import fetch_record\n"
        files[f"store/{noun}_view.py"] = (
            f'"""Views over {noun} records."""\n' + header + dedent(f'''

            def {noun}_summary():
                """The {noun} summary line for record {rid}."""
                row = {call}
                return None if row is None else f"{{row['name']}}: {{row['total']:.2f}}"
            ''') + _filler(rng, noun, 8)
        )
        test_lines.append(dedent(f'''
            def test_{noun}_summary():
                mod = importlib.import_module("store.{noun}_view")
                assert mod.{noun}_summary() == "record-{rid}: {rid * 10.0:.2f}"
        '''))
    test_lines.append(dedent('''
        def test_the_new_signature():
            assert load_record(5)["id"] == 5
            assert load_record(record_id=7, strict=True)["name"] == "record-7"
            assert load_record(999) is None
            import pytest

            with pytest.raises(KeyError):
                load_record(999, strict=True)
            with pytest.raises(TypeError):
                load_record(5, True)  # strict is keyword-only now


        def test_the_old_name_is_gone_everywhere():
            root = pathlib.Path(__file__).resolve().parents[1] / "store"
            offenders = [p.name for p in root.glob("*.py") if "fetch_record" in p.read_text()]
            assert offenders == []
    '''))
    files["tests/test_store.py"] = "\n".join(test_lines)
    files["conftest.py"] = "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent))\n"
    return {
        "id": "api-migration",
        "prompt": (
            "Rename store.records.fetch_record(id, strict) to load_record(record_id, *, "
            "strict=False) — the id parameter renamed, strict keyword-only with a default "
            "of False — and update EVERY caller in store/ to the new name and signature so "
            "that no module mentions fetch_record any more. Do not edit the tests. Run "
            "pytest to verify before you finish."
        ),
        "setup": files,
        "check": {"type": "pytest"},
    }


def main() -> None:
    rng = random.Random(SEED)
    for task in (bugfix_chain(rng), settings_audit(rng), api_migration(rng)):
        folder = OUT / task["id"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "task.json").write_text(json.dumps(task, indent=1), encoding="utf-8")
        size = sum(len(v) for v in task["setup"].values())
        print(f"{task['id']}: {len(task['setup'])} files, {size:,} chars")


if __name__ == "__main__":
    main()
