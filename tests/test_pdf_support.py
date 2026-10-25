"""PDFs for models that cannot read one (`llm/pdf_support`): which models can, what a PDF holds, its
pages drawn as images, and the user message rewritten for a model without PDF support, as text or,
for one that sees, as page images.
"""

from __future__ import annotations

import base64
import io
import struct
import zlib

import pytest

from harness.llm import pdf_support
from harness.llm.providers.base import ModelCapabilities
from harness.llm.providers.capabilities import capabilities_for

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
NO_PDF = ModelCapabilities(vision=False, pdf=False)


def _blank_pdf_url(pages: int = 3) -> str:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=300)
    pdf = io.BytesIO()
    writer.write(pdf)
    return "data:application/pdf;base64," + base64.b64encode(pdf.getvalue()).decode()


def _attached(url: str) -> dict:
    return {"type": "file", "file": {"filename": "doc.pdf", "file_data": url}}


@pytest.fixture(autouse=True)
def _text_fallback_each_time():
    pdf_support.set_fallback_mode("text")
    yield
    pdf_support.set_fallback_mode("text")


@pytest.mark.parametrize(
    ("model", "reads_pdf"),
    [
        ("gpt-5.6-sol", True),
        ("anthropic:claude-fable-5", True),
        ("gemini:gemini-2.5-pro", True),
        ("zai:glm-5.2", False),
        ("kimi:kimi-k2.6", False),
        ("together:zai-org/GLM-5.2", False),
        ("ollama:qwen3", False),
    ],
)
def test_the_three_native_providers_read_pdfs_and_compatible_vendors_do_not(model, reads_pdf):
    assert bool(capabilities_for(model).pdf) is reads_pdf


def test_inspecting_says_how_many_pages_and_bytes_and_refuses_what_is_not_a_pdf():
    looked = pdf_support.inspect(_blank_pdf_url(pages=4))
    assert looked["ok"] and looked["pages"] == 4 and looked["bytes"] > 0
    for other in ("data:image/png;base64,zz", "plain text"):
        assert not pdf_support.inspect(other)["ok"]


def test_drawn_pages_are_whole_pngs_at_twice_the_page_size():
    drawn = pdf_support.rasterize(_blank_pdf_url(pages=3), max_pages=2)
    assert drawn is not None and len(drawn) == 2
    png = base64.b64decode(drawn[0].split(",", 1)[1])
    assert png[:8] == PNG_SIGNATURE
    width, height = struct.unpack(">II", png[16:24])
    assert (width, height) == (400, 600)  # 200x300 at RASTER_SCALE 2
    channels = 4 if png[25] == 6 else 3
    data_at = png.find(b"IDAT")
    (length,) = struct.unpack(">I", png[data_at - 4 : data_at])
    assert len(zlib.decompress(png[data_at + 4 : data_at + 4 + length])) == height * (1 + width * channels)  # every row is there


def test_a_pdf_with_no_text_is_replaced_by_a_note_the_model_can_see():
    [note] = pdf_support.adapt_content([_attached(_blank_pdf_url())], NO_PDF)
    assert note["type"] == "text" and "no extractable text" in note["text"]


def test_page_images_go_only_to_a_model_that_sees():
    url = _blank_pdf_url(pages=2)
    pdf_support.set_fallback_mode("images")
    assert [part["type"] for part in pdf_support.adapt_content([_attached(url)], ModelCapabilities(vision=True, pdf=False))] == ["text", "image_url", "image_url"]
    assert all(part["type"] == "text" for part in pdf_support.adapt_content([_attached(url)], NO_PDF))  # text for one that does not


def test_the_rest_of_the_message_is_left_as_it_was():
    adapted = pdf_support.adapt_content([{"type": "text", "text": "hi"}, _attached(_blank_pdf_url())], NO_PDF)
    assert adapted[0] == {"type": "text", "text": "hi"} and len(adapted) == 2


def test_an_unknown_fallback_is_taken_as_text():
    assert pdf_support.set_fallback_mode("images") == "images"
    assert pdf_support.set_fallback_mode("bogus") == "text"
