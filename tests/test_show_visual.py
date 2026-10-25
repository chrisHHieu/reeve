"""`show_visual`: blocks are checked against the catalog, a block that names a file is
filled from the file by the harness, and the model reads back what was shown. Data is invented."""

from __future__ import annotations

import pytest

from harness.tools.visual import VISUAL_CONTRACT, visual_tools
from harness.tools.visual.data import parse_number
from harness.tools.visual.formula import FormulaError, check_formula


@pytest.fixture()
def show(tmp_path):
    [tool] = visual_tools(str(tmp_path))
    return tool


def _card(tool, blocks):
    value = tool(blocks=blocks)
    meta = value.get("_card_meta")
    presented = VISUAL_CONTRACT.present_result({"blocks": blocks}, {k: v for k, v in value.items() if k != "_card_meta"}, meta)
    return value, presented


def test_a_chart_from_a_file_is_computed_by_the_harness_not_typed(show, tmp_path):
    (tmp_path / "spend.csv").write_text(
        "date;category;amount\n01/09;Ăn uống;1.250.000\n02/09;Nhà ở;6.500.000\n03/09;Ăn uống;960.000\n04/09;Đi lại;350.000\n",
        encoding="utf-8",
    )
    blocks = [{"type": "chart", "kind": "bar", "title": "Theo danh mục",
               "source": {"file": "spend.csv", "x": "Category", "y": "amount", "agg": "sum"}}]
    value, presented = _card(show, blocks)
    [block] = presented.card["blocks"]
    assert block["labels"] == ["Nhà ở", "Ăn uống", "Đi lại"]  # summed, then largest first
    assert block["series"] == [{"name": "amount", "values": [6500000.0, 2210000.0, 350000.0]}]
    assert block["source"] == {"file": "spend.csv", "rows": 4}
    text = VISUAL_CONTRACT.render({}, {k: v for k, v in value.items() if k != "_card_meta"})
    assert text.startswith("Shown to the user as v1:") and "Ăn uống=2.21e+06" in text and "spend.csv (4 rows)" in text


def test_bars_over_time_stand_in_time_order_and_several_series_are_not_ranked(show, tmp_path):
    """Months ranked by their first column read July, September, August, October (a real session,
    2026-10-10). Time stays in time order; one series over categories is still ranked."""
    (tmp_path / "months.csv").write_text(
        "month,food,family\n2026-07,3851670,1000000\n2026-09,3535655,0\n2026-08,3498675,6000000\n2026-10,900000,10000000\n",
        encoding="utf-8",
    )
    many = [{"type": "chart", "kind": "bar", "source": {"file": "months.csv", "x": "month", "y": ["food", "family"]}}]
    assert _card(show, many)[1].card["blocks"][0]["labels"] == ["2026-07", "2026-08", "2026-09", "2026-10"]
    one = [{"type": "chart", "kind": "bar", "source": {"file": "months.csv", "x": "month", "y": "food"}}]
    assert _card(show, one)[1].card["blocks"][0]["labels"] == ["2026-07", "2026-08", "2026-09", "2026-10"]
    (tmp_path / "slash.csv").write_text("thang,chi\n09/2026,2\n07/2026,5\n10/2026,1\n", encoding="utf-8")
    slashed = [{"type": "chart", "kind": "bar", "source": {"file": "slash.csv", "x": "thang", "y": "chi"}}]
    assert _card(show, slashed)[1].card["blocks"][0]["labels"] == ["07/2026", "09/2026", "10/2026"]
    ranked = [{"type": "chart", "kind": "bar", "source": {"file": "months.csv", "x": "month", "y": "food", "sort": "desc"}}]
    assert _card(show, ranked)[1].card["blocks"][0]["labels"][0] == "2026-07"  # a named sort still wins


def test_inline_blocks_are_checked_and_a_bad_one_names_itself(show):
    good = [
        {"type": "metrics", "items": [{"label": "Tổng chi", "value": "13,7 tr", "delta": "+8%"}]},
        {"type": "compare", "options": ["Gói A", {"name": "Gói B", "highlight": True}],
         "rows": [{"label": "Lãi", "values": ["9%", "7,5% rồi thả nổi"]}]},
        {"type": "table", "columns": ["a", "b"], "rows": [[1, 2], [3, 4]]},
        {"type": "form", "title": "Báo cáo", "fields": [{"name": "fmt", "label": "Định dạng", "kind": "select",
                                                         "options": ["Excel", "PDF"]}], "submit": "Tạo"},
    ]
    value, presented = _card(show, good)
    assert [b["type"] for b in presented.card["blocks"]] == ["metrics", "compare", "table", "form"]
    assert "its answer arrives as the user's next message" in value["shown"][3]
    bad = show(blocks=[{"type": "chart", "labels": ["a", "b"], "series": [{"name": "x", "values": [1]}]}])
    assert bad["error"].startswith("Nothing was shown. block 1 (chart): series 'x' has 1 values for 2 labels")
    assert "type` is one of" in show(blocks=[{"type": "gauge"}])["error"]


