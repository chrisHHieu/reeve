"""What a model provider is, as the Settings form and the router see it."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..base import ProviderClient

DEFAULT_OLLAMA_URL = "http://localhost:11434"


@dataclass(frozen=True)
class ProviderField:
    """One input of a provider's settings form."""

    key: str
    label: str
    secret: bool = False
    required: bool = True
    help: str = ""
    placeholder: str = ""
    #: Filled in but still editable, for example a vendor's official endpoint so the person only
    #: pastes a key. A placeholder is the grey hint and this is a real value.
    default: str = ""
    #: When non-empty the field is drawn as a segmented choice instead of a text box. Each
    #: option is `{"value", "label"}` plus optional `"tag"` (a small badge such as "Easiest"),
    #: `"desc"` (a line above the method's panel) and `"command"` (a copyable terminal command
    #: shown in it). The chosen value is stored like any other field.
    choices: tuple = ()
    #: `{"other_field_key": "value"}`: the field is drawn only while that other field holds that
    #: value. This is how a form switches between authentication methods.
    show_when: Optional[dict] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "secret": self.secret,
            "required": self.required,
            "help": self.help,
            "placeholder": self.placeholder,
            "default": self.default,
            "choices": [dict(choice) for choice in self.choices],
            "show_when": self.show_when,
        }


@dataclass(frozen=True)
class ProviderDescriptor:
    """A model provider: the fields of its form, and how to build its client."""

    name: str
    title: str
    needs_key: bool
    fields: list[ProviderField]
    build: Callable[[dict[str, Any], Any], ProviderClient] = field(repr=False)
    #: The environment variable that can supply the key (`ANTHROPIC_API_KEY`, say).
    env_key: Optional[str] = None
    #: A line under the title, such as "Connects through X's OpenAI-compatible API".
    blurb: str = ""
    #: `"oauth"` means there is no key form: a browser sign-in configures the provider (the
    #: tokens go in its `provider:<name>` profile) and the app draws connect and sign-out. None
    #: is the usual form.
    auth: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "needs_key": self.needs_key,
            "fields": [f.to_dict() for f in self.fields],
            "blurb": self.blurb,
            "auth": self.auth,
        }


def normalize_ollama_url(url: Optional[str]) -> str:
    """An OpenAI-compatible base URL from `http://host:11434` or `.../v1`. Ollama serves that
    API under `/v1` and its own API at the root, so the target is always `<root>/v1`."""
    base = (url or DEFAULT_OLLAMA_URL).strip().rstrip("/") or DEFAULT_OLLAMA_URL
    return base if base.endswith("/v1") else base + "/v1"
