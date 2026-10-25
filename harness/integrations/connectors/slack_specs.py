"""The Slack tool catalog as data: one row per tool, one Web API method behind it.

Slack's own MCP server (`mcp.slack.com`) takes only Marketplace or internal apps and only a
user token, and it has no channel, user-group or scheduling administration, so the owner's
call (2026-09-30) was to build the tools on the Web API. A tool is a row here, not a
function: name, method, read or write, which token, and the arguments. `slack_tools.py`
turns rows into callables and `catalog/policy.py` into catalogue entries, so the two cannot list
different tools.

Three groups, by what a wrong call costs:

* **read** — free to run.
* **write** — asks first (a connector write always does); on by default.
* **admin** — asks first AND off by default: channels, members, user groups, deletion. The
  person switches them on in the connector's Tools list.

`token="user"` marks the methods Slack answers only for a user token (search, reminders,
status). Without one the tool says so instead of failing with `not_allowed_token_type`.
Arguments named `channel` and `user` accept what a person says (`#general`, `@ana`, an
email) as well as an id; the resolution is in `slack_tools.py`, not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Arg:
    name: str
    kind: str  # "string" | "integer" | "boolean" | "list" | "json" | "channel" | "user"
    description: str
    required: bool = False
    #: The name on the wire when Slack's differs from the one the model sees.
    wire: str = ""

    @property
    def sent_as(self) -> str:
        return self.wire or self.name


@dataclass(frozen=True)
class SlackTool:
    name: str
    method: str  # the Web API method, or "" for a tool with a handler
    kind: str  # "read" | "write"
    group: str  # "read" | "write" | "admin"
    label: str
    description: str
    args: tuple[Arg, ...] = ()
    token: str = "bot"  # "bot" | "user"
    #: JSON body instead of form fields (methods whose arguments are structured).
    json_body: bool = False
    #: How the response is cut down to what a model needs (`slack_shape.SHAPES`).
    shape: str = "plain"
    #: A hand-written tool that is more than one method call (`slack_tools.HANDLERS`).
    handler: str = ""
    #: The argument that names what a standing rule pins.
    target_arg: Optional[str] = None


def S(name: str, description: str, required: bool = False, wire: str = "") -> Arg:
    return Arg(name, "string", description, required, wire)


def N(name: str, description: str, required: bool = False) -> Arg:
    return Arg(name, "integer", description, required)


def B(name: str, description: str) -> Arg:
    return Arg(name, "boolean", description)


def L(name: str, description: str, required: bool = False) -> Arg:
    return Arg(name, "list", description, required)


def J(name: str, description: str, required: bool = False) -> Arg:
    return Arg(name, "json", description, required)


def CH(description: str = "Channel: an id (C…), or a name such as #general", wire: str = "channel") -> Arg:
    return Arg("channel", "channel", description, True, wire)


def US(name: str = "user", description: str = "A person: an id (U…), @handle or email", required: bool = True) -> Arg:
    return Arg(name, "user", description, required)


_CURSOR = S("cursor", "The `next_cursor` of the previous page")
_LIMIT = N("limit", "How many to return (default 50)")

# -- read -------------------------------------------------------------------------

READ: tuple[SlackTool, ...] = (
    SlackTool("slack_list_channels", "", "read", "read", "List channels",
              "Find channels by name: id, name, private or public, and whether the bot is a member.",
              (S("query", "Part of a channel name"), N("limit", "How many (default 25, max 100)")),
              handler="list_channels"),
    SlackTool("slack_channel_info", "conversations.info", "read", "read", "Channel info",
              "A channel's topic, purpose, member count, privacy and whether it is archived.",
              (CH(),), shape="channel"),
    SlackTool("slack_channel_members", "conversations.members", "read", "read", "Channel members",
              "The user ids in a channel (use slack_get_user for names).",
              (CH(), _LIMIT, _CURSOR), shape="members"),
    SlackTool("slack_read_channel", "conversations.history", "read", "read", "Read a channel",
              "The latest messages of a channel or DM, newest first. `oldest`/`latest` are message timestamps.",
              (CH(), _LIMIT, S("oldest", "Only messages after this timestamp"),
               S("latest", "Only messages before this timestamp"), _CURSOR), shape="messages"),
    SlackTool("slack_read_thread", "conversations.replies", "read", "read", "Read a thread",
              "A thread's messages, the parent first. `ts` is the parent message's timestamp.",
              (CH(), S("ts", "Timestamp of the thread's parent message", True), _LIMIT, _CURSOR),
              shape="messages"),
    SlackTool("slack_get_message", "", "read", "read", "Get one message",
              "One message by channel and timestamp, with its permalink.",
              (CH(), S("ts", "The message's timestamp", True)), handler="get_message"),
    SlackTool("slack_list_users", "", "read", "read", "List people",
              "Find people by name or handle: id, name, handle, guest or not.",
              (S("query", "Part of a name or handle"), N("limit", "How many (default 25, max 100)")),
              handler="list_users"),
    SlackTool("slack_get_user", "", "read", "read", "Get a person",
              "One person's profile: name, title, time zone, status, and whether they are a bot.",
              (US(),), handler="get_user"),
    SlackTool("slack_list_user_channels", "users.conversations", "read", "read", "A person's channels",
              "The channels a person is in (the bot's own when no user is given).",
              (US(required=False), S("types", "public_channel,private_channel,mpim,im (default: the first two)"),
               _LIMIT, _CURSOR), shape="channels"),
    SlackTool("slack_list_usergroups", "usergroups.list", "read", "read", "List user groups",
              "User groups (@handles that notify a set of people).", (B("include_users", "Include member ids"),),
              shape="usergroups"),
    SlackTool("slack_usergroup_members", "usergroups.users.list", "read", "read", "User group members",
              "The user ids in a user group.", (S("usergroup", "The user group id (S…)", True),), shape="members"),
    SlackTool("slack_search_messages", "search.messages", "read", "read", "Search messages",
              "Search messages across the workspace. Supports Slack operators: `in:#channel from:@ana after:2026-09-01`.",
              (S("query", "The search", True), N("count", "Results per page (default 20, max 100)"),
               N("page", "Page number"), S("sort", "`score` or `timestamp`")),
              token="user", shape="search"),
    SlackTool("slack_search_files", "search.files", "read", "read", "Search files",
              "Search files shared in the workspace.", (S("query", "The search", True), N("count", "Results per page"),
                                                        N("page", "Page number")), token="user", shape="search_files"),
    SlackTool("slack_list_files", "files.list", "read", "read", "List files",
              "Files shared in a channel or by a person, newest first.",
              (Arg("channel", "channel", "Only this channel", False), US(required=False), N("count", "How many (default 20)"),
               N("page", "Page number")), shape="files"),
    SlackTool("slack_get_file", "files.info", "read", "read", "File info",
              "A file's name, type, size, owner and where it was shared.",
              (S("file", "The file id (F…)", True),), shape="file"),
    SlackTool("slack_read_canvas", "", "read", "read", "Read a canvas",
              "A canvas's text (the canvas id is a file id, F…).", (S("canvas_id", "The canvas id", True),),
              handler="read_canvas"),
    SlackTool("slack_get_reactions", "reactions.get", "read", "read", "Reactions on a message",
              "Who reacted to a message and with what.",
              (CH(), S("timestamp", "The message's timestamp", True)), shape="reactions"),
    SlackTool("slack_list_pins", "pins.list", "read", "read", "Pinned items", "What is pinned in a channel.",
              (CH(),), shape="pins"),
    SlackTool("slack_list_bookmarks", "bookmarks.list", "read", "read", "Channel bookmarks",
              "The links bookmarked at the top of a channel.", (CH(wire="channel_id"),), shape="plain"),
    SlackTool("slack_list_scheduled", "chat.scheduledMessages.list", "read", "read", "Scheduled messages",
              "Messages scheduled to be sent, with when.", (Arg("channel", "channel", "Only this channel", False),
                                                          _LIMIT), shape="plain"),
    SlackTool("slack_list_reminders", "reminders.list", "read", "read", "List reminders",
              "The signed-in person's reminders.", (), token="user", shape="plain"),
    SlackTool("slack_user_status", "", "read", "read", "Is someone around",
              "A person's presence and do-not-disturb state — check before messaging them.",
              (US(),), handler="user_status"),
    SlackTool("slack_list_items", "slackLists.items.list", "read", "read", "Read a Slack List",
              "The rows of a Slack List.", (S("list_id", "The list id (F…)", True), _LIMIT, _CURSOR)),
)
