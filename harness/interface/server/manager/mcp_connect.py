"""McpConnectMixin: the one-click sign-in flows of the connectors that sit on an MCP server.

Three grants reach a vendor's server: OAuth with dynamic registration (`mcp_connect_connector`),
a device code (`_device_connect_connector`) and a pasted bearer token (`arm_bearer_mcp`). The
browser PKCE flow (`connect_pkce_connector`) is for connectors whose tools are our own and not a
vendor server's. Every flow that seeds a server entry takes it back if the connect fails, since
an enabled entry with nothing behind it would re-arm at every session start.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from harness.extensions.mcp import delete_global_server, put_global_server


class McpConnectMixin:
    def _keep_mcp_mode(self, name: str, profile: dict[str, Any], **more: Any) -> None:
        self.secrets.put(f"{name}:default", {**profile, "mode": "mcp", "enabled": True, **more})

    async def _connect_or_roll_back(self, name: str) -> dict[str, Any]:
        """Connect the server that was just seeded; on failure remove its entry again."""
        result = await self.connect_mcp(name)
        if not result.get("ok"):
            delete_global_server(name)
        return result

    # -- OAuth with dynamic registration -------------------------------------------------

    async def mcp_connect_connector(self, name: str, variant: Optional[str] = None) -> dict[str, Any]:
        """One-click connect of a connector that has an MCP server (`descriptor.mcp_url`): seed
        the global entry, run the browser OAuth flow, and mark the connector profile
        `mode: "mcp"` on success.

        `variant` picks among a multi-endpoint server's endpoints. It steers the URL now but is
        only PERSISTED on success: writing it first made an empty profile non-empty, and a
        connector with no manual fields reads a non-empty profile as connected, so the card
        turned green the moment the flow started with no tokens behind it. The sign-in is for
        that endpoint specifically (its own OAuth resource), and the plan must resolve to the
        same URL afterwards or the tokens would not match the server they were minted for. An
        unknown key is no error: it resolves to the default, like no key at all.
        """
        from harness.integrations.connectors.descriptors import get_descriptor, mcp_url_for

        descriptor = get_descriptor(name)
        if descriptor is None or not descriptor.mcp_url:
            return {"ok": False, "error": f"{name} has no MCP connect path"}
        if descriptor.mcp_device_auth:
            return await self._device_connect_connector(descriptor)
        profile = self.secrets.get(f"{name}:default") or {}
        chosen = variant if (descriptor.mcp_variants and variant) else None
        put_global_server(
            name,
            {
                "url": mcp_url_for(descriptor, {**profile, "mcp_variant": chosen} if chosen else profile),
                "auth": "oauth",
                "requires_approval": False,
                "enabled": True,
            },
        )
        result = await self._connect_or_roll_back(name)
        if result.get("ok"):
            fresh = self.secrets.get(f"{name}:default") or {}
            self._keep_mcp_mode(name, fresh, **({"mcp_variant": chosen} if chosen else {}))
        return result

    # -- device code ---------------------------------------------------------------------

    def device_client_id(self, descriptor: Any) -> str:
        """The public OAuth client id of a device-grant connector, or "" if there is none.

        The operator's, from the environment or the state folder's `.env` (`client_env`); a
        descriptor's own id is the fallback for a build that carries one. The source tree carries
        none, so without the setting the card offers its manual way.
        """
        from harness.integrations.connectors.descriptors import client_env

        return client_env(descriptor.oauth_client_id_env) or (descriptor.oauth_client_id or "").strip()

    def _device_scope(self, descriptor: Any) -> str:
        """The scope the device grant asks for: the descriptor's, widened to what the chosen
        toolsets need. With toolsets it asks for exactly their union, so choosing one and
        reconnecting is the whole "grant access" step."""
        if descriptor.mcp_toolsets is None:
            return descriptor.oauth_scope
        profile = self.secrets.get(f"{descriptor.name}:default")
        return descriptor.mcp_toolsets.scope_string(descriptor.mcp_toolsets.enabled_ids(profile))

    async def _device_connect_connector(self, descriptor: Any) -> dict[str, Any]:
        """Start a device grant: return the user code NOW and finish in the background. The
        person is about to paste the code, so this half is awaited and only the polling runs
        detached."""
        from harness.extensions.mcp.device_auth import DeviceFlowError, request_code

        client_id = self.device_client_id(descriptor)
        if not client_id:
            return {
                "ok": False,
                "error": (
                    f"One-click sign-in needs a {descriptor.title} client id. Use the Manual tab, or "
                    f"set {descriptor.oauth_client_id_env} in Reeve's .env."
                ),
            }
        try:
            code = await request_code(
                device_endpoint=descriptor.mcp_device_endpoint,
                client_id=client_id,
                scope=self._device_scope(descriptor),
            )
        except DeviceFlowError as exc:
            return {"ok": False, "error": str(exc)}
        self._mcp_authorizing.add(descriptor.name)
        self._device_flows[descriptor.name] = code.to_dict()
        # Held by reference until it ends: a task nobody stores can be collected mid-flight.
        task = asyncio.create_task(self._device_finish(descriptor, client_id, code))
        self._background_flows.add(task)
        task.add_done_callback(self._background_flows.discard)
        return {"ok": True, "device": code.to_dict()}

    async def _device_finish(self, descriptor: Any, client_id: str, code: Any) -> None:
        """Poll until the person approves, then arm the server. Runs detached."""
        from harness.extensions.mcp.device_auth import DeviceFlowError, poll_for_token, store_tokens
        from harness.integrations.connectors.descriptors import mcp_url_for

        name = descriptor.name
        try:
            raw = await poll_for_token(
                token_endpoint=descriptor.oauth_token_endpoint, client_id=client_id, code=code
            )
            store_tokens(name, raw, self.secrets)
        except DeviceFlowError as exc:
            self._mcp_errors[name] = str(exc)[:500]
            return
        except Exception as exc:  # the transport blew up mid-poll
            self._mcp_errors[name] = (str(exc) or exc.__class__.__name__)[:500]
            return
        finally:
            self._mcp_authorizing.discard(name)
            self._device_flows.pop(name, None)
        put_global_server(
            name,
            {
                "url": mcp_url_for(descriptor, self.secrets.get(f"{name}:default")),
                "auth": "device",
                "client_id": client_id,
                "requires_approval": False,
                "enabled": True,
            },
        )
        if (await self._connect_or_roll_back(name)).get("ok"):
            self._keep_mcp_mode(name, self.secrets.get(f"{name}:default") or {})

    def set_connector_toolsets(self, name: str, ids: list[str]) -> dict[str, Any]:
        """Choose which of the vendor server's toolsets this connection serves.

        The choice is saved and the answer says which scopes the token still lacks. It never
        starts a sign-in: the page shows the missing permission and the person presses "Grant
        access", the ordinary connect the descriptor already runs (which now asks for the union
        of scopes). Locked sets stay on whatever is sent.
        """
        from harness.extensions.mcp import device_auth
        from harness.integrations.connectors.descriptors import get_descriptor

        descriptor = get_descriptor(name)
        if descriptor is None or descriptor.mcp_toolsets is None:
            return {"ok": False, "error": f"{name} has no toolsets to choose"}
        known = {toolset.id for toolset in descriptor.mcp_toolsets.TOOLSETS}
        unknown = [i for i in ids if i not in known]
        if unknown:
            return {"ok": False, "error": f"unknown toolset: {', '.join(unknown)}"}
        profile = self.secrets.get(f"{name}:default") or {}
        self.secrets.put(f"{name}:default", {**profile, "mcp_toolsets": list(ids)})
        chosen = descriptor.mcp_toolsets.enabled_ids({"mcp_toolsets": ids})
        granted = device_auth.granted_scopes(name, self.secrets)
        return {"ok": True, "enabled": chosen, "needs_scopes": descriptor.mcp_toolsets.missing_scopes(chosen, granted)}

    # -- a pasted bearer token -----------------------------------------------------------

    async def arm_bearer_mcp(self, name: str) -> dict[str, Any]:
        """Point a manually connected connector at the vendor's MCP server with the token the
        person pasted.

        A personal access token is a bearer and these servers accept one in the Authorization
        header, so the manual path reaches the same catalog the device grant does; only how the
        token was obtained differs. Without this the pasted token would drive our few REST tools
        while the vendor's whole server sat unused.

        Failure is not fatal: the entry is rolled back and `mode` is left unset, so the
        connector keeps working on its REST tools and does not end up in "mcp" mode with no
        reachable tools.
        """
        from harness.extensions.mcp.device_auth import store_tokens
        from harness.integrations.connectors.descriptors import get_descriptor, mcp_url_for

        descriptor = get_descriptor(name)
        if descriptor is None or not descriptor.mcp_url or not descriptor.mcp_device_auth:
            return {"ok": False, "error": f"{name} has no bearer-token MCP path"}
        profile = self.secrets.get(f"{name}:default") or {}
        token = next(
            (str(profile.get(f.key) or "").strip() for f in descriptor.fields if f.secret and profile.get(f.key)),
            "",
        )
        if not token:
            return {"ok": False, "error": f"no {descriptor.title} token stored"}
        store_tokens(name, {"access_token": token, "token_type": "bearer"}, self.secrets)
        put_global_server(
            name,
            {"url": mcp_url_for(descriptor, profile), "auth": "device", "requires_approval": False, "enabled": True},
        )
        result = await self._connect_or_roll_back(name)
        if result.get("ok"):
            self._keep_mcp_mode(name, profile)
            return {"ok": True, "tools": result.get("tools")}
        return {"ok": False, "error": result.get("error")}  # `mode` stays: the REST tools still work

    # -- browser PKCE, tools of our own --------------------------------------------------

    def pkce_client(self, descriptor: Any) -> tuple[str, str]:
        """(client_id, client_secret) of a PKCE connector, or ("", "") when none is registered.

        Resolution is the process environment, then the local `.env` the secret store already
        reads, then a descriptor's own value (the source tree sets none). That lets an operator
        supply a client WITHOUT committing one: Google requires a client secret on the token
        exchange even for a desktop app, and though its documentation calls an installed app's
        secret non-confidential, a credential in the source tree is still one in every clone.
        """
        from harness.integrations.connectors.descriptors import client_env

        return (
            client_env(descriptor.oauth_client_id_env) or (descriptor.oauth_client_id or "").strip(),
            client_env(descriptor.oauth_client_secret_env) or (descriptor.oauth_client_secret or "").strip(),
        )

    async def connect_token_grant(self, name: str) -> dict[str, Any]:
        """One click for a vendor whose authorize page hands the app's key a token (Trello, ADR
        0197): the browser opens, the person presses Allow, the landing page posts the token, and it
        is checked and stored exactly as a pasted key and token would be."""
        from harness.integrations.connectors.descriptors import get_descriptor
        from harness.integrations.connectors.lifecycle.connect import connect_connector
        from harness.integrations.connectors.trello_auth import CALLBACK_PATH, GRANT, authorize_url
        from harness.extensions.mcp.oauth import redirect_base

        descriptor = get_descriptor(name)
        if descriptor is None or not getattr(descriptor, "browser_token_auth", False):
            return {"ok": False, "error": f"{name} has no browser sign-in"}
        key, _secret = self.pkce_client(descriptor)
        if not key:
            message = (
                f"One click needs a {descriptor.title} API key: set {descriptor.oauth_client_id_env} in "
                "Reeve's .env, or use Manual."
            )
            self._mcp_errors[name] = message
            return {"ok": False, "error": message}
        self._mcp_errors.pop(name, None)
        self._mcp_authorizing.add(name)
        try:
            token = await GRANT.run(authorize_url(key, redirect_base() + CALLBACK_PATH))
            result = await asyncio.to_thread(connect_connector, self.secrets, name, {"api_key": key, "token": token})
        except Exception as exc:
            result = {"ok": False, "error": (str(exc) or exc.__class__.__name__)[:500]}
        finally:
            self._mcp_authorizing.discard(name)
        if not result.get("ok"):
            self._mcp_errors[name] = str(result.get("error") or "the sign-in failed")[:500]
        return result

    async def connect_pkce_connector(self, name: str) -> dict[str, Any]:
        """Browser sign-in for a connector whose tools are ours and not a vendor MCP server's.

        It is the same grant as the MCP-backed PKCE path with a different landing: the token goes
        into the connector's own profile (`connectors/google_auth.py`) and no MCP server entry is
        seeded, because there is no MCP server on the other side."""
        from harness.extensions.mcp.pkce_auth import PkceAuthError, authorize_raw
        from harness.integrations.connectors import microsoft_auth
        from harness.integrations.connectors.descriptors import get_descriptor
        from harness.integrations.connectors.google_auth import _identity_scope, store_signin

        descriptor = get_descriptor(name)
        if descriptor is None or not getattr(descriptor, "pkce_auth", False):
            return {"ok": False, "error": f"{name} has no browser sign-in"}
        client_id, client_secret = self.pkce_client(descriptor)
        if not client_id:
            return {
                "ok": False,
                "error": (
                    f"One-click sign-in needs a {descriptor.title} client id: set "
                    f"{descriptor.oauth_client_id_env} in Reeve's .env, or use Manual."
                ),
            }
        self._mcp_authorizing.add(name)
        try:
            raw = await authorize_raw(
                auth_endpoint=descriptor.pkce_auth_endpoint,
                token_endpoint=descriptor.oauth_token_endpoint,
                client_id=client_id,
                client_secret=client_secret,
                scope=(
                    microsoft_auth.with_identity(descriptor.oauth_scope)
                    if descriptor.pkce_provider == "microsoft"
                    else _identity_scope(descriptor.oauth_scope)
                ),
                extra_params=dict(descriptor.pkce_auth_extra or ()),
            )
        except PkceAuthError as exc:
            self._mcp_errors[name] = str(exc)[:500]
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            message = (str(exc) or exc.__class__.__name__)[:500]
            self._mcp_errors[name] = message
            return {"ok": False, "error": message}
        finally:
            self._mcp_authorizing.discard(name)
        if descriptor.pkce_provider == "microsoft":
            return microsoft_auth.store_signin(self.secrets, name, raw, client_id=client_id)
        return store_signin(self.secrets, name, raw, client_id=client_id, client_secret=client_secret)