def test_a_what_if_is_refused_when_a_formula_names_what_it_does_not_declare(show):
    block = {"type": "what_if", "inputs": [{"name": "amount", "label": "Vay", "min": 100, "max": 3000, "value": 1500}],
             "values": [{"name": "r", "formula": "9/100/12"}],
             "outputs": [{"label": "Trả hàng tháng", "formula": "pmt(r, years*12, amount*1e6)"}]}
    assert "unknown name 'years'" in show(blocks=[block])["error"]
    block["inputs"].append({"name": "years", "min": 5, "max": 30, "value": 20, "step": 1})
    block["chart"] = {"x": {"name": "y", "from": 0, "to": "years"}, "series": [{"name": "Đã trả", "formula": "y*12*pmt(r, years*12, amount*1e6)"}]}
    value, presented = _card(show, [block])
    assert presented.card["blocks"][0]["chart"]["x"]["to"] == "years"


def test_a_file_outside_the_session_is_not_read(show, tmp_path):
    outside = tmp_path.parent / "secret.csv"
    outside.write_text("a,b\n1,2\n", encoding="utf-8")
    error = show(blocks=[{"type": "table", "source": {"file": str(outside)}}])["error"]
    assert "Nothing was shown" in error and "1,2" not in error


def test_the_formula_grammar():
    check_formula("min(a, 2) ** 2 + -b % 3 ^ 2", {"a", "b"})
    for bad in ["a.b", "__import__('os')", "a[0]", "a > 1", "x", "sqrt(1, 2)", "(a", "a +"]:
        with pytest.raises(FormulaError):
            check_formula(bad, {"a"})


def test_numbers_read_like_the_gui_reads_them():
    assert parse_number("6.500.000đ") == 6500000 and parse_number("1,250.5") == 1250.5
    assert parse_number("12%") == 12 and parse_number("-3,5") == -3.5
    assert parse_number("v1.2") is None and parse_number("abc12") is None


def test_no_tool_for_a_remote_session(tmp_path):
    assert visual_tools(str(tmp_path), backend=object()) == []


def test_a_visual_is_named_and_the_model_is_told_it_is_part_of_the_answer(show):
    """The measured failure (2026-10-09): 8 of 10 answers that showed a visual restated it as a
    table. The result names the visual, says where it goes and that it is not to be said again."""
    blocks = [{"type": "table", "columns": ["a"], "rows": [["1"], ["2"]]}]
    first, presented = _card(show, blocks)
    second = show(blocks=blocks)
    assert (first["id"], second["id"]) == ("v1", "v2")
    assert presented.card["id"] == "v1"
    text = VISUAL_CONTRACT.render({}, {k: v for k, v in first.items() if k != "_card_meta"})
    assert "Shown to the user as v1" in text and "`:visual[v1]`" in text and "Do not restate" in text
    assert show(blocks=[{"type": "gauge"}]).get("id") is None  # a refused call takes no name


def test_a_surface_that_draws_no_visual_writes_it_as_text(show):
    """The TUI and `reeve exec` write a placed visual from its card, as ChatGPT keeps a Markdown
    fallback for clients that draw none; a visual the surface never saw says what stood there."""
    from harness.interface.tui.transcript import lines_for
    from harness.session.events import Event, EventType
    from harness.tools.visual import plain_visual_markers

    text = "A is cheaper.\n\n:visual[v1]\n\nDrag the rate."
    assert plain_visual_markers(text) == "A is cheaper.\n\n[visual v1: open this session in the app to see it]\n\nDrag the rate."
    _, presented = _card(show, [
        {"type": "chart", "kind": "bar", "title": "Spend", "unit": "đ", "labels": ["Nhà ở", "Ăn uống"],
         "series": [{"name": "amount", "values": [6500000, 2210000]}]},
        {"type": "metrics", "items": [{"label": "Total", "value": 8710000, "unit": "đ"}]},
    ])
    shown: dict = {}
    lines_for(Event(EventType.TOOL_FINISHED, {"name": "show_visual", "presentation": {"card": presented.card}}), shown)
    [line] = lines_for(Event(EventType.ASSISTANT_MESSAGE, {"text": text}), shown)
    assert ":visual[" not in line and "open this session" not in line
    assert "| Nhà ở | 6,500,000 đ |" in line and "- Total: 8,710,000 đ" in line and line.endswith("Drag the rate.")


