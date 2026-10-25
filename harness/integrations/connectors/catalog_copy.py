"""What a connector's page says before it is connected: what it is for, and what it can reach.

The page shows "About" and "Access" while there are no credentials yet, so the words live here,
with the catalog. They describe behaviour, not benefits: every line must stay true to the
connector's real tools (`catalog/`) and, for a managed connector, to the scopes its OAuth app asks
for. A line that promises more than is granted is a bug in the product.

ABOUT may be missing (the list's blurb is shown instead). ACCESS may not, for any available
connector; `tests/test_connectors.py` checks.
"""

from __future__ import annotations

#: A paragraph each, for the connector's page.
ABOUT: dict[str, str] = {
    "firecrawl": "Firecrawl's remote MCP server. The agent can convert a page to clean "
    "markdown, crawl a site, list its URLs and watch pages for changes. Signing in unlocks "
    "crawl, map, monitors and the research tools; without an account the server offers only "
    "scrape, search and parse.",
    "upwork": "Upwork's remote MCP server. The agent can search and save jobs, read your "
    "proposals, contracts, milestones and offers, check the freelancer dashboard and your "
    "financials, read and send messages, and update or boost your profile. It acts as you, "
    "so it sees and changes exactly what your own Upwork account can.",
    "postman": "Postman's remote MCP server. The agent can read and edit collections, "
    "workspaces, environments, mocks and API specs, and run a collection. You choose how "
    "far it may go when you connect: Essentials has no tool that deletes anything, Full "
    "adds deleting and workspace administration, and Code generation is read-only.",
    "redmine": "Your own Redmine site, reached through its REST API with a personal API key. "
    "The agent can search and read issues, open and update them, comment, and read or log "
    "time. It never touches project settings, permissions or users, and never deletes.",
    "trello": "Your Trello boards, through Trello's REST API as your own account. The agent can "
    "find and read boards, lists and cards with their checklists, comments, attachments and "
    "custom fields; create, move, assign, label, comment on and check off cards; attach files "
    "from the conversation's folder; and shape lists, labels and boards. Archiving is the "
    "default way to set things aside; deleting is a separate tool that always says so.",
    "tcbs": "TCBS's remote MCP server for TCInvest. The agent reads Vietnamese market data "
    "directly: company profiles, financial statements, peer comparisons, valuation models, "
    "technical ratings, insider and foreign trading, and news. It is read-only, so it never "
    "touches your portfolio or places an order.",
    "google_docs": "Google's remote MCP server for Docs. The agent reads a document's text "
    "and edits it in place. It can reach the documents you open or create through it, and "
    "not the rest of your Drive.",
    "google_sheets": "Google's remote MCP server for Sheets. The agent reads ranges of "
    "cells, writes values and formulas, and inserts rows or columns. It can reach the "
    "spreadsheets you open or create through it, and not the rest of your Drive.",
    "google_slides": "Google's remote MCP server for Slides. The agent reads how a deck is "
    "built and edits slide content. It can reach the presentations you open or create "
    "through it, and not the rest of your Drive.",
    "jira": "A single Atlassian sign-in covers Jira, Confluence and Bitbucket: search and "
    "update issues, read and write pages, review pull requests. The sign-in happens on this "
    "computer and no token is typed. Agents get a curated part of Atlassian's catalog and "
    "not all of it. Atlassian's hosted MCP does not serve Server or Data Center sites; for "
    "those use the manual API token tab, which reaches Jira only.",
    "telegram": "Talk to Reeve from Telegram. What you send to your bot reaches the "
    "agent, and the reply comes back to the same chat. Only people on your allow-list get "
    "through.",
    "slack": "Put Reeve in Slack: mention it in a channel or message it directly "
    "and its replies land in the thread. You can connect any number of workspaces, each "
    "with its own list of who may talk to the agent.",
    "email": "Read, search and send mail on any IMAP account (Gmail, iCloud, Fastmail or a "
    "server of your own), signing in with an app password and not your account password.",
    "gmail": "Search, summarize and send through your Gmail. Several accounts connect side "
    "by side, and privacy filters can hide chosen senders or labels from agents completely.",
    "google_calendar": "Check when you are free, summarize your week and manage events. "
    "Several Google accounts connect side by side.",
    "browser": "A browser built into the app that agents drive to read pages and act on "
    "websites. It is separate from your own browser, and what it does is subject to "
    "approval.",
    "github": "Work with issues, pull requests, repository files and CI status. One click "
    "opens GitHub in your browser with a short code already copied; paste it, approve, and "
    "you are connected. No token is created or typed, it is your own GitHub account and not "
    "a shared one, and the token stays on this computer. Mentioning the agent on an issue "
    "no longer reaches it, because that needed a public URL for GitHub's webhooks and a "
    "desktop has none.",
    "outlook": "Your Microsoft mail, calendar and people, a work or a personal account, through "
    "one browser sign-in that renews itself. The agent can search and read mail and whole "
    "conversations, reply, forward, draft, send with files from the conversation's folder, "
    "and file messages; plan meetings, find times everyone is free and answer invitations. "
    "Several mailboxes connect side by side.",
    "whatsapp_personal": "Your own WhatsApp number, linked as a device the way WhatsApp Web is, through "
    "an unofficial library. The agent can list, read and search your chats and contacts, and save "
    "media into the conversation's folder; every message, reaction or read receipt it sends waits "
    "for your approval, and it writes only to people you already talk to. WhatsApp can ban a "
    "number it sees doing this.",
    "teams": "Your Microsoft Teams chats and channels, with a work or school account. The agent "
    "can list and read your chats, start a chat with people or write in one, see your teams and "
    "their channels, post and reply in a channel, and react. Reading a channel's posts needs an "
    "administrator's grant and is not asked for.",
    "onedrive": "Your OneDrive and the files shared with you, a work or a personal account. The "
    "agent can find files, open Word, Excel, PowerPoint and PDF files to read them, upload "
    "what it made, organize folders and share by link or by invitation.",
    "notion": "Search and read the pages and databases you share with the connection, and "
    "create new pages. You decide exactly which pages it can see.",
    "google_drive": "Search, browse and read files across your Drive. Several accounts "
    "connect side by side.",
}

