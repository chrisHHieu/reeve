"""`ask_user` beyond one question with string options: an option can carry a description,
a recommendation and a preview; several questions go in one call and come back in one map. A plain
question with string options, and an item saved before any of this, stay exactly as they were.
"""

import asyncio
import json

from harness.integrations.inbox import InboxItem, InboxStore
from harness.interface.server.manager import SessionManager
from harness.tools.ask import MAX_GROUPED_QUESTIONS, answer_result, ask_user_tool, normalize_option, normalize_questions, option_label, question_item_fields

from test_durable_resume import ScriptedProvider, _run_until_pending, _text, _tool

BLANK_OPTION = {"label": "", "description": "", "recommended": False, "preview": ""}
TWO_QUESTIONS = [{"question": "Chart style?", "header": "Chart"}, {"question": "Colors?"}]


# -- what the model is told ----------------------------------------------------------------------


def test_the_schema_offers_rich_options_and_a_capped_list_of_questions():
    function = ask_user_tool().__tool_schema__["function"]
    assert function["name"] == "ask_user"
    properties = function["parameters"]["properties"]
    shapes = properties["options"]["items"]["anyOf"]  # a string, or an object that has a label
    assert {"type": "string"} in shapes
    rich = next(shape for shape in shapes if shape.get("type") == "object")
    assert rich["required"] == ["label"] and set(rich["properties"]) == {"label", "description", "recommended", "preview"}
    assert properties["questions"]["maxItems"] == MAX_GROUPED_QUESTIONS
    assert properties["questions"]["items"]["required"] == ["question"]


def test_a_brief_of_up_to_twelve_questions_is_one_call_and_a_form_from_five():
    from harness.tools.ask.schema import ASK_SCHEMA, FORM_FROM

    assert MAX_GROUPED_QUESTIONS == 12 and FORM_FROM == 5
    raw = [{"question": f"Question {n}?", "header": f"Q{n}", "options": [{"label": "A", "recommended": True}, "B"]} for n in range(15)]
    kept = normalize_questions(raw)
    assert len(kept) == 12 and kept[-1]["header"] == "Q11"
    said = ASK_SCHEMA["function"]["description"]
    assert "up to 12" in said and "from 5 they are shown as one form" in said and "`recommended`" in said
    # A form answers every question; one left to the agent comes back in the same map.
    left = "Up to you: choose what fits best, and say in your answer what you chose."
    assert answer_result(kept, json.dumps({"Q0": "A", "Q1": left})) == {"answers": {"Q0": "A", "Q1": left}}


# -- options and questions, made whole -----------------------------------------------------------


def test_an_option_is_filled_out_to_every_field_and_named_by_its_label():
    assert normalize_option("Bar") == {**BLANK_OPTION, "label": "Bar"}
    rich = normalize_option({"label": "Line", "recommended": True, "preview": "p"})
    assert rich["recommended"] is True and rich["preview"] == "p"
    assert (option_label("Bar"), option_label({"label": "Line"})) == ("Bar", "Line")


def test_questions_past_the_cap_and_ones_with_nothing_asked_are_dropped():
    assert len(normalize_questions([{"question": f"Q{n}?"} for n in range(MAX_GROUPED_QUESTIONS + 2)])) == MAX_GROUPED_QUESTIONS
    assert normalize_questions([{"question": "  "}, "junk", {"header": "h"}]) == []


def test_a_plain_question_keeps_its_string_options():
    fields = question_item_fields({"question": "Env?", "options": ["staging", "prod"]})
    assert (fields["title"], fields["options"], fields["questions"]) == ("Env?", ["staging", "prod"], [])  # still the plain pills


def test_rich_options_are_stored_whole():
    fields = question_item_fields({"question": "Env?", "options": [{"label": "staging", "recommended": True}]})
    assert fields["options"] == [{**BLANK_OPTION, "label": "staging", "recommended": True}]


def test_several_questions_show_the_first_on_the_item():
    fields = question_item_fields({"questions": [{"question": "Chart style?", "header": "Chart", "options": ["Bar"]}, {"question": "Colors?", "multi": True}]})
    assert (fields["title"], fields["header"], fields["options"][0]["label"]) == ("Chart style?", "Chart", "Bar")
    assert len(fields["questions"]) == 2 and fields["questions"][1]["multi"] is True


def test_a_call_that_asks_nothing_makes_no_item():
    for asked in ({}, {"question": "  "}, {"questions": [{"question": ""}]}):
        assert question_item_fields(asked) is None


# -- the answer ----------------------------------------------------------------------------------


def test_one_question_answers_with_a_string_and_several_with_a_map():
    assert answer_result([], "staging") == {"answer": "staging"}
    assert answer_result([], None) == {"answer": ""}
    assert answer_result(TWO_QUESTIONS, json.dumps({"Chart": "Bar", "Colors?": "Blue"})) == {"answers": {"Chart": "Bar", "Colors?": "Blue"}}
    assert answer_result(TWO_QUESTIONS, "Bar") == {"answers": {"Chart": "Bar"}}  # a text-only surface answered the first
    assert answer_result(TWO_QUESTIONS, "") == {"answer": ""}  # which the engine takes as declined


# -- the inbox -----------------------------------------------------------------------------------


def test_several_questions_survive_a_reload_of_the_inbox(tmp_path):
    store = InboxStore(tmp_path / "inbox.json")
    asked = {"questions": [{"question": "Format?", "header": "Format", "options": [{"label": "Table", "preview": "| a | b |"}]}, {"question": "Where to?"}]}
    item = store.add_question("s1", **question_item_fields(asked))
    reloaded = InboxStore(tmp_path / "inbox.json").get(item.id)
    assert reloaded.header == "Format" and reloaded.questions == item.questions
    assert reloaded.options[0]["preview"] == "| a | b |"


def test_an_item_saved_before_any_of_this_still_loads():
    # no header, no questions, string options
    old = InboxItem(id="x", session_id="s", kind="question", title="Env?", options=["staging"])
    assert old.header == "" and old.questions == []


def test_several_questions_answered_in_the_app_reach_the_model_as_one_map(tmp_path):
    questions = [
        {"question": "Chart style?", "header": "Chart", "options": [{"label": "Bar", "recommended": True}, "Line"]},
        {"question": "Which distribution?", "header": "Distribution"},
    ]
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([_tool("ask_user", {"questions": questions}, "call_g"), _text("Bar it is, stacked.")]))

    async def answer():
        engine = manager.get_engine("grouped-q", agent="reeve", workspace=str(tmp_path))
        item = await _run_until_pending(manager, "grouped-q", engine)
        assert (item.kind, item.tool_call_id, item.title, len(item.questions)) == ("question", "call_g", "Chart style?", 2)
        await manager.resolve_inbox(item.id, json.dumps({"Chart": "Bar", "Distribution": "Stacked"}))

    asyncio.run(answer())
    results = [m for m in manager.session_store.load("grouped-q").messages if m.get("role") == "tool"]
    assert results, "no tool result was recorded"
    assert json.loads(results[-1]["content"]) == {"answers": {"Chart": "Bar", "Distribution": "Stacked"}}
    assert manager.inbox.pending("grouped-q") == []
