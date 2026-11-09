"""Notification Service - Email and Slack notifications."""
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional
import httpx

logger = logging.getLogger(__name__)


class NotificationService:
    """Sends notifications via email and Slack."""

    def __init__(
        self,
        email_enabled: bool = False,
        email_address: str = "",
        slack_enabled: bool = False,
        slack_webhook: str = "",
    ):
        self.email_enabled = email_enabled
        self.email_address = email_address
        self.slack_enabled = slack_enabled
        self.slack_webhook = slack_webhook

    async def notify(self, title: str, message: str, level: str = "info"):
        """Send notification through all enabled channels."""
        if self.email_enabled and self.email_address:
            await self._send_email(title, message)
        if self.slack_enabled and self.slack_webhook:
            await self._send_slack(title, message, level)

    async def _send_email(self, subject: str, body: str):
        """Send email notification (using simple SMTP)."""
        try:
            msg = MIMEMultipart()
            msg["Subject"] = f"[QA Officer] {subject}"
            msg["To"] = self.email_address
            msg.attach(MIMEText(body, "html"))

            # Note: For production, configure SMTP settings
            # This is a placeholder that logs the notification
            logger.info(f"Email notification: {subject} -> {self.email_address}")
            logger.info(f"Email body: {body}")
        except Exception as e:
            logger.error(f"Failed to send email: {e}")

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
                    "text": {"type": "plain_text", "text": f"{emoji} {title}"},
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": message},
                },
            ],
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(self.slack_webhook, json=payload)
                if response.status_code == 200:
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