# What connecting actually grants, in short honest lines. A line that describes a power to
# change something always says so, and a line about reading states its limit ("...your account
# can see").
ACCESS: dict[str, list[str]] = {
    "firecrawl": [
        "Reads public web pages: converts them to markdown, searches and extracts data.",
        "Crawls whole sites and maps their URLs, within the limits of your account.",
        "Creates and manages page monitors, which run on Firecrawl's side.",
        "Only fetches the web; it never reads your files or your other connectors.",
    ],
    "upwork": [
        "Reads the jobs, proposals, contracts, milestones, offers and messages your account can see.",
        "Sends messages, saves jobs, submits milestones and answers offers as you.",
        "Can update and boost your profile; boosting spends Connects, which cost money.",
        "Accepting or declining an offer is a real contract decision and not a draft.",
    ],
    "postman": [
        "Reads the collections, workspaces, environments, mocks and specs your account can see.",
        "Creates and updates them, and runs a collection, as you.",
        "Essentials, the default, has no tool that deletes anything.",
        "Full adds 17 delete tools, including deleting a collection or a whole workspace.",
    ],
    "redmine": [
        "Reads and searches the issues, projects and time entries your account can see.",
        "Creates and updates issues, comments and logs time as you.",
        "Deletes nothing and leaves project and user settings alone.",
        "An administrator must have enabled the REST API on the site.",
    ],
    "trello": [
        "Reads the boards, lists, cards, comments and attachments your account can open.",
        "Creates, moves, assigns, labels and comments on cards, and ticks checklist items, as you.",
        "Attaches only files from the conversation's own folder.",
        "Deletes only through its Delete tool, which always names what goes.",
    ],
    "tcbs": [
        "Reads stock overviews, company profiles, officers and major shareholders.",
        "Reads financial statements and key ratios, with peer comparison by industry.",
        "Reads valuation models, technical ratings and analyst recommendations.",
        "Reads company news, events and corporate-action calendars.",
        "Signing in uses TCBS's own OAuth with iOTP, and the tokens stay on this computer.",
    ],
    "google_docs": [
        "Reads the text and structure of the documents shared with this connection.",
        "Inserts and replaces content in those documents.",
        "Cannot browse your whole Drive, only files opened or created with the agent.",
    ],
    "google_sheets": [
        "Reads ranges of cells and the structure of a spreadsheet.",
        "Writes values and formulas, and inserts rows or columns.",
        "Cannot browse your whole Drive, only files opened or created with the agent.",
    ],
    "google_slides": [
        "Reads the structure and text of a presentation.",
        "Edits slide content.",
        "Cannot browse your whole Drive, only files opened or created with the agent.",
    ],
    "telegram": [
        "Sees what people write to your bot; your own chats stay out of reach.",
        "Writes back under the bot's name.",
        "Answers only the senders on your allow-list.",
    ],
    "slack": [
        "Sees the channels the bot has been invited to, and messages sent to it directly.",
        "Writes and shares files there under the bot's name.",
        "Opens files people share in those channels.",
        "Looks up member and channel names, to know who is speaking.",
    ],
    "email": [
        "Searches and opens your mail over IMAP.",
        "Sends mail from your address, and saves attachments on this computer.",
        "Signs in with an app password and never your account password.",
    ],
    "gmail": [
        "Searches and opens your mail.",
        "Sends mail from your address.",
        "Never deletes mail and never changes account settings.",
    ],
    "google_calendar": [
        "Sees the events and free time in all your calendars.",
        "Adds, changes and removes events.",
    ],
    "browser": [
        "Opens and reads web pages in a browser session of its own.",
        "Clicks, types and uploads files only inside that session.",
        "Never touches your personal browser or the accounts you are signed in to there.",
    ],
    # One click now grants the whole catalog of GitHub's own MCP server, which is wider than the
    # three lines this used to carry, and the consent screen is where that has to be said.
    "github": [
        "Reads code, issues, pull requests and CI on the repositories you grant.",
        "One click gives the tools GitHub's own MCP server offers, over the scopes you "
        "approve. They include creating and editing issues, commenting and reviewing pull "
        "requests.",
        "A pasted token reaches the same GitHub MCP tools, bounded by the scopes you gave "
        "that token.",
        "You choose the scope on GitHub's own approval screen.",
        "It cannot receive anything: mentioning the agent on an issue does not reach it.",
    ],
    "outlook": [
        "Reads and searches your mail, your calendar and the people you work with.",
        "Sends, replies, forwards and files mail as you; attaches only files from the conversation's folder.",
        "Creates, changes and cancels events, and answers invitations as you.",
        "Deleting a message moves it to Deleted Items.",
    ],
    "whatsapp_personal": [
        "Reads and searches the chats and contacts this device has kept on your computer.",
        "Sends messages, reactions and read receipts only after you approve each one, and only to people you already talk to.",
        "Keeps your messages on this computer for as long as you choose (90 days unless you change it).",
        "Uses an unofficial library: WhatsApp can ban a number it sees doing this.",
    ],
    "teams": [
        "Reads your chats and their messages, your teams and channels, and finds people in your organization.",
        "Sends chat messages, starts chats, posts and replies in channels, and reacts, as you.",
        "Does not read channel posts: that needs an administrator's grant.",
        "Needs a work or school account.",
    ],
    "onedrive": [
        "Reads and searches your files and the ones shared with you.",
        "Uploads, renames, moves and shares files as you; uploads only from the conversation's folder.",
        "Deleting sends a file to the OneDrive recycle bin.",
    ],
    # One Atlassian sign-in grants all three products at once, so all three belong here. The
    # consent screen the person is about to see asks for the whole grant, and a list that named
    # only Jira would understate what they approve.
    "jira": [
        "Reads and searches the Jira issues your account can see.",
        "Creates, updates and moves issues through their workflow, and comments as you.",
        "Reads and searches the Confluence spaces and pages you can see, and creates and "
        "updates pages and comments as you.",
        "Reads the Bitbucket repositories, files and pull requests you can see, and opens "
        "pull requests, comments and approves as you.",
        "Deletes no issue, page or comment, and leaves project and permission settings alone.",
    ],
    "whatsapp": [
        "Writes from your Cloud API number.",
        "One way only: your chats are not readable through it.",
    ],
    "google_drive": [
        "Searches and opens your files; that is all it does.",
        "Nothing in your Drive is ever changed or removed through it.",
    ],
    "canva": ["Looks through your designs and exports them; that is all it does."],
    "figma": [
        "Opens design files and their comments, and exports assets.",
        "Comments under your name; a design itself is never edited.",
    ],
    "notion": [
        "Sees the pages and databases you share with the connection, and nothing else.",
        "One click gives the tools Notion's own MCP server offers, which include editing "
        "pages and not only creating them.",
        "A pasted integration token can search and read, query databases and create pages.",
        "Either way, nothing outside what you share with the connection is reachable.",
    ],
}

# For a connector with no lines of its own yet (experimental ones): none ships without one.
_DEFAULT_ACCESS = ["It can reach what the credentials you give it can reach, no more."]


def about_for(name: str) -> str:
    """The "About" paragraph, or "" (the list's one-line blurb stands in)."""
    return ABOUT.get(name) or ""


def access_for(name: str) -> list[str]:
    """The "Access" lines, a fresh list each call."""
    return [*(ACCESS.get(name) or _DEFAULT_ACCESS)]
