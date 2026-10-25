"""The email connector: any IMAP and SMTP mailbox, signed in with an app password."""

from .tools import make_email_tools
from .validate import validate_email_account

__all__ = ["make_email_tools", "validate_email_account"]
