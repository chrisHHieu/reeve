"""The `seen-before` probe: a failure that is back, in a project too large to read.

Its first version was ten files. Run on a model (2026-10-07, five runs a side) the recall
acted every time and showed nothing: the agent read most of the project before it first ran
the suite, so by the time the note arrived there was little left for it to save. A recall
is worth what locating the cause costs, and in ten files that is nothing.

This one is about ninety files. One test fails with a wrong total. The cause is eight
imports away, behind two lookups by name (a manifest of rule providers, a catalogue of
tables) and thirty tables with names that say nothing (`t001` … `t030`): one key in one
table is spelled in mixed case, and lookups are lower-cased. Nothing near the test names
the table. Around it are six packages that have nothing to do with it, each with its own
passing tests, so reading everything is not a plan.

The project's repairs store is seeded with the earlier repair of that same test (`repairs`),
which is the probe's situation: this has been fixed here before.

The same project carries two tasks of the recurrence eval (`benchmarks/tasks-recur`), so that a
kept repair is not measured on one bug alone:

    seen-before-catalogue   another cause of the same failure, also far from the test: the
                            catalogue sends the region rates to a table that is not them. It
                            is repaired, then it is back, as in `seen-before`.
    seen-before-new-cause   the first session repairs `seen-before`'s cause; the later ones meet
                            the project as it left it, with the catalogue's cause written in.
                            The same test fails, for a reason the kept repair has nothing to do
                            with: what a record (and a lesson) of an earlier cause does when it
                            is recalled where it does not apply.
"""

from __future__ import annotations

from textwrap import dedent

CONFTEST = "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent))\n"
FAILING = "tests/test_report.py::test_total_includes_regional_tax"
#: The table that holds the bug, and its number among the thirty.
CAUSE = "shop/data/tables/t017.py"
#: Where the other cause is: the catalogue that says which module holds which table.
CATALOGUE = "shop/data/catalogue.py"
#: The catalogue's line for the region rates, and the same line sending them to a table of
#: sku weights (a lookup by region there finds nothing, which is worth 0).
_REGION_ROW = "'rates-by-region': \"shop.data.tables.t017\""
_WRONG_ROW = "'rates-by-region': \"shop.data.tables.t001\""

#: name in the catalogue -> (table number, rows). The three the price path reads come first.
_TABLES: dict[str, tuple[int, dict[str, float]]] = {
    "rates-by-region": (17, {"EU-North": 0.25, "eu-south": 0.21, "uk": 0.20, "us-west": 0.0725}),
    "rates-by-product": (4, {"chair": 0.10}),
    "surcharge-by-region": (23, {"eu-north": 0.04, "eu-south": 0.03, "uk": 0.05, "us-west": 0.02}),
}
_OTHER_NAMES = (
    "weights-by-sku", "lead-days-by-supplier", "pallet-size-by-sku", "duty-by-origin",
    "margin-by-category", "restock-level-by-sku", "handling-by-carrier", "bulk-break-by-sku",
    "returns-window-by-category", "loyalty-rate-by-tier", "insurance-by-value-band",
    "packaging-cost-by-size", "priority-fee-by-service", "storage-cost-by-zone",
    "credit-limit-by-tier", "late-fee-by-terms", "rebate-by-supplier", "markup-by-channel",
    "fuel-levy-by-carrier", "sample-rate-by-category", "commission-by-channel",
    "damage-rate-by-carrier", "shrinkage-by-zone", "cutoff-hour-by-carrier",
    "min-order-by-supplier", "carton-count-by-sku", "tolerance-by-category",
)
_KEYS = ("alpha", "bravo", "charlie", "delta", "echo")

#: The six packages beside the shop: (package, noun, the five modules it has).
_AROUND = (
    ("inventory", "stock", ("levels", "counts", "bins", "transfers", "audits")),
    ("shipping", "parcel", ("labels", "routes", "carriers", "manifests", "tracking")),
    ("customers", "account", ("profiles", "tiers", "contacts", "consent", "merges")),
    ("returns", "claim", ("intake", "grading", "refunds", "restocking", "disputes")),
    ("notify", "message", ("templates", "queues", "digests", "channels", "retries")),
    ("ledger", "entry", ("journals", "periods", "accruals", "reversals", "balances")),
)


def _tables() -> dict[str, str]:
    """`shop/data/tables/tNNN.py` for all thirty, and the catalogue that names them."""
    numbered = dict(_TABLES)
    free = [n for n in range(1, 31) if n not in {number for number, _ in _TABLES.values()}]
    for name, number in zip(_OTHER_NAMES, free):
        numbered[name] = (number, {key: round(0.01 * (number + i), 4) for i, key in enumerate(_KEYS)})
    files = {"shop/data/tables/__init__.py": ""}
    for name, (number, rows) in numbered.items():
        body = "".join(f"    {key!r}: {rate!r},\n" for key, rate in rows.items())
        files[f"shop/data/tables/t{number:03d}.py"] = f"ROWS = {{\n{body}}}\n"
    index = "".join(
        f"    {name!r}: \"shop.data.tables.t{number:03d}\",\n"
        for name, (number, _) in sorted(numbered.items(), key=lambda item: item[1][0])
    )
    files["shop/data/catalogue.py"] = (
        '"""Which module holds which table. Tables are numbered in the order they were added."""\n\n'
        f"INDEX = {{\n{index}}}\n"
    )
    return files


