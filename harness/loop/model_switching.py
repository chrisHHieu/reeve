"""ModelSwitchMixin: changing the model or its thinking level in the middle of a conversation."""

from __future__ import annotations

from typing import Optional


class ModelSwitchMixin:
    def switch_model(self, model: str) -> Optional[str]:
        """Rebind the session's model mid-conversation (roadmap item 3).

        History is in the canonical OpenAI shape and every provider converts per call, so the switch
        is the field write plus a saved notice marking where it happened. The notice carries a
        warning when history holds images the new model cannot see, which are sent as placeholders
        (see `_outbound_messages`). It returns the notice text, or None when nothing changed (the
        same model, or the first binding on a fresh session)."""
        if not model or model == self.model:
            return None
        had_history = any(message.get("role") != "system" for message in self.messages)
        previous = self.model
        self.model = model
        # The context signal is the provider's count for the last request plus an estimate, adjusted
        # for bias, of what came after. Both are tokenizer-specific and a switch changes the
        # tokenizer. Left alone, a number measured on the old model can fire the new model's trigger
        # at once. This was seen live on 2026-09-23: a switch to a 128k-window model compacted while
        # the local estimate sat at 89k, because the last provider count came from the previous
        # model. Dropped, the signal falls back to a fresh estimate for the new model until its
        # first usage report calibrates it.
        self._last_context_tokens = None
        self._context_measured_at = len(self.messages)
        self._estimate_bias = None
        self._measured_view_tokens = None
        # The thinking patch belongs to a model. A level the new model has no body for is dropped,
        # and so is one its catalogue does not list: a "medium" carried onto a model offering
        # low and high was still sent, and the picker showed a level the new model has no button
        # for (owner-hit 2026-10-24). Dropped, the new model runs on its own default; the setting
        # is then applied again, so a switch cannot leave the old model's body behind.
        if self.thinking_level:
            from harness.llm.providers.variants import levels_for, patch_for

            offered = levels_for(model)
            if not patch_for(model, self.thinking_level) or (offered and self.thinking_level not in offered):
                self.thinking_level = None
        self._apply_thinking_level()
        # The edit tools follow the model: `apply_patch` is offered only to families
        # trained on its envelope.
        from harness.tools.edit_surface import sync_edit_surface

        sync_edit_surface(self)
        if not had_history:
            self._restate_model()
            return None
        from harness.llm.providers.matrix import model_labels

        text = f"Model switched to {model_labels().get(model, model)}"
        try:
            caps = self.provider.capabilities(model)
        except Exception:
            caps = None
        if caps is not None and not getattr(caps, "vision", False) and self._history_has_images():
            text += " — earlier images can't be read by this model"
        # The ids ride the notice, so the models a conversation ran on are read back from it after a
        # reload (`_restate_model`).
        self._append_notice("model_switch", text, model=model, previous=previous)
        self._restate_model()
        # The notice is history now, and the fresh baseline has to sit after it or the next signal
        # would count it again as an unmeasured delta.
        self._context_measured_at = len(self.messages)
        return text

    def _restate_model(self) -> None:
        """Name the model this conversation runs on now in the system prompt, with the ones that
        wrote its earlier replies (`context/model_line.py`). The new prompt is logged with the next
        request (`_note_tool_changes`); the new model has no cache for it to cost."""
        if not self.messages or self.messages[0].get("role") != "system":
            return
        from harness.context.model_line import with_model_line

        earlier = [
            str(message.get("previous"))
            for message in self.messages
            if message.get("role") == "notice" and message.get("kind") == "model_switch" and message.get("previous")
        ]
        content = self.messages[0].get("content")
        if not isinstance(content, str) or not self.model:
            return
        restated = with_model_line(content, self.model, earlier)
        if restated != content:
            self.messages[0] = {**self.messages[0], "content": restated}

    def _apply_thinking_level(self) -> None:
        """Work out `model_settings` again from the base plus the chosen level's patch. This is
        outside the round-trip loop on purpose: the patch is a plain dict merge, so nothing here can
        fail a turn, and an unknown level adds nothing (the UI offers only levels
        `variants.levels_for` returned)."""
        settings = dict(self._base_model_settings)
        if self.thinking_level:
            from harness.llm.providers.variants import patch_for

            settings = {**patch_for(self.model, self.thinking_level), **settings}
        self.model_settings = settings

    def switch_thinking(self, level: Optional[str]) -> Optional[str]:
        """Rebind the session's thinking level mid-conversation. It returns a notice text, or None
        when nothing changed.

        A switch changes the request bytes, so it costs the prompt cache, which is the one real cost
        of the feature. The level is a named level (`low`, `high`) and not a raw settings dict, so the
        mapping to a provider stays in `providers/variants.py` and the session stores something a UI
        can show."""
        level = (level or "").strip() or None
        if level == "default":
            level = None  # the picker shows this; it is stored as "no override"
        if level == self.thinking_level:
            return None
        had_history = any(message.get("role") != "system" for message in self.messages)
        self.thinking_level = level
        self._apply_thinking_level()
        if not had_history:
            return None
        if level:
            from harness.llm.providers.variants import levels_for

            if level not in levels_for(self.model):
                text = f"{self.model} doesn't offer a '{level}' thinking level"
            else:
                text = f"Thinking set to {level}"
        else:
            text = "Thinking reset to the model default"
        self._append_notice("thinking_switch", text)
        return text

    def _history_has_images(self) -> bool:
        return any(
            isinstance(part, dict) and part.get("type") == "image_url"
            for message in self.messages
            if isinstance(message.get("content"), list)
            for part in message["content"]
        )
