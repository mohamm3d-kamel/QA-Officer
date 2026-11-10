"""Notification Service - Email (SMTP) and Slack notifications."""
import asyncio
import html
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import httpx

logger = logging.getLogger(__name__)


class NotificationService:
    """Sends notifications via SMTP email and Slack webhook."""

    def __init__(
        self,
        email_enabled: bool = False,
        email_address: str = "",
        slack_enabled: bool = False,
        slack_webhook: str = "",
        smtp_host: str = "",
        smtp_port: int = 587,
        smtp_username: str = "",
        smtp_password: str = "",
        smtp_from: str = "",
    ):
        self.email_enabled = email_enabled
        self.email_address = email_address
        self.slack_enabled = slack_enabled
        self.slack_webhook = slack_webhook
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_username = smtp_username
        self.smtp_password = smtp_password
        self.smtp_from = smtp_from or smtp_username or email_address

    @classmethod
    def from_settings(cls, settings: dict) -> "NotificationService":
        """Build a NotificationService directly from DB settings dictionary."""
        try:
            port = int(settings.get("smtp_port") or 587)
        except (TypeError, ValueError):
            port = 587
        return cls(
            email_enabled=str(settings.get("notification_email_enabled", "false")).lower() == "true",
            email_address=settings.get("notification_email", ""),
            slack_enabled=str(settings.get("notification_slack_enabled", "false")).lower() == "true",
            slack_webhook=settings.get("notification_slack_webhook", ""),
            smtp_host=settings.get("smtp_host", ""),
            smtp_port=port,
            smtp_username=settings.get("smtp_username", ""),
            smtp_password=settings.get("smtp_password", ""),
            smtp_from=settings.get("smtp_from", ""),
        )

    async def notify(self, title: str, message: str, level: str = "info"):
        """Send notification through all enabled channels."""
        if self.email_enabled and self.email_address:
            await self._send_email(title, message)
        if self.slack_enabled and self.slack_webhook:
            await self._send_slack(title, message, level)

    def _deliver_smtp_sync(self, msg: MIMEMultipart):
        if self.smtp_port == 465:
            with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=15) as server:
                if self.smtp_username and self.smtp_password:
                    server.login(self.smtp_username, self.smtp_password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=15) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                if self.smtp_username and self.smtp_password:
                    server.login(self.smtp_username, self.smtp_password)
                server.send_message(msg)

    async def _send_email(self, subject: str, body: str):
        """Send email notification via configured SMTP server."""
        if not self.smtp_host:
            logger.warning(f"Email notification skipped for '{subject}': SMTP host is not configured in Settings.")
            return
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"[QA Officer] {subject}"
            msg["From"] = self.smtp_from or self.email_address
            msg["To"] = self.email_address
            msg.attach(MIMEText(body, "plain"))
            msg.attach(MIMEText(f"<pre>{html.escape(body)}</pre>", "html"))

            await asyncio.to_thread(self._deliver_smtp_sync, msg)
            logger.info(f"Email notification sent: {subject} -> {self.email_address}")
        except Exception as e:
            logger.error(f"Failed to send email notification: {e}")

    async def _send_slack(self, title: str, message: str, level: str = "info"):
        """Send Slack notification via webhook."""
        emoji_map = {
            "info": "ℹ️",
            "success": "✅",
            "warning": "⚠️",
            "error": "🚨",
        }
        emoji = emoji_map.get(level, "ℹ️")

        payload = {
            "text": f"{emoji} *{title}*",
            "blocks": [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": f"{emoji} {title}"[:150]},
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": message[:3000]},
                },
            ],
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(self.slack_webhook, json=payload)
                if 200 <= response.status_code < 300:
                    logger.info(f"Slack notification sent: {title}")
                else:
                    logger.warning(f"Slack notification failed: {response.status_code}")
        except Exception as e:
            logger.error(f"Failed to send Slack notification: {e}")

    async def notify_bug_created(self, bug_title: str, jira_key: str, jira_url: str):
        """Notify about a new bug ticket."""
        await self.notify(
            title=f"New Bug: {bug_title}",
            message=f"Bug ticket <{jira_url}|{jira_key}> has been created.\n\n*{bug_title}*",
            level="warning",
        )

    async def notify_bug_resolved(self, bug_title: str, jira_key: str):
        """Notify about a resolved bug."""
        await self.notify(
            title=f"Bug Resolved: {bug_title}",
            message=f"Bug ticket {jira_key} has been verified and moved to Ready To Deploy.",
            level="success",
        )

    async def notify_bug_failed(self, bug_title: str, jira_key: str, reason: str):
        """Notify about a failed QA bug."""
        await self.notify(
            title=f"Bug Failed QA: {bug_title}",
            message=f"Bug ticket {jira_key} has failed QA.\n\n*Reason:* {reason}",
            level="error",
        )

    async def notify_status_change(self, jira_key: str, old_status: str, new_status: str):
        """Notify about a Jira ticket status change."""
        await self.notify(
            title=f"Status Change: {jira_key}",
            message=f"Ticket moved from *{old_status}* to *{new_status}*",
            level="info",
        )

