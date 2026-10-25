"""WebSearchSettingsMixin — Settings ▸ Web search: where searches go, the key, and a test.

The `web_search` tool resolves its provider on every call (`web/tool.py: resolve_provider`), so
what is saved here applies to the next search of every session, with no restart. Three things
a person needs and the old pair of methods did not give:

  · WHERE THE KEY COMES FROM. A search may use a key saved here, an `EXA_API_KEY` in the
    environment, or the one the Exa connector card stored (`exa:default`) — the same order
    `_provider_key` reads them in. Saying which is the difference between "I removed my key"
    and "searches still bill my account".
  · SAVING THE PROVIDER KEEPS THE KEY. `set_web_search(provider)` used to write `{provider}`
    over the whole profile, so switching to "DuckDuckGo only" and back silently threw the
    key away. A key now changes only when one is given, or when removal is asked for.
  · A TEST that runs one real search through the exact chain a session would use and says
    who answered: a rejected key does not fail a search, it falls through to DuckDuckGo, so
    without this a wrong key looks like a working one.

Tavily is the third choice, an engine and not a connector, so its key is saved
here beside Exa's (`tavily_api_key`). A key is checked with Tavily when it is saved (the usage
endpoint is free), it can be chosen only once it has a key, and removing its key sends
searches back to Exa. The screen is told about its key (`tavily`) and where a search really
starts (`engine`).

Never returns a key, in any field or error.
"""
from __future__ import annotations

import os
import time
from typing import Any, Optional


#: The choices a person makes. `auto` is the shipped chain (Exa, then DuckDuckGo when Exa
#: errs or finds nothing); `tavily` is Tavily then DuckDuckGo, on the Tavily key saved here;
#: `duckduckgo` sends searches nowhere else.
WEB_SEARCH_MODES = ("auto", "tavily", "duckduckgo")
#: The query a test runs when none is given: current enough that an empty answer means
#: something is wrong, not that the topic is obscure.
DEFAULT_TEST_QUERY = "latest technology news"