def _around() -> dict[str, str]:
    """Six packages with nothing to do with the failure, and a passing test file for each."""
    files: dict[str, str] = {}
    for package, noun, modules in _AROUND:
        files[f"{package}/__init__.py"] = f'"""{package.capitalize()}: everything about a {noun}."""\n'
        for position, module in enumerate(modules):
            files[f"{package}/{module}.py"] = dedent(f'''\
                """{module.capitalize()} of a {noun}."""

                _{module}: dict[str, list] = {{}}


                def add_{noun}_{module}(key, value):
                    """Record `value` under `key`; returns how many are held for it."""
                    held = _{module}.setdefault(str(key).strip().lower(), [])
                    held.append(value)
                    return len(held)


                def {noun}_{module}_for(key):
                    return list(_{module}.get(str(key).strip().lower(), []))


                def {noun}_{module}_total(key, weight={position + 1}):
                    return sum(v * weight for v in {noun}_{module}_for(key) if isinstance(v, (int, float)))
                ''')
        first, second = modules[0], modules[1]
        files[f"tests/test_{package}.py"] = dedent(f'''\
            from {package}.{first} import add_{noun}_{first}, {noun}_{first}_for, {noun}_{first}_total
            from {package}.{second} import add_{noun}_{second}, {noun}_{second}_total


            def test_{noun}_{first}_are_kept_by_key():
                assert add_{noun}_{first}(" K1 ", 2) == 1
                assert add_{noun}_{first}("k1", 3) == 2
                assert {noun}_{first}_for("K1") == [2, 3]


            def test_{noun}_{first}_total():
                add_{noun}_{first}("k2", 4)
                assert {noun}_{first}_total("k2") == 4


            def test_{noun}_{second}_total_is_weighted():
                add_{noun}_{second}("k3", 5)
                assert {noun}_{second}_total("k3") == 10
            ''')
    return files


def _shop() -> dict[str, str]:
    return {
        "shop/__init__.py": "",
        "shop/report.py": dedent('''\
            """Order totals for the weekly report."""

            from shop.pricing import price_for


            def order_total(order):
                return round(
                    sum(price_for(line["sku"], line["qty"], order["region"]) for line in order["lines"]), 2
                )
            '''),
        "shop/pricing.py": dedent('''\
            """What one order line costs where it ships."""

            from shop import rules

            CATALOG = {"desk": 80.0, "lamp": 20.0, "chair": 45.0}


            def price_for(sku, qty, region):
                base = CATALOG[sku] * qty
                base -= base * rules.apply("discount", sku)
                return base * (1 + rules.apply("tax", region))
            '''),
        "shop/rules/__init__.py": "from shop.rules.engine import apply\n",
        "shop/rules/engine.py": dedent('''\
            """Applies one kind of rule to one key."""

            from shop.rules import loader


            def apply(kind, key):
                return loader.provider_for(kind).rate(key)
            '''),
        "shop/rules/loader.py": dedent('''\
            """Finds the provider of a kind of rule, by the manifest."""

            import importlib

            from shop.rules.manifest import MANIFEST

            _providers = {}


            def provider_for(kind):
                if kind not in _providers:
                    _providers[kind] = importlib.import_module(MANIFEST[kind]).PROVIDER
                return _providers[kind]
            '''),
        "shop/rules/manifest.py": dedent('''\
            """Which module provides which kind of rule."""

            MANIFEST = {
                "tax": "shop.rules.providers.regional",
                "discount": "shop.rules.providers.product",
                "shipping": "shop.rules.providers.regional_flat",
            }
            '''),
        "shop/rules/providers/__init__.py": "",
        "shop/rules/providers/regional.py": dedent('''\
            """A rate that depends on where the order ships."""

            from shop.data import store


            class RegionalRates:
                table = "rates-by-region"

                def rate(self, region):
                    return store.lookup(self.table, region)


            PROVIDER = RegionalRates()
            '''),
        "shop/rules/providers/product.py": dedent('''\
            """A rate that depends on the product."""

            from shop.data import store


            class ProductRates:
                table = "rates-by-product"

                def rate(self, sku):
                    return store.lookup(self.table, sku)


            PROVIDER = ProductRates()
            '''),
        "shop/rules/providers/regional_flat.py": dedent('''\
            """A flat surcharge that depends on where the order ships."""

            from shop.data import store


            class RegionalSurcharge:
                table = "surcharge-by-region"

                def rate(self, region):
                    return store.lookup(self.table, region)


            PROVIDER = RegionalSurcharge()
            '''),
        "shop/data/__init__.py": "",
        "shop/data/keys.py": dedent('''\
            """One spelling for a key, whoever typed it."""


            def normalise(key):
                return str(key).strip().lower()
            '''),
        "shop/data/store.py": dedent('''\
            """Reads a table by its catalogue name. A key a table does not hold is worth 0."""

            import importlib

            from shop.data.catalogue import INDEX
            from shop.data.keys import normalise

            _loaded = {}


            def _rows(table):
                if table not in _loaded:
                    _loaded[table] = dict(importlib.import_module(INDEX[table]).ROWS)
                return _loaded[table]


            def lookup(table, key):
                return _rows(table).get(normalise(key), 0.0)
            '''),
        "tests/test_report.py": dedent('''\
            from shop.report import order_total


            def test_total_includes_regional_tax():
                order = {"region": "EU-North", "lines": [{"sku": "desk", "qty": 1}, {"sku": "lamp", "qty": 1}]}
                assert order_total(order) == 125.0


            def test_total_applies_the_standing_discount():
                order = {"region": "uk", "lines": [{"sku": "chair", "qty": 2}]}
                assert order_total(order) == 97.2
            '''),
    }