def test_the_shapes_live_models_sent_are_read(show, tmp_path):
    """Three refusals from the live probes: a chart series written over `x` while its variable
    had another name, a what-if that is a chart alone, and a metric naming its column as `y`."""
    what_if = {"type": "what_if", "inputs": [{"name": "years", "min": 1, "max": 30, "value": 20}],
               "chart": {"x": {"name": "year", "from": 0, "to": "years"}, "series": [{"name": "s", "formula": "x * 2"}]}}
    assert "error" not in show(blocks=[what_if])
    (tmp_path / "d.csv").write_text("cat,amount\na,1\nb,2\n", encoding="utf-8")
    metric = {"type": "metrics", "items": [{"label": "Total", "source": {"file": "d.csv", "y": "amount", "agg": "sum"}}]}
    _, presented = _card(show, [metric])
    assert presented.card["blocks"][0]["items"][0]["value"] == 3


def _workbook(path, rows, date_column=None):
    """A minimal .xlsx: one sheet, shared strings for text, a date style for one column."""
    import zipfile
    from xml.sax.saxutils import escape

    strings: list[str] = []

    def cell(ref, value, col):
        if isinstance(value, (int, float)):
            style = ' s="1"' if col == date_column else ""
            return f'<c r="{ref}"{style}><v>{value}</v></c>'
        strings.append(str(value))
        return f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>'

    body = "".join(
        f'<row r="{r + 1}">' + "".join(cell(f"{chr(65 + c)}{r + 1}", v, c) for c, v in enumerate(row)) + "</row>"
        for r, row in enumerate(rows)
    )
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("xl/workbook.xml", f'<workbook {ns}><sheets><sheet name="Chi tiêu" sheetId="1"/></sheets></workbook>')
        zf.writestr("xl/worksheets/sheet1.xml", f"<worksheet {ns}><sheetData>{body}</sheetData></worksheet>")
        zf.writestr("xl/sharedStrings.xml", f"<sst {ns}>" + "".join(f"<si><t>{escape(s)}</t></si>" for s in strings) + "</sst>")
        zf.writestr("xl/styles.xml", f'<styleSheet {ns}><cellXfs><xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>')


def test_a_workbook_is_read_like_a_csv_and_its_dates_are_dates(show, tmp_path):
    """Office data lives in workbooks: a chart names the .xlsx and its sheet, the harness sums it."""
    _workbook(tmp_path / "q3.xlsx", [["Ngày", "Danh mục", "Số tiền"], [46266, "Nhà ở", 6500000], [46267, "Ăn uống", 1250000],
                                     [46268, "Nhà ở", 300000]], date_column=0)
    chart = {"type": "chart", "kind": "bar", "source": {"file": "q3.xlsx", "sheet": "chi tiêu", "x": "Danh mục", "y": "Số tiền"}}
    _, presented = _card(show, [chart])
    block = presented.card["blocks"][0]
    assert block["labels"] == ["Nhà ở", "Ăn uống"] and block["series"][0]["values"] == [6800000.0, 1250000.0]
    table = show(blocks=[{"type": "table", "source": {"file": "q3.xlsx"}}])
    assert "error" not in table
    from harness.tools.visual.data import read_rows

    columns, rows = read_rows(tmp_path / "q3.xlsx")
    assert rows[0]["Ngày"] == "2026-09-01"  # a date cell, not the day count 46266
    assert "has no sheet 'Q4'" in show(blocks=[{"type": "table", "source": {"file": "q3.xlsx", "sheet": "Q4"}}])["error"]


def test_a_timeline_reads_the_dates_people_write(show):
    items = [{"label": "Thiết kế", "start": "15/9/2026", "end": "2026-10-10", "group": "Chuẩn bị"},
             {"label": "Ra mắt", "start": "2026-12-08"}]
    value, presented = _card(show, [{"type": "timeline", "title": "Kế hoạch", "items": items}])
    block = presented.card["blocks"][0]
    assert block["items"][0] == {"label": "Thiết kế", "start": "2026-09-15", "end": "2026-10-10", "group": "Chuẩn bị"}
    assert "from 2026-09-15 to 2026-12-08" in value["shown"][0]
    bad = show(blocks=[{"type": "timeline", "items": [{"label": "x", "start": "Q4"}]}])["error"]
    assert "`start` 'Q4' is not a date" in bad


