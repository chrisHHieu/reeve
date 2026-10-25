"""A tool call DeepSeek wrote as its own markup, left in the reply's text, is read back as a tool call.

Found in real sessions (2026-10-02): five replies from `deepseek-v4.1-flash` held the call as
text, `finish_reason=stop`, no `tool_calls`. The turn ended "completed", nothing ran, and the markup sat in the
transcript. The markup is DSML, and in the logs it arrives damaged: the opening `function_calls` / `invoke` tokens are
gone and the tool's name is left as `<parameter name="TOOL">`. The fixtures below have that shape with invented content.
What has to hold:

  - the whole form and the damaged forms both become a call to the named tool with its parameters;
  - it is as strict as the other salvages: an unknown tool, a parameter the tool does not declare, or a call that
    never closes stays text;
  - the provider hands the turn the salvaged calls and clears the text.
"""

from __future__ import annotations

from harness.llm.providers.dsml import looks_like_dsml, salvage_dsml
from harness.llm.providers.chat.salvage import maybe_salvage_tool_calls as _maybe_salvage_tool_calls, salvage_tool_calls_from_text as _salvage_tool_calls_from_text

B = "｜"  # the fullwidth bar DeepSeek's special tokens are written with
D = f"{B}DSML{B}"


def _tool(name, *params):
    return {
        "type": "function",
        "function": {
            "name": name,
            "parameters": {"type": "object", "properties": {p: {"type": "string"} for p in params}},
        },
    }


TOOLS = [
    _tool("replace_in_file", "path", "old", "new"),
    _tool("run_shell", "command", "description", "shell", "timeout"),
    _tool("read_file", "path"),
]

# The three shapes seen in the session logs, content invented.
LOST_HEAD_SPACED = (
    "Fixed the stray line:\n\n"
    '<parameter name="replace_in_file">\n'
    f'<{D} parameter name="new" string="true">line one\nline two</{D} parameter>\n'
    f'<{D} parameter name="old" string="true">line one\n\nline two</{D} parameter>\n'
    f'<{D} parameter name="path" string="true">docs/guide.md</{D} parameter>\n'
    f"</{D} invoke>\n</{D} calls>"
)
LOST_HEAD_PLAIN = (
    "Checking the lockfile.\n"
    '<parameter name="replace_in_file">\n'
    '<parameter name="path">src/app.py</parameter>\n'
    '<parameter name="old">a = 1</parameter>\n'
    '<parameter name="new">a = 2</parameter>\n'
    f"</invoke>\n</{D} parameter>\n</{D} invoke>\n</{D} calls>"
)
LOST_HEAD_MIXED = (
    "Run the suite:\n"
    '<parameter name="run_shell">\n'
    '<parameter name="command" string="true">cd /repo && pytest -q\n</parameter>\n'
    f'<parameter name="description">Run tests</{D} parameter>\n'
    f'<{D} parameter name="shell" string="true">bash</{D} parameter>\n'
    f"</{D} invoke>\n</{D} calls>"
)
WHOLE = (
    f"<{D}function_calls>\n"
    f'<{D}invoke name="read_file">\n'
    f'<{D}parameter name="path" string="true">README.md</{D}parameter>\n'
    f"</{D}invoke>\n"
    f"</{D}function_calls>"
)


def test_the_damaged_forms_become_calls_to_the_named_tool():
    spaced = salvage_dsml(LOST_HEAD_SPACED, TOOLS)
    assert [c.name for c in spaced] == ["replace_in_file"]
    assert spaced[0].arguments == {"new": "line one\nline two", "old": "line one\n\nline two", "path": "docs/guide.md"}

    plain = salvage_dsml(LOST_HEAD_PLAIN, TOOLS)
    assert plain[0].name == "replace_in_file"
    assert plain[0].arguments == {"path": "src/app.py", "old": "a = 1", "new": "a = 2"}

    mixed = salvage_dsml(LOST_HEAD_MIXED, TOOLS)
    assert mixed[0].name == "run_shell"
    assert mixed[0].arguments == {"command": "cd /repo && pytest -q", "description": "Run tests", "shell": "bash"}


def test_the_whole_form_is_read_too():
    calls = salvage_dsml(WHOLE, TOOLS)
    assert [(c.name, c.arguments) for c in calls] == [("read_file", {"path": "README.md"})]