def _project() -> dict[str, str]:
    """The ninety files, with `seen-before`'s cause in them."""
    return {"conftest.py": CONFTEST, **_shop(), **_tables(), **_around()}


def _repaired(files: dict[str, str]) -> dict[str, str]:
    """`files` with `seen-before`'s cause repaired."""
    return {**files, CAUSE: files[CAUSE].replace("'EU-North'", "'eu-north'")}


def _miscatalogued(files: dict[str, str]) -> dict[str, str]:
    """`files` with the catalogue's cause written in."""
    assert _REGION_ROW in files[CATALOGUE]
    return {**files, CATALOGUE: files[CATALOGUE].replace(_REGION_ROW, _WRONG_ROW)}


def _hidden() -> dict[str, str]:
    """What grades every task of this project: a region is found however it is spelled, and the
    regions it does not hold are worth nothing. Either cause fails it."""
    return {
        "tests/test_report_regions.py": dedent('''\
            from shop.report import order_total

            ORDER = [{"sku": "desk", "qty": 1}, {"sku": "lamp", "qty": 1}]


            def test_a_region_is_found_however_it_is_spelled():
                assert order_total({"region": "eu-north", "lines": ORDER}) == 125.0
                assert order_total({"region": " EU-NORTH ", "lines": ORDER}) == 125.0


            def test_the_other_regions_still_price():
                assert order_total({"region": "eu-south", "lines": ORDER}) == 121.0
                assert order_total({"region": "nowhere", "lines": ORDER}) == 100.0
            '''),
    }


#: The words every task of this project is given: the failure is back after a merge.
PROMPT = (
    "tests/test_report.py is failing again since yesterday's merge. Make the suite "
    "green. Do not change the tests."
)


def seen_before() -> dict:
    files = _project()
    return {
        "id": "seen-before",
        "mechanism": "repair_recall",
        "prompt": PROMPT,
        "setup": files,
        "hidden": _hidden(),
        "unchanged": ["tests/test_report.py"],
        # An earlier session of this project repaired the same test, in the same file, and
        # the merge put the bug back: what that repair left is gone again.
        "repairs": [
            {
                "marks": [f"test:{FAILING}"], "command": "python -m pytest -q", "files": [CAUSE], "steps": 14,
                "left": {CAUSE: _repaired(files)[CAUSE]},
            }
        ],
        "check": {"type": "pytest"},
    }


def seen_before_catalogue() -> dict:
    """The recurrence eval's second cause: repaired, then back."""
    return {
        "id": "seen-before-catalogue",
        "mechanism": "repair_recall",
        "prompt": PROMPT,
        "setup": _miscatalogued(_repaired(_project())),
        "hidden": _hidden(),
        "unchanged": ["tests/test_report.py"],
        "check": {"type": "pytest"},
    }


def seen_before_new_cause() -> dict:
    """The recurrence eval's misplaced recall: `seen-before`'s cause first, then (`later`, written
    over the project as the first session left it) the catalogue's, with the first repair still
    in place."""
    files = _project()
    return {
        "id": "seen-before-new-cause",
        "mechanism": "repair_recall",
        "prompt": PROMPT,
        "setup": files,
        "later": {CATALOGUE: _miscatalogued(files)[CATALOGUE]},
        "hidden": _hidden(),
        "unchanged": ["tests/test_report.py"],
        "check": {"type": "pytest"},
    }


def recurrence_tasks() -> list[dict]:
    """The tasks only the recurrence eval runs (`benchmarks/tasks-recur`). They stay out of the
    probe suite: `ablation` picks a mechanism's probe by its name, one probe a mechanism."""
    return [seen_before_catalogue(), seen_before_new_cause()]
