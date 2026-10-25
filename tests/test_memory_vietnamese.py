"""Vietnamese, the way people type it: without tone marks. `fold` strips what Vietnamese
orthography adds to Latin letters (tone marks, and đ→d, which no Unicode decomposition
does), so a query typed as "mat khau duoc" finds "Mật khẩu được" in search_history and
memory names stay readable ASCII slugs."""

from __future__ import annotations

from harness.context.memory.memory_format import fold
from harness.tools.history import HistorySearch


def test_fold_strips_tone_marks_and_maps_d():
    assert fold("Hồ Sơ Thuế") == "ho so thue"
    assert fold("Mật khẩu được lưu") == "mat khau duoc luu"
    assert fold("deploy to Vercel") == "deploy to vercel"


def test_history_finds_diacritic_less_queries_and_quotes_verbatim(tmp_path):
    from harness.session.conversations import ConversationStore
    from harness.session.sessions import SessionRecord

    store = ConversationStore(tmp_path)
    store.save(
        SessionRecord(
            session_id="s1",
            workspace="w",
            model="m",
            mode="interactive",
            messages=[
                {"role": "user", "content": "Mật khẩu được lưu ở đâu nhỉ?"},
                {"role": "assistant", "content": "Mật khẩu được lưu trong 1Password."},
            ],
            title="vn",
        )
    )
    hs = HistorySearch(store, session_id="s1", workspace="w")
    result = hs.search("mat khau duoc")
    assert result["hits"], "a diacritic-less query must still find the passage"
    # The quote is what was said, accents intact — matching folds, quoting does not.
    assert "Mật khẩu được lưu trong 1Password." in result["hits"][0]["said"]
