"""Text of the transactional e-mails."""

from __future__ import annotations

from urllib.parse import quote

from ..core.config import Settings
from ..core.email import Outbox


def queue_reset_email(
    outbox: Outbox, settings: Settings, *, to: str, name: str, token: str
) -> None:
    link = f"{settings.public_app_url.rstrip('/')}/reset-password?token={quote(token)}"
    outbox.add(
        to,
        "Reset your Attendify password",
        f"Hi {name},\n\nUse this link to choose a new password "
        f"(valid for {settings.reset_token_minutes} minutes, single use):\n{link}\n\n"
        f"Token: {token}\n\nIf you did not ask for this, ignore this e-mail.\n",
    )


def queue_invite_email(
    outbox: Outbox, settings: Settings, *, to: str, name: str, org_name: str, token: str
) -> None:
    link = f"{settings.public_app_url.rstrip('/')}/accept-invite?token={quote(token)}"
    outbox.add(
        to,
        f"You have been invited to {org_name} on Attendify",
        f"Hi {name},\n\n{org_name} invited you to Attendify. Set your password here "
        f"(valid for {settings.invite_token_days} days, single use):\n{link}\n\n"
        f"Token: {token}\n",
    )
