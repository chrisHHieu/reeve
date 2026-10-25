"""A personal WhatsApp account, linked as a device, that the agent can read and answer from.

WhatsApp offers a personal account no API: the only way in is the one WhatsApp Web uses, a
linked device on the multi-device protocol (`whatsmeow`, reached through `neonize`). It is not
an official client, and an account using one can be banned; the connector that exposes this is
experimental and says so before it connects.

The parts, lowest first:

    records   what one message, chat or contact is, independent of any library
    store     the messages this device has seen, in SQLite, searchable without accents
    convert   neonize's protobuf events into records (the only module that reads them)
    link      the live connection: pairing, history and messages in, sends out
    server    the MCP server the harness arms, with read tools and gated send tools

Nothing here sends a message on its own: every send is a tool call the person approves.
"""