class WebSearchSettingsMixin:
    def _web_search_profile(self) -> dict[str, Any]:
        return dict(self.secrets.get("web_search:default") or {})

    def get_web_search(self) -> dict[str, Any]:
        from harness.integrations.web import provider_names, search_engine, tavily_key
        from harness.integrations.web.tool import provider_name

        profile = self._web_search_profile()
        configured = str(profile.get("provider") or "").strip()
        chosen = configured.lower()
        if profile.get("api_key"):
            source: Optional[str] = "settings"
        elif os.environ.get("EXA_API_KEY"):
            source = "environment"
        elif (self.secrets.get("exa:default") or {}).get("api_key"):
            source = "connector"
        else:
            source = None
        return {
            # What a search will actually try: the pinned provider, or the shipped chain.
            "provider": configured or provider_name(self.secrets),
            "mode": chosen if chosen in ("duckduckgo", "tavily") else "auto",
            # Where a search starts: Tavily chosen with no key runs Exa.
            "engine": search_engine(self.secrets),
            "tavily": {
                "has_key": bool(profile.get("tavily_api_key")),
                "key_source": "settings"
                if profile.get("tavily_api_key")
                else ("environment" if tavily_key(self.secrets) else None),
                "key_set_at": profile.get("tavily_key_set_at"),
            },
            # The live destination, as the approval card names it.
            "destination": provider_name(self.secrets),
            "has_key": bool(profile.get("api_key")),
            "key_source": source,
            "key_set_at": profile.get("key_set_at"),
            "providers": provider_names(),
        }

    def set_web_search(
        self,
        provider: str,
        api_key: Optional[str] = None,
        *,
        remove_key: bool = False,
        tavily_key: Optional[str] = None,
        remove_tavily_key: bool = False,
    ) -> dict[str, Any]:
        from harness.integrations.web import provider_names
        from harness.integrations.web import tavily_key as saved_tavily_key
        from harness.integrations.web.tavily import check_key

        provider = str(provider or "").strip().lower()
        if provider == "auto":
            provider = "exa"  # the chain is what "exa" resolves to
        if provider not in provider_names():
            return {"ok": False, "error": f"unknown provider: {provider}"}
        new_tavily = str(tavily_key or "").strip()
        if new_tavily:
            refused = check_key(new_tavily)
            if refused:
                return {"ok": False, "error": refused}
        if remove_tavily_key and provider == "tavily":
            provider = "exa"  # without its key Tavily cannot answer: searches go back to Exa
        if provider == "tavily" and not (new_tavily or saved_tavily_key(self.secrets)):
            return {"ok": False, "error": "Add a Tavily API key first."}
        before = self.get_web_search()["provider"]
        profile = self._web_search_profile()
        profile["provider"] = provider
        key = str(api_key or "").strip()
        if key:
            profile["api_key"] = key
            profile["key_set_at"] = time.strftime("%Y-%m-%d")
        elif remove_key:
            profile.pop("api_key", None)
            profile.pop("key_set_at", None)
        if new_tavily:
            profile["tavily_api_key"] = new_tavily
            profile["tavily_key_set_at"] = time.strftime("%Y-%m-%d")
        elif remove_tavily_key:
            profile.pop("tavily_api_key", None)
            profile.pop("tavily_key_set_at", None)
        self.secrets.put("web_search:default", profile)
        # "Always allow searches this session" is consent to a NAMED destination —
        # the card says which provider the queries go to. A new provider is a new
        # destination, so every live session's grant dies with the old one. (Scheduled
        # tasks that name-allow web_search are unaffected: their approver re-allows.)
        if provider != before:
            for engine in self._engines.values():
                engine.permissions.session_allow_tools.discard("web_search")
        return {"ok": True, "provider": provider}

    def test_web_search(self, query: Optional[str] = None) -> dict[str, Any]:
        """One real search through the chain a session would use, and who answered.

        Blocking (two HTTP calls at worst, Exa's 15 s and DuckDuckGo's 20 s): the route runs it
        off the loop. The answer names the service that answered and, when the chain fell
        through, the one that did not and why — which is how a rejected key shows itself.
        Errors are sentences with the key's value nowhere in them.
        """
        from harness.integrations.web.tool import resolve_provider

        wanted = " ".join(str(query or "").split())[:200] or DEFAULT_TEST_QUERY
        started = time.monotonic()
        try:
            provider = resolve_provider(self.secrets)
            results = provider.search(wanted, 3)
        except Exception as exc:  # noqa: BLE001 — a test reports, it does not raise
            return {
                "ok": False,
                "query": wanted,
                "error": self._without_key(f"The search failed: {exc}"),
                "ms": int((time.monotonic() - started) * 1000),
            }
        answered = str(getattr(provider, "last_used", getattr(provider, "name", "")) or "")
        out: dict[str, Any] = {
            "ok": bool(results),
            "query": wanted,
            "provider": answered,
            "results": len(results),
            "ms": int((time.monotonic() - started) * 1000),
        }
        if results:
            out["first"] = {"title": results[0].title[:160], "url": results[0].url[:300]}
        else:
            out["error"] = "No results came back."
        if getattr(provider, "fell_back", False):
            primary = getattr(getattr(provider, "primary", None), "name", "")
            out["fallback_from"] = str(primary)
            out["fallback_reason"] = self._without_key(
                str(getattr(provider, "fallback_reason", ""))
            )[:300]
        return out

    def _without_key(self, text: str) -> str:
        """`text` with any key this store holds for search blanked out."""
        for profile, field in (
            ("web_search:default", "api_key"),
            ("web_search:default", "tavily_api_key"),
            ("exa:default", "api_key"),
        ):
            key = str((self.secrets.get(profile) or {}).get(field) or "")
            if len(key) >= 6:
                text = text.replace(key, "•••")
        for name in ("EXA_API_KEY", "TAVILY_API_KEY"):
            env = os.environ.get(name) or ""
            if len(env) >= 6:
                text = text.replace(env, "•••")
        return text
