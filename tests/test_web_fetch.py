"""`web_fetch` hardening: streamed byte cap, HTML depth guard, image/PDF attachments,
HTTP-status results, and the model-settable timeout.

The transport is bounded on every axis a hostile page could exploit; these tests pin the
bounds and the two attachment routes (an image the model can see, a PDF the model receives
natively or as a local text extraction via pdf_support at send time).
"""

from harness.integrations.web import fetch as fetch_mod

_PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
_PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n"


class _Resp:
    """The httpx-shaped response the streaming path reads (chunks, then close)."""

    def __init__(
        self,
        status: int = 200,
        body: bytes = b"",
        ctype: str = "text/html; charset=utf-8",
        chunks: list[bytes] | None = None,
    ) -> None:
        self.status_code = status
        self.content = body
        self.headers = {"content-type": ctype}
        self.encoding = "utf-8"
        self.extensions: dict = {}
        self._chunks = chunks
        self.closed = False

    def iter_bytes(self):
        yield from (self._chunks if self._chunks is not None else [self.content])

    def close(self) -> None:
        self.closed = True


def _run(monkeypatch, resp, url: str = "https://example.com/x", **kwargs):
    monkeypatch.setattr(fetch_mod, "get_checked", lambda client, u, **kw: resp)
    args = {"max_chars": 1000}
    args.update(kwargs)
    return fetch_mod.make_web_fetch_tool()(url, **args)


# -- status ------------------------------------------------------------------------


def test_status_code_is_reported_and_the_response_is_closed(monkeypatch):
    resp = _Resp(200, b"<p>ok</p>")
    out = _run(monkeypatch, resp)
    assert out["status_code"] == 200 and "ok" in out["text"]
    assert resp.closed is True


def test_non_2xx_is_a_result_not_an_error(monkeypatch):
    """The rule: a 404 page or a 403 that names the block reason is data the model can
    act on. The status is always in the result, so nothing is claimed by omission."""
    out = _run(monkeypatch, _Resp(404, b"gone", ctype="text/plain; charset=utf-8"))
    assert out["status_code"] == 404
    assert "gone" in out["text"] and "error" not in out


def test_render_carries_the_http_status():
    text = fetch_mod._web_fetch_render(
        {}, {"url": "https://x", "status_code": 404, "text": "gone", "content_type": "text/plain"}
    )
    assert "HTTP 404" in text
    assert "HTTP 403" in fetch_mod._web_fetch_render({}, {"error": "nope", "status_code": 403})


# -- transport caps ----------------------------------------------------------------


def test_body_is_capped_while_streaming(monkeypatch):
    monkeypatch.setattr(fetch_mod, "_MAX_BYTES", 10)
    resp = _Resp(200, ctype="text/plain; charset=utf-8", chunks=[b"a" * 8, b"b" * 8])
    out = _run(monkeypatch, resp)
    assert out["bytes_truncated"] is True
    assert out["text"] == "a" * 8 + "b" * 2


def test_read_capped_keeps_an_exactly_at_cap_body_intact():
    resp = _Resp(200, ctype="text/plain", chunks=[b"a" * 4, b"b" * 6])
    raw, truncated = fetch_mod._read_capped(resp, 10)
    assert raw == b"a" * 4 + b"b" * 6 and truncated is False


# -- HTML depth guard --------------------------------------------------------------


def test_deeply_nested_html_is_omitted_not_converted():
    assert fetch_mod._html_to_markdown("<div>" * 600 + "x") == fetch_mod._HTML_OMITTED
    assert fetch_mod._html_to_markdown("<div><p>fine</p></div>").strip().endswith("fine")


def test_markup_inside_script_does_not_count_as_nesting():
    html = "<script>" + "<div>" * 600 + "</script>hi"
    assert fetch_mod._HTML_OMITTED not in fetch_mod._html_to_markdown(html)


# -- attachments -------------------------------------------------------------------


def test_an_image_is_attached_as_a_vision_part(monkeypatch):
    out = _run(monkeypatch, _Resp(200, _PNG, ctype="image/png"))
    assert out["format"] == "image" and out["truncated"] is False
    part = out["_attach"][0]
    assert part["type"] == "image_url"
    assert part["image_url"]["url"].startswith("data:image/png;base64,")
    assert "attached" in out["text"]


def test_an_oversized_image_is_refused_not_attached(monkeypatch):
    monkeypatch.setattr(fetch_mod, "_MAX_IMAGE_BYTES", 4)
    out = _run(monkeypatch, _Resp(200, b"12345", ctype="image/png"))
    assert "error" in out and "_attach" not in out


def test_a_pdf_is_attached_as_a_file_part(monkeypatch):
    out = _run(
        monkeypatch,
        _Resp(200, _PDF, ctype="application/pdf"),
        url="https://example.com/paper.pdf",
    )
    part = out["_attach"][0]
    assert part["type"] == "file"
    assert part["file"]["filename"] == "paper.pdf"
    assert part["file"]["file_data"].startswith("data:application/pdf;base64,")


def test_a_pdf_served_as_octet_stream_is_recognised(monkeypatch):
    out = _run(
        monkeypatch,
        _Resp(200, _PDF, ctype="application/octet-stream"),
        url="https://example.com/dl",
    )
    assert out["format"] == "pdf" and out["_attach"][0]["type"] == "file"


def test_a_binary_non_2xx_reports_the_status_without_attaching(monkeypatch):
    out = _run(monkeypatch, _Resp(404, b"", ctype="image/png"))
    assert out["error"] and out["status_code"] == 404 and "_attach" not in out


# -- timeout -----------------------------------------------------------------------


def test_timeout_is_clamped_to_the_tool_contract():
    assert fetch_mod._clamp_timeout(None) == fetch_mod._DEFAULT_TIMEOUT
    assert fetch_mod._clamp_timeout(0) == fetch_mod._DEFAULT_TIMEOUT
    assert fetch_mod._clamp_timeout(float("nan")) == fetch_mod._DEFAULT_TIMEOUT
    assert fetch_mod._clamp_timeout(7) == 7.0
    assert fetch_mod._clamp_timeout("15") == 15.0
    assert fetch_mod._clamp_timeout(999) == fetch_mod._MAX_TIMEOUT
