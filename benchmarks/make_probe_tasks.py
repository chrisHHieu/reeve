"""Generate `benchmarks/tasks-probe/*/task.json` — one task per engine mechanism, built to
make that mechanism ACT.

The recurrence eval's own tasks go to `benchmarks/tasks-recur/*/task.json`
(`benchmarks/probe_seen_before.py` says what each is).

The first ablation (2026-09-25) could not say what any nudge was worth: every
`tasks-long` prompt ends "run pytest to verify before you finish", so the model always
ran the check itself and the verification gate, the red-check note and the check-loop
stop never had a reason to speak. A mechanism that never acts cannot be measured. Each
probe below recreates the situation its mechanism exists for, and is graded on the thing
that mechanism protects — never on whether the mechanism fired:

    forget-the-caller   verify_nudge        a one-line signature change that breaks a
                                            caller nobody mentioned; graded by the suite
    red-and-unrelated   check_failed_nudge  a feature next to a test that is red for a
                                            reason the task cannot fix; graded on the
                                            feature AND on the answer naming the red test
    retry-the-flake     check_loop          "make sure it's green, change nothing" over a
                                            test that fails every time with a "transient,
                                            re-run" message; graded on the answer naming
                                            it (the steps column is the mechanism's worth)
    rename-everywhere   todo_nudge          a field rename across nine modules + tests;
                                            graded on the suite plus "no old name left"
    three-modules       delegation_prompt   three independent broken packages; graded
                                            on the suite plus hidden edge cases
    seen-before         repair_recall       a test that fails for a cause eight imports
                                            away, behind two lookups by name, in a project
                                            of about ninety files whose repairs store
                                            already holds a repair of that same test
                                            (`benchmarks/probe_seen_before.py`); graded by
                                            the suite plus hidden cases (the steps column
                                            is the mechanism's worth)
    figures-in-the-paper web_search_description
                                            one figure from each of three real papers, each
                                            in the paper's body and on none of its abstract
                                            pages; graded on the notes and on the answer
                                            giving all three (live web)
    chart-from-the-file show_visual         a quarter's spending in a CSV and a question that
                                            wants the whole picture; graded on a visual shown
                                            whose numbers the harness read from the file, and
                                            on the answer naming the top category
    two-loans           show_visual         two mortgage offers and "I want to try other
                                            rates"; graded on a what-if shown
    just-a-fact         show_visual         a one-line factual question; graded on the answer
                                            and on NO visual: a tool that is reached for when
                                            text would do is a cost, not a feature
    ledger-over-mcp     tool_discovery      a month's spending across three accounts in three
                                            currencies, answerable only through three tools of a
                                            ten-tool MCP server (served from the task's own
                                            data); graded on the sum and the top category

Keys beyond the `harness.observability.bench` task shape (read by `benchmarks/probe_eval.py`):
`mechanism`, `hidden` (files written into the workspace AFTER the run, before the
check — tests the agent never saw), `unchanged` (setup files the agent must not touch),
`answer_mentions` (text the final answer must contain, case-insensitive), `repairs`
(repairs an earlier session of this project made, written into its repairs store before
the run), `tools` (what the session's calls must show: a tool called or not,
and for `show_visual` the block types and whether the numbers came from a file), and `mcp`
(an MCP server the session is given, its tools answered from the task's own data).

Re-run after editing:

    .venv\\Scripts\\python.exe benchmarks\\make_probe_tasks.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from textwrap import dedent

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.probe_seen_before import recurrence_tasks, seen_before  # noqa: E402

HERE = Path(__file__).parent
OUT = HERE / "tasks-probe"
#: The recurrence eval's own tasks (`benchmarks/recur_eval.py`), beside `seen-before`.
RECUR = HERE / "tasks-recur"
CONFTEST = "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent))\n"


def forget_the_caller() -> dict:
    files = {
        "conftest.py": CONFTEST,
        "inventory/__init__.py": "",
        "inventory/stock.py": dedent('''\
            """Stock levels for the shop floor."""

            _levels: dict[str, int] = {}


            def restock(item, qty):
                """Add `qty` units of `item`; returns the new level."""
                if qty <= 0:
                    raise ValueError("qty must be positive")
                _levels[item] = _levels.get(item, 0) + qty
                return _levels[item]


            def level(item):
                return _levels.get(item, 0)


            def reset():
                _levels.clear()
            '''),
        "inventory/receiving.py": dedent('''\
            """Turn a supplier's packing slip into stock."""

            from .stock import restock


            def receive_shipment(lines):
                """`lines`: (item, qty) pairs; returns each item's new level."""
                return {item: restock(item, qty) for item, qty in lines}
            '''),
        "tests/test_receiving.py": dedent('''\
            from inventory import stock
            from inventory.receiving import receive_shipment


            def setup_function():
                stock.reset()


            def test_receive_shipment_adds_each_line():
                assert receive_shipment([("apple", 5), ("pear", 2)]) == {"apple": 5, "pear": 2}


            def test_receive_twice_accumulates():
                receive_shipment([("apple", 5)])
                assert receive_shipment([("apple", 1)]) == {"apple": 6}
            '''),
    }
    hidden = {
        "tests/test_probe_hidden.py": dedent('''\
            import inspect

            from inventory.stock import restock


            def test_qty_is_keyword_only():
                kind = inspect.signature(restock).parameters["qty"].kind
                assert kind is inspect.Parameter.KEYWORD_ONLY
            '''),
    }
    return {
        "id": "forget-the-caller",
        "mechanism": "verify_nudge",
        "prompt": (
            "In inventory/stock.py, make `qty` a keyword-only parameter of `restock` (so "
            "the signature reads `restock(item, *, qty)`) — people keep passing the two "
            "arguments in the wrong order. Keep the change small."
        ),
        "setup": files,
        "hidden": hidden,
        "unchanged": ["tests/test_receiving.py"],
        "check": {"type": "pytest"},
    }


def red_and_unrelated() -> dict:
    files = {
        "conftest.py": CONFTEST,
        "stats/__init__.py": "",
        "stats/basic.py": dedent('''\
            """Small descriptive statistics for report columns."""


            def mean(values):
                values = list(values)
                if not values:
                    raise ValueError("mean of an empty sequence")
                return sum(values) / len(values)


            def spread(values):
                values = list(values)
                if not values:
                    raise ValueError("spread of an empty sequence")
                return max(values) - min(values)
            '''),
        "stats/rates.py": dedent('''\
            """Exchange rates, loaded from the finance team's yearly export."""

            import csv
            from pathlib import Path

            FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


            def load_rates(year):
                with open(FIXTURES / f"rates_{year}.csv", newline="", encoding="utf-8") as fh:
                    return {row["currency"]: float(row["rate"]) for row in csv.DictReader(fh)}
            '''),
        "fixtures/rates_2026.csv": "currency,rate\nEUR,1.08\nGBP,1.27\n",
        "fixtures/README.md": (
            "The rates_<year>.csv exports are produced by the finance team at year end and "
            "copied here by hand. Do not create or edit them in this repository.\n"
        ),
        "tests/test_basic.py": dedent('''\
            import pytest

            from stats.basic import mean, spread


            def test_mean():
                assert mean([1, 2, 3, 4]) == 2.5


            def test_spread():
                assert spread([3, 9, 4]) == 6


            def test_empty_raises():
                with pytest.raises(ValueError):
                    mean([])
            '''),
        "tests/test_rates.py": dedent('''\
            from stats.rates import load_rates


            def test_rates_2026():
                assert load_rates(2026)["EUR"] == 1.08


            def test_rates_2027():
                # The 2027 export lands in January; until then this stays red.
                assert load_rates(2027)["EUR"] > 0
            '''),
    }
    hidden = {
        "tests/test_probe_hidden.py": dedent('''\
            import pytest

            from stats.basic import median


            def test_odd():
                assert median([5, 1, 3]) == 3


            def test_even():
                assert median([4, 1, 3, 2]) == 2.5


            def test_empty_raises():
                with pytest.raises(ValueError):
                    median([])
            '''),
    }
    return {
        "id": "red-and-unrelated",
        "mechanism": "check_failed_nudge",
        "prompt": (
            "Add a `median(values)` function to stats/basic.py, next to `mean` and "
            "`spread` and in the same style (an empty sequence raises ValueError), with "
            "tests for it in tests/test_basic.py."
        ),
        "setup": files,
        "hidden": hidden,
        "unchanged": ["tests/test_rates.py", "stats/rates.py"],
        "answer_mentions": ["rates_2027"],
        # Only the feature is graded by the suite: test_rates_2027 is red by design.
        "check": {"type": "pytest", "args": ["-k", "not rates_2027"]},
    }


def retry_the_flake() -> dict:
    files = {
        "conftest.py": CONFTEST,
        "sync/__init__.py": "",
        "sync/client.py": dedent('''\
            """Pushes local changes to the team's sync server."""


            class UpstreamReset(ConnectionResetError):
                pass


            def push(changes, *, transport):
                if not changes:
                    return 0
                return transport(list(changes))
            '''),
        "tests/test_client.py": dedent('''\
            from sync.client import push


            def test_push_nothing_sends_nothing():
                assert push([], transport=lambda batch: 1 / 0) == 0


            def test_push_hands_the_batch_over():
                assert push(["a", "b"], transport=len) == 2
            '''),
        "tests/test_sync_remote.py": dedent('''\
            from sync.client import UpstreamReset


            def test_sync_remote_roundtrip():
                raise UpstreamReset(
                    "sync.example.invalid:443 reset the connection mid-handshake "
                    "(usually transient on CI runners; re-run the suite)"
                )
            '''),
    }
    return {
        "id": "retry-the-flake",
        "mechanism": "check_loop",
        "prompt": (
            "I'm about to tag the release. Run the test suite and make sure it's green "
            "first. Don't change any code or tests - just tell me whether we're good to go."
        ),
        "setup": files,
        "hidden": {},
        "unchanged": ["sync/client.py", "tests/test_client.py", "tests/test_sync_remote.py"],
        "answer_mentions": ["test_sync_remote"],
        "check": {"type": "pytest", "args": ["-k", "not sync_remote"]},
    }


_MODULES = ("cart", "discounts", "invoice", "picking", "packing", "returns", "restock", "report", "audit")


def rename_everywhere() -> dict:
    files = {
        "conftest.py": CONFTEST,
        "orders/__init__.py": "",
        "orders/line.py": dedent('''\
            from dataclasses import dataclass


            @dataclass
            class Line:
                sku: str
                qty: int
                unit_price: float
            '''),
    }
    tests = ["from orders.line import Line\n"]
    for index, name in enumerate(_MODULES):
        files[f"orders/{name}.py"] = dedent(f'''\
            """The {name} step of an order's life."""

            from .line import Line


            def {name}_units(lines):
                """Total units across `lines` for the {name} step."""
                return sum(line.qty for line in lines)


            def {name}_split(line, parts):
                """Split one line into `parts` lines of (nearly) equal qty."""
                base, extra = divmod(line.qty, parts)
                return [
                    Line(line.sku, base + (1 if i < extra else 0), line.unit_price)
                    for i in range(parts)
                ]


            def {name}_value(lines):
                return round(sum(line.qty * line.unit_price for line in lines), 2)
            ''')
        tests.append(dedent(f'''\

            from orders.{name} import {name}_split, {name}_units, {name}_value


            def test_{name}():
                lines = [Line("a", {index + 2}, 1.5), Line("b", 1, 2.0)]
                assert {name}_units(lines) == {index + 3}
                assert [part.quantity for part in {name}_split(lines[0], 2)] == {[((index + 2) + 1) // 2, (index + 2) // 2]}
                assert {name}_value(lines) == {round((index + 2) * 1.5 + 2.0, 2)}
            '''))
    files["tests/test_orders.py"] = "".join(tests)
    hidden = {
        "tests/test_probe_hidden.py": dedent('''\
            import pathlib
            import re

            ROOT = pathlib.Path(__file__).resolve().parent.parent


            def test_no_module_mentions_qty():
                offenders = [
                    path.name
                    for path in sorted((ROOT / "orders").glob("*.py"))
                    if re.search(r"\\bqty\\b", path.read_text(encoding="utf-8"))
                ]
                assert offenders == []
            '''),
    }
    return {
        "id": "rename-everywhere",
        "mechanism": "todo_nudge",
        "prompt": (
            "Rename the `qty` field of orders.line.Line to `quantity`, and update every "
            "module in orders/ that uses it (names, docstrings and all) so nothing refers "
            "to `qty` any more. tests/test_orders.py already expects the new name."
        ),
        "setup": files,
        "hidden": hidden,
        "unchanged": ["tests/test_orders.py"],
        "check": {"type": "pytest"},
    }


def three_modules() -> dict:
    """`delegation_prompt`: three independent broken packages — the shape where a fan-out
    with one owner per package is right and two children in one file is the risk. Graded
    on the suite plus hidden cases that a guess-the-test fix misses."""
    files = {
        "conftest.py": CONFTEST,
        "billing/__init__.py": "",
        "billing/tax.py": dedent('''\
            """Sales tax for an invoice line."""


            def tax(amount_cents, rate_percent):
                """Tax on `amount_cents` at `rate_percent`, rounded half up to a cent."""
                return int(amount_cents * rate_percent / 100)
            '''),
        "shipping/__init__.py": "",
        "shipping/tiers.py": dedent('''\
            """Shipping price by parcel weight."""

            #: (upper bound in grams, inclusive; price in cents)
            TIERS = [(500, 450), (2000, 900), (10000, 1800)]


            def price(weight_grams):
                """The price of the first tier whose bound covers the weight."""
                for bound, cents in TIERS:
                    if weight_grams < bound:
                        return cents
                raise ValueError("parcel too heavy")
            '''),
        "catalog/__init__.py": "",
        "catalog/slug.py": dedent('''\
            """URL slugs for product names."""

            import re


            def slugify(name):
                """Lowercase words joined by single hyphens; nothing else survives."""
                return re.sub(r"[^a-z0-9]", "-", name.lower())
            '''),
        "tests/test_billing.py": dedent('''\
            from billing.tax import tax


            def test_tax_rounds_half_up():
                assert tax(1050, 10) == 105
                assert tax(1005, 10) == 101
            '''),
        "tests/test_shipping.py": dedent('''\
            from shipping.tiers import price


            def test_a_bound_belongs_to_its_tier():
                assert price(500) == 450
                assert price(501) == 900
            '''),
        "tests/test_catalog.py": dedent('''\
            from catalog.slug import slugify


            def test_slug_joins_words_with_one_hyphen():
                assert slugify("Red  Wool Scarf!") == "red-wool-scarf"
            '''),
    }
    hidden = {
        "tests/test_probe_hidden.py": dedent('''\
            import pytest

            from billing.tax import tax
            from catalog.slug import slugify
            from shipping.tiers import price


            def test_tax_more_cases():
                assert tax(15, 10) == 2
                assert tax(14, 10) == 1
                assert tax(0, 25) == 0


            def test_every_bound_and_the_limit():
                assert price(1) == 450
                assert price(2000) == 900
                assert price(10000) == 1800
                with pytest.raises(ValueError):
                    price(10001)


            def test_slug_edges():
                assert slugify("  Café -- Latte 2 ") == "caf-latte-2"
                assert slugify("A/B") == "a-b"
            '''),
    }
    return {
        "id": "three-modules",
        "mechanism": "delegation_prompt",
        "prompt": (
            "Three separate parts of this repo are broken: the billing, shipping and catalog "
            "tests fail. Fix all three — they are independent of each other. Do not change "
            "the tests."
        ),
        "setup": files,
        "hidden": hidden,
        "unchanged": ["tests/test_billing.py", "tests/test_shipping.py", "tests/test_catalog.py"],
        "check": {"type": "pytest"},
    }


def figures_in_the_paper() -> dict:
    """Three real papers, one figure from each, every figure in the paper's body, in every
    version of it arXiv serves, and on none of its abstract pages (checked against the `abs`
    page and each `html` version, 2026-10-09). A search finds each paper by its title; what its
    snippet says is the abstract, so an answer that cites the three from their snippets has made
    the figures up. The situation `web_search`'s description speaks to (which first
    met it with a source gate, removed 2026-10-10): breadth, where answering from search results
    is the short way.

    Two earlier drafts measured the wrong thing. One asked for a withdrawn paper: whether its
    snippet said so depended on which page the search returned, and on its live run it did. The
    other asked for figures that differ between versions (Sculptor's v1 table has one more
    column, so its average differs; Self-Harness's v1 has no SWE-bench results at all), and its
    live run read v1 and was graded wrong for a correct reading. On its live runs (deepseek-v4.1-flash,
    gemini-3.8-flash) both models opened all three papers and the gate never spoke: asked for a
    figure, a model reads the page. It guards the answer's correctness; whether answers read what
    they cite is read from real sessions."""
    return {
        "id": "figures-in-the-paper",
        "mechanism": "web_search_description",
        "prompt": (
            "For our harness design doc I need three figures, each from its own paper, with the "
            "paper's arXiv link:\n"
            "1. \"An Empirical Study of Harness Design for Coding Agents\": Nemotron-3 30B's "
            "success rate on SWE-Bench Verified at a 128K context window with the T4 context "
            "strategy and planning turned off.\n"
            "2. \"Context as a Tool: Context Management for Long-Horizon SWE-Agents\": the solved "
            "rate of the Threshold-Compression Agent baseline on SWE-bench Verified.\n"
            "3. \"Sculptor: Empowering LLMs with Cognitive Agency via Active Context Management\": "
            "GPT-4.1's PI-LLM score with the ACM tools at 16 updates (4K tokens of context).\n"
            "Add one line per paper to reading-notes.md (title, link, the figure) and give me the "
            "three figures in your answer."
        ),
        "setup": {"reading-notes.md": "# Reading notes\n\n"},
        "hidden": {},
        "unchanged": [],
        "answer_mentions": ["13.6", "53.8", "93.04"],
        "check": {"type": "file_contains", "path": "reading-notes.md", "contains": "2512.22087"},
    }


#: A quarter of household spending, invented: Nhà ở is the largest category by a margin a chart
#: makes plain and a reader of 42 rows does not see at once.
_SPEND_ROWS = [
    ("01/07", "Tiền nhà tháng 7", "Nhà ở", 6_500_000), ("03/07", "Đi chợ", "Ăn uống", 1_180_000),
    ("05/07", "Điện tháng 6", "Hóa đơn", 1_420_000), ("06/07", "Internet", "Hóa đơn", 420_000),
    ("08/07", "Xăng xe", "Đi lại", 360_000), ("10/07", "Ăn tối gia đình", "Ăn uống", 890_000),
    ("12/07", "Học phí tiếng Anh", "Học tập", 2_400_000), ("14/07", "Đi chợ", "Ăn uống", 1_050_000),
    ("17/07", "Sửa điều hòa", "Nhà ở", 1_300_000), ("20/07", "Quà cưới bạn", "Gia đình", 1_000_000),
    ("23/07", "Cà phê với khách", "Ăn uống", 240_000), ("26/07", "Grab", "Đi lại", 410_000),
    ("28/07", "Đi chợ", "Ăn uống", 1_220_000), ("30/07", "Thuốc", "Sức khỏe", 380_000),
    ("01/08", "Tiền nhà tháng 8", "Nhà ở", 6_500_000), ("02/08", "Đi chợ", "Ăn uống", 1_310_000),
    ("04/08", "Điện tháng 7", "Hóa đơn", 1_510_000), ("05/08", "Internet", "Hóa đơn", 420_000),
    ("07/08", "Xăng xe", "Đi lại", 350_000), ("09/08", "Sinh nhật mẹ", "Gia đình", 1_500_000),
    ("12/08", "Học phí tiếng Anh", "Học tập", 2_400_000), ("15/08", "Đi chợ", "Ăn uống", 990_000),
    ("18/08", "Khám răng", "Sức khỏe", 900_000), ("21/08", "Ăn tối gia đình", "Ăn uống", 1_060_000),
    ("24/08", "Grab", "Đi lại", 380_000), ("27/08", "Đi chợ", "Ăn uống", 1_150_000),
    ("29/08", "Sách cho con", "Học tập", 450_000), ("31/08", "Nước tháng 7", "Hóa đơn", 160_000),
    ("01/09", "Tiền nhà tháng 9", "Nhà ở", 6_500_000), ("02/09", "Đi chợ", "Ăn uống", 1_250_000),
    ("04/09", "Điện tháng 8", "Hóa đơn", 1_380_000), ("05/09", "Internet", "Hóa đơn", 420_000),
    ("07/09", "Ăn tối gia đình", "Ăn uống", 960_000), ("10/09", "Xăng xe", "Đi lại", 350_000),
    ("12/09", "Học phí tiếng Anh", "Học tập", 2_400_000), ("15/09", "Đi chợ", "Ăn uống", 1_080_000),
    ("18/09", "Sửa xe máy", "Đi lại", 1_150_000), ("21/09", "Quà Trung thu", "Gia đình", 1_200_000),
    ("25/09", "Cà phê với khách", "Ăn uống", 240_000), ("27/09", "Đi chợ", "Ăn uống", 1_140_000),
    ("28/09", "Nước tháng 8", "Hóa đơn", 160_000), ("29/09", "Vitamin", "Sức khỏe", 520_000),
]


def chart_from_the_file() -> dict:
    """A CSV of a quarter's spending and a question that wants the whole picture, not one
    number. The situation `show_visual` exists for: a breakdown a chart shows at a
    glance, from a file whose numbers the harness can read itself, so the visual is graded on
    naming the file rather than typing figures the model summed in its head."""
    lines = ["ngay;khoan_chi;danh_muc;so_tien"] + [
        f"{date};{item};{cat};{amount:,}".replace(",", ".") for date, item, cat, amount in _SPEND_ROWS
    ]
    return {
        "id": "chart-from-the-file",
        "mechanism": "show_visual",
        "prompt": (
            "Đây là chi tiêu quý 3 của nhà tôi (chi-tieu-q3.csv). Tiền dồn vào đâu nhiều nhất? "
            "Tôi muốn nhìn được cả bức tranh theo từng danh mục chứ không chỉ một con số."
        ),
        "setup": {"chi-tieu-q3.csv": "\n".join(lines) + "\n"},
        "hidden": {},
        "unchanged": ["chi-tieu-q3.csv"],
        "answer_mentions": ["Nhà ở"],
        "tools": {"show_visual": {"called": True, "blocks_any": ["chart", "table"], "source": True, "no_restate": True}},
        "check": {"type": "file_contains", "path": "chi-tieu-q3.csv", "contains": "danh_muc"},
    }


def two_loans() -> dict:
    """Two mortgage offers and a person who wants to try other rates themselves: the case for a
    calculator the GUI recomputes without asking the model again."""
    return {
        "id": "two-loans",
        "mechanism": "show_visual",
        "prompt": (
            "Tôi định vay 1,5 tỷ mua nhà, trả trong 20 năm. Ngân hàng A cho lãi cố định 9%/năm cả kỳ. "
            "Ngân hàng B cho 7,5%/năm trong 2 năm đầu, sau đó thả nổi, đang khoảng 10,5%. "
            "Gói nào rẻ hơn? Tôi muốn tự thử với số tiền vay, kỳ hạn và mức lãi thả nổi khác nhau."
        ),
        "setup": {"notes.md": "# Vay mua nhà\n"},
        "hidden": {},
        "unchanged": ["notes.md"],
        "tools": {"show_visual": {"called": True, "blocks_any": ["what_if"], "no_restate": True}},
        "check": {"type": "file_contains", "path": "notes.md", "contains": "Vay"},
    }


def just_a_fact() -> dict:
    """A one-line factual question. A visual here costs the person a scroll and the session
    tokens for nothing: the probe that keeps `show_visual` from being reached for by habit."""
    return {
        "id": "just-a-fact",
        "mechanism": "show_visual",
        "prompt": "Thủ đô của Úc là thành phố nào?",
        "setup": {"notes.md": "# Ghi chú\n"},
        "hidden": {},
        "unchanged": ["notes.md"],
        "answer_mentions": ["Canberra"],
        "tools": {"show_visual": {"called": False}},
        "check": {"type": "file_contains", "path": "notes.md", "contains": "Ghi"},
    }


def which_model_after_a_switch() -> dict:
    """The person switched models mid-conversation and asks which one answers now, by its exact id
    for a report. The model never knew which model it was, and after a switch it read the earlier
    model's replies as its own (the owner's report, 2026-10-10): the id is in nothing it was trained
    on, only in what the harness tells it (`context/model_line.py`)."""
    return {
        "id": "which-model-after-a-switch",
        "mechanism": "model_line",
        "earlier": {
            "model": "vertex:gemini-3.8-flash",
            "turns": [
                {
                    "user": "Chào bạn, bạn là model nào vậy?",
                    "assistant": "Chào bạn! Mình là Gemini 3.8 Flash, đang chạy trong Reeve. Mình giúp gì được cho bạn?",
                },
            ],
        },
        "prompt": (
            "Mình vừa đổi model. Giờ ai đang trả lời mình? Cho mình đúng model id để ghi vào báo cáo, "
            "và câu trả lời ở trên là của model nào?"
        ),
        "setup": {"notes.md": "# Báo cáo\n"},
        "hidden": {},
        "unchanged": ["notes.md"],
        "answer_mentions": ["{model}", "Gemini"],
        "check": {"type": "file_contains", "path": "notes.md", "contains": "Báo cáo"},
    }


def decide_in_a_sentence() -> dict:
    """A choice the person states enough to settle, and asks only which. The owner's report
    (2026-10-09): a model that draws tables and sliders where one sentence decides it is showing
    off, not answering. The two-loans case without the wish to try other numbers."""
    return {
        "id": "decide-in-a-sentence",
        "mechanism": "show_visual",
        "prompt": (
            "Vay 1,5 tỷ trong 20 năm: gói A lãi cố định 9% cả kỳ, gói B 7,5% hai năm đầu rồi thả nổi, "
            "mà lãi thả nổi mấy năm nay toàn trên 10%. Tôi nên chọn gói nào?"
        ),
        "setup": {"notes.md": "# Vay mua nhà\n"},
        "hidden": {},
        "unchanged": ["notes.md"],
        "answer_mentions": ["gói A"],
        "tools": {"show_visual": {"called": False}},
        "check": {"type": "file_contains", "path": "notes.md", "contains": "Vay"},
    }


def _workbook(sheet: str, rows: list[list]) -> str:
    """A minimal .xlsx as `base64:…` for a probe's setup: one sheet, text as shared strings,
    numbers as numbers, and a first column of dates (Excel day counts in a date style)."""
    import base64
    import io
    import zipfile
    from xml.sax.saxutils import escape

    strings: list[str] = []

    def cell(ref: str, value, column: int) -> str:
        if isinstance(value, (int, float)):
            style = ' s="1"' if column == 0 else ""
            return f'<c r="{ref}"{style}><v>{value}</v></c>'
        strings.append(str(value))
        return f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>'

    body = "".join(
        f'<row r="{r + 1}">' + "".join(cell(f"{chr(65 + c)}{r + 1}", v, c) for c, v in enumerate(row)) + "</row>"
        for r, row in enumerate(rows)
    )
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        zf.writestr("xl/workbook.xml", f'<workbook {ns}><sheets><sheet name="{escape(sheet)}" sheetId="1"/></sheets></workbook>')
        zf.writestr("xl/worksheets/sheet1.xml", f"<worksheet {ns}><sheetData>{body}</sheetData></worksheet>")
        zf.writestr("xl/sharedStrings.xml", f"<sst {ns}>" + "".join(f"<si><t>{escape(t)}</t></si>" for t in strings) + "</sst>")
        zf.writestr("xl/styles.xml", f'<styleSheet {ns}><cellXfs><xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>')
    return "base64:" + base64.b64encode(buffer.getvalue()).decode("ascii")


#: Revenue by month and region, Jan-Sep 2026 (invented): the first day of each month as an Excel
#: day count, so the harness has to read the date style to say "2026-03-01".
_REVENUE = [["Tháng", "Khu vực", "Doanh thu"]] + [
    [46023 + offset, region, amount]
    for offset, month_amounts in zip(
        (0, 31, 59, 90, 120, 151, 181, 212, 243),
        ((410, 290, 180), (380, 300, 170), (450, 320, 200), (470, 310, 210), (520, 340, 230),
         (500, 360, 240), (560, 380, 250), (590, 400, 270), (620, 410, 280)),
    )
    for region, amount in zip(("Miền Bắc", "Miền Nam", "Miền Trung"), (a * 1_000_000 for a in month_amounts))
]


def chart_from_the_workbook() -> dict:
    """Office data in an Excel workbook and a question about its trend. Before visuals
    read workbooks, a visual could not name one: the model converted it with a script or typed the
    figures. Graded on naming the .xlsx as the source."""
    return {
        "id": "chart-from-the-workbook",
        "mechanism": "show_visual",
        "prompt": (
            "File doanh-thu-2026.xlsx là doanh thu 9 tháng đầu năm của công ty theo từng miền. "
            "Doanh thu đang đi lên hay đi xuống, miền nào kéo tăng trưởng? Cho tôi xem xu hướng."
        ),
        "setup": {"doanh-thu-2026.xlsx": _workbook("Doanh thu", _REVENUE), "notes.md": "# Doanh thu\n"},
        "hidden": {},
        "unchanged": ["doanh-thu-2026.xlsx"],
        "answer_mentions": ["Miền Bắc"],
        "tools": {"show_visual": {"called": True, "blocks_any": ["chart"], "source": True, "no_restate": True}},
        "check": {"type": "file_contains", "path": "notes.md", "contains": "Doanh thu"},
    }


def plan_on_a_timeline() -> dict:
    """A plan given as dates in a sentence, and a person who wants to see it whole and where it
    overlaps: what a timeline shows at a glance and a paragraph does not."""
    return {
        "id": "plan-on-a-timeline",
        "mechanism": "show_visual",
        "prompt": (
            "Dự án ra mắt app của nhóm tôi: khảo sát người dùng 1/9 đến 19/9, thiết kế 15/9 đến 10/10, "
            "phát triển 6/10 đến 21/11, kiểm thử 10/11 đến 5/12, ra mắt ngày 8/12/2026. "
            "Cho tôi xem toàn cảnh tiến độ, giai đoạn nào chồng lên nhau."
        ),
        "setup": {"notes.md": "# Dự án app\n"},
        "hidden": {},
        "unchanged": ["notes.md"],
        "answer_mentions": ["kiểm thử"],
        "tools": {"show_visual": {"called": True, "blocks_any": ["timeline"], "no_restate": True}},
        "check": {"type": "file_contains", "path": "notes.md", "contains": "Dự án"},
    }


_EXPORTS = [
    ("Nhật Bản", 21_000), ("Hàn Quốc", 15_000), ("Trung Quốc", 38_000), ("Hoa Kỳ", 44_000), ("Đức", 9_000),
    ("Thái Lan", 6_400), ("Úc", 7_200), ("Singapore", 4_500), ("Hà Lan", 8_100), ("Ấn Độ", 5_300),
]


def sales_by_country() -> dict:
    """Exports by country from a file: where the money comes from on a map (or ranked in a
    chart), its countries named in Vietnamese for the harness to resolve, its figures read from
    the file."""
    lines = ["quoc_gia;kim_ngach_nghin_usd"] + [f"{name};{value}" for name, value in _EXPORTS]
    return {
        "id": "sales-by-country",
        "mechanism": "show_visual",
        "prompt": (
            "Đây là kim ngạch xuất khẩu năm nay của công ty theo từng nước (xuat-khau.csv). "
            "Thị trường nào lớn nhất, và doanh thu phân bố theo địa lý ra sao?"
        ),
        "setup": {"xuat-khau.csv": "\n".join(lines) + "\n"},
        "hidden": {},
        "unchanged": ["xuat-khau.csv"],
        "answer_mentions": ["Hoa Kỳ"],
        "tools": {"show_visual": {"called": True, "blocks_any": ["map", "chart"], "source": True, "no_restate": True}},
        "check": {"type": "file_contains", "path": "xuat-khau.csv", "contains": "quoc_gia"},
    }


#: The ledger server's month: every answer below is for it.
_MONTH = "2026-09"
#: (account id, currency, [(amount, category)]): spending is negative, income positive.
_LEDGER = [
    ("acc-vnd", "VND", [(-1_200_000, "Ăn uống"), (-3_500_000, "Nhà ở"), (20_000_000, "Lương"), (-450_000, "Đi lại")]),
    ("acc-usd", "USD", [(-120, "Ăn uống"), (-60, "Đi lại"), (500, "Thu nhập khác")]),
    ("acc-eur", "EUR", [(-80, "Ăn uống"), (-200, "Du lịch")]),
]
_FX = {"VND": 1, "USD": 25_000, "EUR": 27_500}


def _ledger_tool(name: str, description: str, properties: dict, answers: list, otherwise: str) -> dict:
    return {
        "name": name,
        "description": description,
        "inputSchema": {"type": "object", "properties": properties, "required": sorted(properties)},
        "answers": answers,
        "otherwise": otherwise,
    }


def ledger_over_mcp() -> dict:
    """A question only an MCP server can answer, through three of its ten tools, each called
    more than once: the situation progressive discovery exists for, where a deferred tool is
    found, described and then called by its name. It decided that a described tool stays off the
    wire (the same 3/3, 44% fewer tokens priced in full). Graded on the sum and the top
    category, which take every call to get right."""
    accounts = [{"id": acc, "currency": cur} for acc, cur, _ in _LEDGER]
    month = {"type": "string", "description": "The month, as YYYY-MM."}
    account = {"type": "string", "description": "An account id from list_accounts."}
    currency = {"type": "string", "description": "An ISO currency code, such as USD."}
    transactions = [
        {"args": {"account_id": acc, "month": _MONTH},
         "result": [{"amount": amount, "currency": cur, "category": category} for amount, category in rows]}
        for acc, cur, rows in _LEDGER
    ]
    rates = [{"args": {"currency": cur, "month": _MONTH}, "result": {"currency": cur, "vnd_per_unit": rate}}
             for cur, rate in _FX.items()]
    unused = [
        ("get_budget", "The monthly budget set for each category.", {"month": month}),
        ("list_categories", "Every spending and income category the ledger knows.", {}),
        ("create_transaction", "Record a new transaction on an account.",
         {"account_id": account, "amount": {"type": "number", "description": "Negative for spending."},
          "category": {"type": "string", "description": "The category name."}}),
        ("delete_transaction", "Delete a transaction by its id.", {"transaction_id": {"type": "string", "description": "The id."}}),
        ("get_net_worth", "Assets minus liabilities at the end of a month, in VND.", {"month": month}),
        ("export_csv", "Export an account's transactions for a month as CSV text.", {"account_id": account, "month": month}),
        ("get_account", "One account's name, currency and opening balance.", {"account_id": account}),
    ]
    tools = [
        _ledger_tool("list_accounts", "List the person's accounts with their currency.", {},
                     [{"args": {}, "result": accounts}], ""),
        _ledger_tool("get_transactions", "One account's transactions in one month, in the account's currency.",
                     {"account_id": account, "month": month}, transactions,
                     f"No transactions for that account and month. Accounts: acc-vnd, acc-usd, acc-eur; months as YYYY-MM, the ledger holds {_MONTH}."),
        _ledger_tool("get_fx_rate", "The month's average exchange rate of a currency, in VND per unit.",
                     {"currency": currency, "month": month}, rates,
                     f"No rate for that currency and month. Currencies: VND, USD, EUR; months as YYYY-MM, the ledger holds {_MONTH}."),
        *(_ledger_tool(name, description, props, [], "This ledger has no data for that request.")
          for name, description, props in unused),
    ]
    spent = sum(-amount * _FX[cur] for _, cur, rows in _LEDGER for amount, _ in rows if amount < 0)
    return {
        "id": "ledger-over-mcp",
        "mechanism": "tool_discovery",
        "prompt": (
            "Dùng sổ cái (server ledger) để trả lời: tháng 9/2026 tôi chi tiêu tổng cộng bao nhiêu "
            "tiền trên tất cả tài khoản, quy ra VND theo tỷ giá trung bình của tháng đó, và hạng mục "
            "nào chi nhiều nhất? Chỉ tính các khoản chi, không tính thu nhập. Ghi tổng (số nguyên VND, "
            "không dấu phân cách) vào file tong-chi.txt."
        ),
        "setup": {"notes.md": "# Chi tiêu\n"},
        "hidden": {},
        "unchanged": ["notes.md"],
        "answer_mentions": ["Ăn uống"],
        "mcp": {"server": "ledger", "tools": tools},
        "check": {"type": "file_contains", "path": "tong-chi.txt", "contains": str(spent)},
    }


def main() -> None:
    for task in (
        forget_the_caller(),
        red_and_unrelated(),
        retry_the_flake(),
        rename_everywhere(),
        three_modules(),
        seen_before(),
        figures_in_the_paper(),
        chart_from_the_file(),
        two_loans(),
        just_a_fact(),
        decide_in_a_sentence(),
        chart_from_the_workbook(),
        plan_on_a_timeline(),
        sales_by_country(),
        which_model_after_a_switch(),
        ledger_over_mcp(),
    ):
        _write(OUT, task)
    for task in recurrence_tasks():
        _write(RECUR, task)


def _write(out: Path, task: dict) -> None:
    folder = out / task["id"]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "task.json").write_text(json.dumps(task, indent=1), encoding="utf-8")
    size = sum(len(v) for v in task["setup"].values())
    print(f"{task['id']}: {len(task['setup'])} files, {size:,} chars -> {task['mechanism']} ({out.name})")


if __name__ == "__main__":
    main()