def test_a_map_names_countries_in_any_language_and_refuses_what_is_not_one(show, tmp_path):
    regions = [{"name": "Việt Nam", "value": 120}, {"name": "Japan", "value": 210}, {"name": "KR", "value": 150},
               {"name": "Singapore", "value": 45}, {"name": "Gibraltar", "value": 2}]
    value, presented = _card(show, [{"type": "map", "unit": "tr USD", "regions": regions}])
    block = presented.card["blocks"][0]
    assert [r["id"] for r in block["regions"]] == ["VNM", "JPN", "KOR", "SGP", "GIB"]
    # 1:50m outlines draw Singapore; Gibraltar has none, and the model is told it is listed instead.
    assert "Gibraltar too small to shade" in value["shown"][0] and "Singapore" not in value["shown"][0].split(";")[-1]
    assert "no country named 'Narnia'" in show(blocks=[{"type": "map", "regions": [{"name": "Narnia", "value": 1}]}])["error"]
    (tmp_path / "sales.csv").write_text("country,amount\nThái Lan,10\nThái Lan,5\nĐức,7\n", encoding="utf-8")
    _, presented = _card(show, [{"type": "map", "source": {"file": "sales.csv", "region": "country", "value": "amount"}}])
    assert [(r["id"], r["value"]) for r in presented.card["blocks"][0]["regions"]] == [("THA", 15.0), ("DEU", 7.0)]


def test_the_setting_says_when_the_tool_is_offered(tmp_path):
    """Settings > Visuals: off is no tool; asked is a tool that waits to be asked."""
    assert visual_tools(str(tmp_path), mode="off") == []
    [asked] = visual_tools(str(tmp_path), mode="asked")
    assert "only when the person asks" in asked.__tool_schema__["function"]["description"]
    [auto] = visual_tools(str(tmp_path))
    assert "When one sentence decides the question" in auto.__tool_schema__["function"]["description"]


def test_the_review_reads_what_was_shown_from_facts():
    facts = VISUAL_CONTRACT.facts({"blocks": [{"type": "chart", "source": {"file": "a.csv"}}, {"type": "table"}]},
                                  {"shown": ["x", "y"], "id": "v2"})
    assert facts == {"shown": True, "name": "v2", "blocks": "chart,table", "sourced": 1}
    assert VISUAL_CONTRACT.facts({}, {"error": "Nothing was shown."}) == {"shown": False}


def test_the_visuals_setting_round_trips_and_reaches_the_tool_list(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from harness.compose import build_engine
    from harness.interface.server import create_app
    from harness.interface.server.manager import SessionManager
    from harness.modes.agents.reeve import reeve_agent

    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    client = TestClient(create_app(SessionManager(data_dir=tmp_path / "data")))
    assert client.get("/v1/settings").json()["visuals"] == "auto"
    assert client.post("/v1/settings/visuals", json={"visuals": "off"}).json() == {"ok": True, "visuals": "off"}
    assert client.post("/v1/settings/visuals", json={"visuals": "loud"}).json()["ok"] is False
    assert SessionManager(data_dir=tmp_path / "data").visuals() == "off"
    names = build_engine(agent=reeve_agent(), workspace=tmp_path, provider=object(), visuals="off").registry.names()
    assert "show_visual" not in names and "present" in names


def test_the_review_counts_what_an_answer_did_with_its_visuals(tmp_path):
    """Placed and said once; shown and left out; restated as a table; refused."""
    import json

    from scripts import harness_review

    def result(turn, seq, facts):
        return {"type": "tool/result", "name": "show_visual", "turn": turn, "seq": seq, "t": 1, "facts": facts}

    def answer(turn, seq, text):
        return {"type": "assistant/message", "turn": turn, "seq": seq, "t": 1, "text": text}

    rows = [
        {"type": "turn/start", "turn": 1, "t": 1}, result(1, 1, {"shown": True, "name": "v1"}),
        result(1, 2, {"shown": True, "name": "v2"}), answer(1, 3, "A.\n\n:visual[v2]\n\nB."),
        {"type": "turn/start", "turn": 2, "t": 1}, result(2, 4, {"shown": False}), result(2, 5, {"shown": True, "name": "v3"}),
        answer(2, 6, "A.\n\n| a | b |\n| --- | --- |\n| 1 | 2 |"),
    ]
    log = tmp_path / "a.events.jsonl"
    log.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    counts, refs = harness_review.review([log])
    assert counts["visual turns"] == 2 and counts["visuals shown"] == 3
    assert refs["visual-drafts"] == [("a.events.jsonl", 3, "v1")]
    assert refs["visual-unplaced"] == [("a.events.jsonl", 6, "1 shown")]
    assert refs["visual-restated"] == [("a.events.jsonl", 6)]
    assert refs["visual-refused"] == [("a.events.jsonl", 4)]
