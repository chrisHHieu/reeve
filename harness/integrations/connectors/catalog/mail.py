"""Mailbox tools: IMAP email, Gmail, Outlook."""

from __future__ import annotations

from .types import ConnectorToolDef, row_makers

_read, _write = row_makers("email")
EMAIL: tuple[ConnectorToolDef, ...] = (
    _read("email_list_folders", "List folders", "List mailbox folders and message counts."),
    _read("email_search", "Search mail", "Search the mailbox; returns envelopes, never marks messages read."),
    _read("email_read", "Read message", "Read one email's headers, body, and attachment list."),
    _write("email_download_attachment", "Save attachment", "Save one attachment into the session folder (requires approval)."),
    _write("email_send", "Send email", "Send or reply to an email via SMTP (requires approval).", target="to"),
)

_read, _write = row_makers("gmail")
GMAIL: tuple[ConnectorToolDef, ...] = (
    _read("gmail_search_messages", "Search Gmail", "Search Gmail messages."),
    _read("gmail_get_message", "Read message", "Read a Gmail message."),
    _write("gmail_send_email", "Send email", "Send an email through Gmail.", target="to"),
    _read("gmail_search_threads", "Search threads", "List Gmail threads matching a search query."),
    _read("gmail_get_thread", "Read thread", "Read every message in a Gmail thread."),
    _read("gmail_list_drafts", "List drafts", "List draft emails."),
    _read("gmail_get_draft", "Read draft", "Read one draft email."),
    _read("gmail_list_labels", "List labels", "List labels and their ids."),
    _write("gmail_create_draft", "Create draft", "Create a draft email; nothing is sent."),
    _write("gmail_create_label", "Create label", "Create a new Gmail label."),
    _write("gmail_label_message", "Label message", "Add labels to one message."),
    _write("gmail_label_thread", "Label thread", "Add labels to a whole thread."),
    _write("gmail_update_message_labels", "Update message labels", "Add and remove labels on one message atomically."),
    _write("gmail_unlabel_message", "Unlabel message", "Remove labels from one message."),
    _write("gmail_unlabel_thread", "Unlabel thread", "Remove labels from a whole thread."),
    _write("gmail_trash_message", "Trash message", "Move one message to the Trash."),
    _write("gmail_trash_thread", "Trash thread", "Move a whole thread to the Trash."),
    _write("gmail_untrash_message", "Restore message", "Restore one message from the Trash."),
    _write("gmail_untrash_thread", "Restore thread", "Restore a whole thread from the Trash."),
    _write("gmail_mark_message_spam", "Mark message spam", "Mark one message as Spam."),
    _write("gmail_mark_thread_spam", "Mark thread spam", "Mark a whole thread as Spam."),
    _write("gmail_unmark_message_spam", "Unmark message spam", "Move one message out of Spam."),
    _write("gmail_unmark_thread_spam", "Unmark thread spam", "Move a whole thread out of Spam."),
    _write("gmail_apply_sensitive_message_label", "Apply TRASH/SPAM to message", "Apply the TRASH or SPAM label to one message."),
    _write("gmail_apply_sensitive_thread_label", "Apply TRASH/SPAM to thread", "Apply the TRASH or SPAM label to a whole thread."),
    _read("gmail_list_attachments", "List attachments", "List a message's attachments and the ids needed to download them."),
    _write("gmail_download_attachment", "Download attachment", "Save one attachment into the session folder (requires approval)."),
    _write("gmail_reply", "Reply", "Reply in the message's thread, to its sender or to everyone (requires approval)."),
    _write("gmail_create_reply_draft", "Draft a reply", "Write a reply as a draft in the thread; nothing is sent."),
    _write("gmail_forward", "Forward", "Forward a message with its text quoted (requires approval)."),
    _write("gmail_send_draft", "Send draft", "Send an existing draft (requires approval)."),
    _write("gmail_update_draft", "Update draft", "Rewrite a draft's recipients, subject and body."),
    _write("gmail_delete_draft", "Delete draft", "Delete a draft permanently (requires approval)."),
    _write("gmail_update_label", "Rename label", "Rename a Gmail label."),
    _write("gmail_delete_label", "Delete label", "Delete a label; its messages stay (requires approval)."),
    _write("gmail_batch_modify", "Label many messages", "Add and remove labels on up to 1000 messages at once: archive, mark read, star."),
)

_read, _write = row_makers("outlook")
OUTLOOK: tuple[ConnectorToolDef, ...] = (
    _read("outlook_whoami", "Read my account", "Read the account, its email and the mailbox's time zone."),
    _read("outlook_search_messages", "Search mail", "Search or list mail by text, folder, sender, date or attachments."),
    _read("outlook_read_message", "Read message", "Read a message whole, with its attachments listed."),
    _read("outlook_read_thread", "Read conversation", "Read a whole conversation."),
    _read("outlook_list_folders", "List folders", "List the mailbox's folders."),
    _read("outlook_find_people", "Find people", "Find a person's email by name."),
    _write("outlook_download_attachment", "Download attachment", "Save an attachment into the session folder."),
    _write("outlook_send_mail", "Send mail", "Send an email, with attachments from the session folder.", target="to"),
    _write("outlook_reply", "Reply", "Reply or reply to all.", target="message_id"),
    _write("outlook_forward", "Forward", "Forward a message.", target="to"),
    _write("outlook_draft", "Drafts", "Create, change or send a draft."),
    _write("outlook_organize", "File mail", "Move, mark read, flag or categorize messages."),
    _read("outlook_list_events", "List events", "List upcoming Outlook calendar events."),
    _read("outlook_find_times", "Find meeting times", "Find times when everyone is free (work accounts)."),
    _write("outlook_create_event", "Create event", "Create an Outlook calendar event."),
    _write("outlook_update_event", "Update event", "Change fields of an existing event."),
    _write("outlook_delete_event", "Delete event", "Delete a calendar event."),
    _write("outlook_respond_event", "Respond to invite", "Accept, decline, or tentatively accept a meeting invite."),
)

_read, _write = row_makers("onedrive")
ONEDRIVE: tuple[ConnectorToolDef, ...] = (
    _read("onedrive_search", "Search", "Search file names and contents."),
    _read("onedrive_list", "List files", "List a folder, the files shared with you, or recent ones."),
    _read("onedrive_download", "Open file", "Save a file into the session folder to read it."),
    _write("onedrive_upload", "Upload", "Upload a file from the session folder."),
    _write("onedrive_create_folder", "Create folder", "Create a folder."),
    _write("onedrive_move", "Move or rename", "Rename or move a file or folder."),
    _write("onedrive_share", "Share", "Share by link or invite people."),
    _write("onedrive_delete", "Delete", "Move a file or folder to the recycle bin."),
)

ALL: tuple[ConnectorToolDef, ...] = EMAIL + GMAIL + OUTLOOK + ONEDRIVE
