"""The cua server's element-click guards: accessibility patterns, stale generations."""

from __future__ import annotations

import pytest

from harness.integrations import cua_server

pytestmark = pytest.mark.skipif(
    cua_server.uia is None or not cua_server._HAS_GUI,
    reason="needs Windows UI Automation and pyautogui",
)


class FakePattern:
    def __init__(self, result: bool = True):
        self.result = result
        self.called = False

    def Invoke(self, *args, **kwargs):
        self.called = True
        return self.result

    def Select(self, *args, **kwargs):
        self.called = True
        return self.result

    @property
    def DefaultAction(self):
        return ""

    def DoDefaultAction(self, *args, **kwargs):
        self.called = True
        return self.result


class FakeValue:
    def __init__(self, value: str):
        self.Value = value


class FakeControl:
    def __init__(self, kind: str, patterns: dict | None = None):
        self.ControlTypeName = kind
        self.patterns = patterns or {}

    def GetPattern(self, pattern_id):
        return self.patterns.get(pattern_id)


@pytest.fixture(autouse=True)
def clean_tree():
    saved = dict(cua_server._LAST_TREE)
    yield
    cua_server._LAST_TREE.clear()
    cua_server._LAST_TREE.update(saved)


@pytest.fixture(autouse=True)
def no_real_clicks(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(
        cua_server,
        "pyautogui",
        type("P", (), {"click": staticmethod(lambda *a, **k: calls.append((a, k)))})(),
    )
    return calls


def _tree(controls: dict, *, generation: int = 1, foreground: bool | None = True):
    cua_server._LAST_TREE.clear()
    cua_server._LAST_TREE.update(
        {
            "items": [
                {"index": index, "type": "Button", "name": f"E{index}", "rect": [0, 0, 10, 10], "enabled": True}
                for index in controls
            ],
            "controls": controls,
            "title": "Test",
            "pid": 1,
            "generation": generation,
            "foreground": foreground,
        }
    )


def test_buttons_invoke_and_list_items_select():
    invoke = FakePattern()
    select = FakePattern()
    _tree(
        {
            0: FakeControl("ButtonControl", {cua_server.uia.PatternId.InvokePattern: invoke}),
            1: FakeControl(
                "ListItemControl",
                {
                    cua_server.uia.PatternId.InvokePattern: FakePattern(),
                    cua_server.uia.PatternId.SelectionItemPattern: select,
                },
            ),
        }
    )
    assert cua_server._invoke_element(0) == "invoke" and invoke.called
    # A list item is a selection, not an invocation: single-click semantics.
    assert cua_server._invoke_element(1) == "select" and select.called
    assert cua_server._invoke_element(9) is None


def test_a_control_without_patterns_has_no_native_action():
    _tree({0: FakeControl("ButtonControl")})
    assert cua_server._invoke_element(0) is None


def test_click_refuses_a_stale_generation_before_any_action():
    invoke = FakePattern()
    _tree({0: FakeControl("ButtonControl", {cua_server.uia.PatternId.InvokePattern: invoke})}, generation=5)
    out = cua_server.click(index=0, generation=4)
    assert "generation 4 -> 5" in out["error"] and not invoke.called

    # The matching generation goes through the element's own action, no coordinates.
    assert cua_server.click(index=0, generation=5) == {"ok": True, "via": "invoke", "index": 0}
    assert invoke.called


def test_a_patternless_element_on_a_background_window_is_refused(no_real_clicks):
    _tree({0: FakeControl("TextControl")}, foreground=False)
    out = cua_server.click(index=0)
    assert "not in the foreground" in out["error"] and no_real_clicks == []

    # Unknown foreground (the platform could not say) keeps the old fail-open path.
    _tree({0: FakeControl("TextControl")}, foreground=None)
    out = cua_server.click(index=0)
    assert out.get("via") == "coordinates" and no_real_clicks


@pytest.mark.skipif(not cua_server._HAS_CLIP, reason="needs pyperclip")
def test_type_text_refuses_a_password_field(monkeypatch):
    typed: list[str] = []

    class FakeFocus:
        IsPassword = True

    class FakeClip:
        @staticmethod
        def paste():
            return ""

        @staticmethod
        def copy(text):
            typed.append(text)

    monkeypatch.setattr(cua_server.uia, "GetFocusedControl", lambda: FakeFocus())
    monkeypatch.setattr(cua_server, "pyperclip", FakeClip)
    out = cua_server.type_text("hunter2")
    assert "password" in out["error"] and typed == []


@pytest.mark.skipif(not cua_server._HAS_CLIP, reason="needs pyperclip")
def test_type_text_types_into_an_ordinary_field(monkeypatch):
    typed: list[str] = []
    hotkeys: list[tuple] = []

    class FakeFocus:
        IsPassword = False
        Name = "Search"
        patterns = {cua_server.uia.PatternId.ValuePattern: FakeValue("xin chào")}

        def GetPattern(self, pattern_id):
            return self.patterns.get(pattern_id)

    class FakeClip:
        @staticmethod
        def paste():
            return "old"

        @staticmethod
        def copy(text):
            typed.append(text)

    class FakeGui:
        @staticmethod
        def hotkey(*keys):
            hotkeys.append(keys)

    monkeypatch.setattr(cua_server.uia, "GetFocusedControl", lambda: FakeFocus())
    monkeypatch.setattr(cua_server, "pyperclip", FakeClip)
    monkeypatch.setattr(cua_server, "pyautogui", FakeGui)
    out = cua_server.type_text("xin chào")
    # The read-back verifies the text landed, and names the field for the caller.
    assert out == {"ok": True, "typed": len("xin chào"), "field": "Search", "verified": True}
    # The text rides the clipboard, then the previous value is restored.
    assert typed == ["xin chào", "old"] and hotkeys


@pytest.mark.skipif(not cua_server._HAS_CLIP, reason="needs pyperclip")
def test_type_text_reports_a_read_back_mismatch_or_no_value(monkeypatch):
    class FakeClip:
        @staticmethod
        def paste():
            return ""

        @staticmethod
        def copy(text):
            pass

    class FakeGui:
        @staticmethod
        def hotkey(*keys):
            pass

    class WrongFocus:
        IsPassword = False
        Name = "Search"
        patterns = {cua_server.uia.PatternId.ValuePattern: FakeValue("something else")}

        def GetPattern(self, pattern_id):
            return self.patterns.get(pattern_id)

    class ValueLessFocus:
        IsPassword = False
        Name = "Terminal"
        patterns: dict = {}

        def GetPattern(self, pattern_id):
            return self.patterns.get(pattern_id)

    monkeypatch.setattr(cua_server, "pyperclip", FakeClip)
    monkeypatch.setattr(cua_server, "pyautogui", FakeGui)

    monkeypatch.setattr(cua_server.uia, "GetFocusedControl", lambda: WrongFocus())
    assert cua_server.type_text("xin chào")["verified"] is False

    # A control with no value pattern cannot be judged — null, never a false alarm.
    monkeypatch.setattr(cua_server.uia, "GetFocusedControl", lambda: ValueLessFocus())
    out = cua_server.type_text("xin chào")
    assert out["verified"] is None and out["field"] == "Terminal"


def test_a_coordinate_fallback_clicks_the_middle_of_the_element(no_real_clicks):
    """`rect` is [left, top, right, bottom]. The centre was `left + right // 2`, which
    only equals the midpoint when left is 0 — the fixture above, so no test saw it."""
    _tree({0: FakeControl("TextControl")}, foreground=True)
    cua_server._LAST_TREE["items"][0]["rect"] = [100, 200, 300, 260]
    out = cua_server.click(index=0)
    assert out["via"] == "coordinates" and (out["x"], out["y"]) == (200, 230)
    assert no_real_clicks[0][0] == (200, 230)


def test_the_foreground_a_read_reports_is_the_one_click_enforces(monkeypatch, no_real_clicks):
    """Through the real `get_app_state`, not a seeded tree: the read returned
    `foreground` but never stored it, so `click` always saw None and the refusal of a
    coordinate click on a covered window could not fire."""
    from types import SimpleNamespace

    class Window:
        Name = "Probe window"
        ControlTypeName = "WindowControl"
        ProcessId = 4242
        NativeWindowHandle = 42
        IsEnabled = True
        BoundingRectangle = SimpleNamespace(left=0, top=0, right=800, bottom=600)

        def GetChildren(self):
            return [Label()]

        def GetTopLevelControl(self):
            return self

        def Exists(self, *args, **kwargs):
            return True

    class Label(FakeControl):
        Name = "Plain text"
        IsEnabled = True
        BoundingRectangle = SimpleNamespace(left=10, top=10, right=110, bottom=30)

        def __init__(self):
            super().__init__("TextControl")

        def GetChildren(self):
            return []

    fake_uia = SimpleNamespace(
        GetFocusedControl=lambda: Window(),
        GetForegroundWindow=lambda: 7,  # another window is in front
        PatternId=cua_server.uia.PatternId,
    )
    monkeypatch.setattr(cua_server, "uia", fake_uia)
    state = cua_server.get_app_state()
    assert state["foreground"] is False
    text_index = next(e["index"] for e in state["elements"] if e["name"] == "Plain text")
    out = cua_server.click(index=text_index, generation=state["generation"])
    assert "not in the foreground" in out.get("error", "") and no_real_clicks == []
