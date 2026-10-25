"""The flagship models (rolled out 2026-07-14): GPT-5.6 Sol, Terra and Luna and Claude Fable 5 are in
the matrix, a flagship is the default, and an account that cannot use one is told so in words (GPT-5.6
reaches organizations one at a time, and quota or credits can run out on any model).
"""

import pytest

from harness.config.config import Config
from harness.llm.providers.errors import friendly_model_error
from harness.llm.providers.matrix import MATRIX, models_for_provider

SOL, FABLE = "gpt-5.6-sol", "anthropic:claude-fable-5"


@pytest.mark.parametrize(
    ("model", "label"),
    [(SOL, "GPT-5.6 Sol · OpenAI"), ("gpt-5.6-terra", "GPT-5.6 Terra · OpenAI"), ("gpt-5.6-luna", "GPT-5.6 Luna · OpenAI"), (FABLE, "Claude Fable 5 · Anthropic")],
)
def test_each_flagship_is_listed_with_its_label_tools_and_vision(model, label):
    entry = MATRIX[model]
    assert entry.label == label and entry.caps.tools and entry.caps.vision


def test_the_flagships_are_offered_by_their_providers_and_sol_is_the_default():
    assert SOL in models_for_provider("openai") and "claude-fable-5" in models_for_provider("anthropic")
    assert Config().model == SOL


def _said(model, error_text):
    return friendly_model_error(model, RuntimeError(error_text))


@pytest.mark.parametrize(
    ("model", "error_text", "words"),
    [
        # OpenAI's answer for a model the organization cannot use yet
        (SOL, "Error code: 404 - {'error': {'code': 'model_not_found', 'message': 'The model `gpt-5.6-sol` does not exist or you do not have access to it.'}}", "doesn't have access to gpt-5.6-sol"),
        # Anthropic's is a not_found_error naming the model
        (FABLE, "Error code: 404 - {'type': 'error', 'error': {'type': 'not_found_error', 'message': 'model: claude-fable-5'}}", f"doesn't have access to {FABLE}"),
        (SOL, "Error code: 429 - {'error': {'code': 'insufficient_quota', 'message': 'You exceeded your current quota, please check your plan and billing details.'}}", "out of quota for gpt-5.6-sol"),
        (FABLE, "Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'Your credit balance is too low to access the Anthropic API.'}}", "out of quota"),
    ],
)
def test_no_access_and_no_quota_are_said_in_words(model, error_text, words):
    said = _said(model, error_text)
    assert said and words in said


@pytest.mark.parametrize(
    "error_text",
    [
        "Error code: 429 - rate_limit_exceeded, retry after 2s",  # a plain rate limit, with no quota code
        "Error code: 404 - no route /v2/chat",  # a wrong base_url, not a question of access
        "connection reset by peer",
    ],
)
def test_any_other_error_is_left_as_it_came(error_text):
    assert _said(SOL, error_text) is None
