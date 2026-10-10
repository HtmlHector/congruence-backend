"""Resend transactional email delivery primitive."""

from __future__ import annotations

import resend
import structlog

from src.config.settings import settings

logger = structlog.get_logger(__name__)


class EmailService:
    """Transactional email service using Resend."""

    def __init__(self, api_key: str | None = None, from_address: str | None = None) -> None:
        self._api_key = api_key or settings.email.api_key
        self._from_address = from_address or settings.email.from_address
        resend.api_key = self._api_key

    async def send_email(
        self,
        to: str | list[str],
        subject: str,
        html_body: str,
        text_body: str | None = None,
    ) -> dict[str, str]:
        """Send an email to one or more recipients."""
        recipients = [to] if isinstance(to, str) else to

        if self._api_key.startswith("re_placeholder"):
            logger.info(
                "email_mock_sent",
                to=recipients,
                subject=subject,
                from_address=self._from_address,
            )
            return {"id": "mock_email_123"}

        params: resend.Emails.SendParams = {
            "from": self._from_address,
            "to": recipients,
            "subject": subject,
            "html": html_body,
        }
        if text_body:
            params["text"] = text_body

        response = resend.Emails.send(params)
        logger.info("email_sent", id=response.get("id"), to=recipients, subject=subject)
        return {"id": str(response.get("id"))}


email_service = EmailService()
