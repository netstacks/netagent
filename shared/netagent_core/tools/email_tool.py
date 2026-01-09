"""Email tool for sending notifications and reports.

Provides email sending capability using SMTP configuration from environment.
"""

import asyncio
import logging
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Optional, Dict, Any

from ..llm.agent_executor import ToolDefinition

logger = logging.getLogger(__name__)


class SendEmailTool:
    """Tool for sending emails via SMTP."""

    name = "send_email"
    description = "Send an email to one or more recipients. Use this to share reports, findings, or notifications."

    def __init__(
        self,
        smtp_server: Optional[str] = None,
        smtp_port: Optional[int] = None,
        smtp_from: Optional[str] = None,
        smtp_username: Optional[str] = None,
        smtp_password: Optional[str] = None,
        use_tls: bool = False,
    ):
        """Initialize email tool.

        Args:
            smtp_server: SMTP server hostname (or SMTP_SERVER env var)
            smtp_port: SMTP port (or SMTP_PORT env var, default 25)
            smtp_from: From address (or SMTP_FROM env var)
            smtp_username: SMTP auth username (optional)
            smtp_password: SMTP auth password (optional)
            use_tls: Whether to use STARTTLS
        """
        self.smtp_server = smtp_server or os.getenv("SMTP_SERVER", "localhost")
        self.smtp_port = smtp_port or int(os.getenv("SMTP_PORT", "25"))
        self.smtp_from = smtp_from or os.getenv("SMTP_FROM", "netagent@localhost")
        self.smtp_username = smtp_username or os.getenv("SMTP_USERNAME")
        self.smtp_password = smtp_password or os.getenv("SMTP_PASSWORD")
        self.use_tls = use_tls or os.getenv("SMTP_USE_TLS", "").lower() == "true"

    def get_tool_definition(self) -> ToolDefinition:
        """Return OpenAI-compatible tool definition."""
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters={
                "type": "object",
                "properties": {
                    "recipients": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of email addresses to send to",
                    },
                    "subject": {
                        "type": "string",
                        "description": "Email subject line",
                    },
                    "body": {
                        "type": "string",
                        "description": "Email body content (plain text)",
                    },
                    "cc": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of CC recipients",
                    },
                },
                "required": ["recipients", "subject", "body"],
            },
            handler=self.execute,
        )

    async def execute(
        self,
        recipients: List[str],
        subject: str,
        body: str,
        cc: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Send an email.

        Args:
            recipients: List of email addresses to send to
            subject: Email subject
            body: Email body (plain text)
            cc: Optional CC recipients

        Returns:
            Dict with success status and message
        """
        try:
            # Validate inputs
            if not recipients:
                return {"success": False, "error": "No recipients specified"}

            if not subject:
                return {"success": False, "error": "No subject specified"}

            if not body:
                return {"success": False, "error": "No body specified"}

            # Create message
            msg = MIMEMultipart()
            msg["From"] = self.smtp_from
            msg["To"] = ", ".join(recipients)
            msg["Subject"] = subject

            if cc:
                msg["Cc"] = ", ".join(cc)

            msg.attach(MIMEText(body, "plain"))

            # All recipients including CC
            all_recipients = list(recipients)
            if cc:
                all_recipients.extend(cc)

            # Send email in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                self._send_email,
                msg,
                all_recipients,
            )

            logger.info(f"Email sent to {recipients} with subject: {subject}")
            return {
                "success": True,
                "message": f"Email sent successfully to {len(recipients)} recipient(s)",
                "recipients": recipients,
                "subject": subject,
            }

        except smtplib.SMTPException as e:
            logger.error(f"SMTP error sending email: {e}")
            return {"success": False, "error": f"SMTP error: {str(e)}"}
        except Exception as e:
            logger.error(f"Error sending email: {e}")
            return {"success": False, "error": str(e)}

    def _send_email(self, msg: MIMEMultipart, recipients: List[str]) -> None:
        """Synchronous email sending (called from thread pool)."""
        with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
            if self.use_tls:
                server.starttls()

            if self.smtp_username and self.smtp_password:
                server.login(self.smtp_username, self.smtp_password)

            server.sendmail(
                self.smtp_from,
                recipients,
                msg.as_string(),
            )


def create_email_tool() -> ToolDefinition:
    """Create email tool with default configuration from environment."""
    tool = SendEmailTool()
    return tool.get_tool_definition()