def test_several_invokes_in_one_reply_are_several_calls():
    text = (
        f'<{D}invoke name="read_file"><{D}parameter name="path" string="true">a.md</{D}parameter></{D}invoke>'
        f'<{D}invoke name="read_file"><{D}parameter name="path" string="true">b.md</{D}parameter></{D}invoke>'
    )
    assert [c.arguments["path"] for c in salvage_dsml(text, TOOLS)] == ["a.md", "b.md"]


def test_a_json_parameter_is_decoded_and_a_string_one_is_not():
    text = (
        f'<{D}invoke name="run_shell">'
        f'<{D}parameter name="command" string="true">sleep 1</{D}parameter>'
        f'<{D}parameter name="timeout" string="false">30</{D}parameter>'
        f"</{D}invoke>"
    )
    call = salvage_dsml(text, TOOLS)[0]
    assert call.arguments == {"command": "sleep 1", "timeout": 30}
    quoted = text.replace('string="false">30', 'string="true">30')
    assert salvage_dsml(quoted, TOOLS)[0].arguments["timeout"] == "30"


def test_the_whole_form_with_a_tool_calls_wrapper_and_an_mcp_tool_name():
    # OpenRouter's replies: the wrapper is `tool_calls`, two invokes of one MCP tool, every tag whole.
    tools = [_tool("mcp__jira__getVisibleJiraProjects", "cloudId", "searchString")]
    nl = chr(10)
    text = nl.join(
        [
            "Looking for the project.",
            f"<{D}tool_calls>",
            f'<{D}invoke name="mcp__jira__getVisibleJiraProjects">',
            f'<{D}parameter name="cloudId" string="true">cloud-1</{D}parameter>',
            f'<{D}parameter name="searchString" string="true">ops</{D}parameter>',
            f"</{D}invoke>",
            f'<{D}invoke name="mcp__jira__getVisibleJiraProjects">',
            f'<{D}parameter name="cloudId" string="true">cloud-2</{D}parameter>',
            f"</{D}invoke>",
            f"</{D}tool_calls>",
        ]
    )
    calls = salvage_dsml(text, tools)
    assert [(c.name, c.arguments) for c in calls] == [
        ("mcp__jira__getVisibleJiraProjects", {"cloudId": "cloud-1", "searchString": "ops"}),
        ("mcp__jira__getVisibleJiraProjects", {"cloudId": "cloud-2"}),
    ]


# -- strict ----------------------------------------------------------------------------------


def test_an_unknown_tool_stays_text():
    assert salvage_dsml(LOST_HEAD_MIXED.replace("run_shell", "format_disk"), TOOLS) == []


def test_a_parameter_the_tool_does_not_declare_stays_text():
    assert salvage_dsml(LOST_HEAD_PLAIN.replace('name="old"', 'name="sudo"'), TOOLS) == []


def test_a_call_that_never_closes_is_a_call_that_was_cut_off():
    cut = LOST_HEAD_PLAIN.split("</invoke>")[0]
    assert salvage_dsml(cut, TOOLS) == []


def test_prose_that_mentions_the_tags_is_not_a_call():
    prose = 'In the prompt, `<parameter name="path">` marks a path and `</invoke>` ends the block.'
    assert salvage_dsml(prose, TOOLS) == []
    assert salvage_dsml("an ordinary answer", TOOLS) == []
    assert not looks_like_dsml("an ordinary answer")
    assert salvage_dsml(WHOLE, None) == [] and salvage_dsml(WHOLE, []) == []


def test_one_bad_name_among_good_calls_refuses_the_lot():
    text = WHOLE + WHOLE.replace("read_file", "rm_rf")
    assert salvage_dsml(text, TOOLS) == []


# -- through the provider's salvage ---------------------------------------------------------------


def test_the_providers_salvage_finds_it_and_clears_the_text():
    calls = _salvage_tool_calls_from_text(LOST_HEAD_MIXED, TOOLS)
    assert [c.name for c in calls] == ["run_shell"] and calls[0].id.startswith("call_salvaged_")
    text, found = _maybe_salvage_tool_calls(LOST_HEAD_SPACED, [], tools=TOOLS)
    assert text is None and [c.name for c in found] == ["replace_in_file"]
    # A reply that came with real calls is left alone, and so is plain text.
    text, found = _maybe_salvage_tool_calls("all done", [], tools=TOOLS)
    assert text == "all done" and found == []
