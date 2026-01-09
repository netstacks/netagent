"""Notification tasks for email, Slack, and Jira."""

import os
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from celery import shared_task

logger = logging.getLogger(__name__)

# SMTP configuration
SMTP_SERVER = os.getenv("SMTP_SERVER", "localhost")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_FROM = os.getenv("SMTP_FROM", "netagent@localhost")
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")


@shared_task
def send_email(to: list, subject: str, body: str, html_body: str = None):
    """Send an email notification.

    Args:
        to: List of recipient email addresses
        subject: Email subject
        body: Plain text body
        html_body: Optional HTML body
    """
    logger.info(f"Sending email to {to}: {subject}")

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = SMTP_FROM
        msg["To"] = ", ".join(to)

        # Plain text version
        msg.attach(MIMEText(body, "plain"))

        # HTML version if provided
        if html_body:
            msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            if SMTP_USERNAME and SMTP_PASSWORD:
                server.starttls()
                server.login(SMTP_USERNAME, SMTP_PASSWORD)

            server.sendmail(SMTP_FROM, to, msg.as_string())

        logger.info(f"Email sent successfully to {to}")
        return {"success": True, "to": to}

    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        return {"success": False, "error": str(e)}


@shared_task
def send_slack_message(channel: str, text: str, blocks: list = None, thread_ts: str = None):
    """Send a Slack message.

    Args:
        channel: Slack channel ID or name
        text: Message text (fallback for notifications)
        blocks: Optional Slack Block Kit blocks
        thread_ts: Optional thread timestamp to reply in thread
    """
    logger.info(f"Sending Slack message to {channel}")

    # This would typically use the Slack Web API
    # For now, we'll just log since the Slack bot service handles this
    # The API service can call this task to queue Slack messages

    import httpx

    slack_bot_token = os.getenv("SLACK_BOT_TOKEN")
    if not slack_bot_token:
        logger.warning("SLACK_BOT_TOKEN not configured")
        return {"success": False, "error": "Slack not configured"}

    try:
        payload = {
            "channel": channel,
            "text": text,
        }

        if blocks:
            payload["blocks"] = blocks
        if thread_ts:
            payload["thread_ts"] = thread_ts

        with httpx.Client() as client:
            response = client.post(
                "https://slack.com/api/chat.postMessage",
                headers={
                    "Authorization": f"Bearer {slack_bot_token}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            result = response.json()

            if result.get("ok"):
                logger.info(f"Slack message sent: {result.get('ts')}")
                return {"success": True, "ts": result.get("ts")}
            else:
                logger.error(f"Slack API error: {result.get('error')}")
                return {"success": False, "error": result.get("error")}

    except Exception as e:
        logger.error(f"Failed to send Slack message: {e}")
        return {"success": False, "error": str(e)}


@shared_task
def send_slack_approval_request(
    channel: str,
    approval_id: int,
    action_description: str,
    risk_level: str,
    context: dict = None,
):
    """Send an approval request to Slack with interactive buttons.

    Args:
        channel: Slack channel to post in
        approval_id: ID of the approval record
        action_description: What action needs approval
        risk_level: Risk level (low, medium, high)
        context: Additional context to display
    """
    logger.info(f"Sending Slack approval request: {approval_id}")

    risk_emoji = {
        "low": ":white_check_mark:",
        "medium": ":warning:",
        "high": ":rotating_light:",
    }.get(risk_level, ":question:")

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{risk_emoji} Approval Required",
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Action:* {action_description}\n*Risk Level:* {risk_level.upper()}",
            }
        },
    ]

    if context:
        context_text = "\n".join([f"• *{k}:* {v}" for k, v in context.items()])
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Context:*\n{context_text}",
            }
        })

    blocks.append({
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Approve"},
                "style": "primary",
                "action_id": f"approve_{approval_id}",
                "value": str(approval_id),
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Reject"},
                "style": "danger",
                "action_id": f"reject_{approval_id}",
                "value": str(approval_id),
            },
        ]
    })

    return send_slack_message(
        channel=channel,
        text=f"Approval required: {action_description}",
        blocks=blocks,
    )


@shared_task
def create_jira_ticket(
    project_key: str,
    summary: str,
    description: str,
    issue_type: str = "Task",
    labels: list = None,
    custom_fields: dict = None,
):
    """Create a Jira ticket using the MCP-Atlassian server.

    Args:
        project_key: Jira project key
        summary: Ticket summary
        description: Ticket description
        issue_type: Issue type (Task, Bug, Story, etc.)
        labels: Optional list of labels
        custom_fields: Optional custom field values
    """
    import asyncio
    from netagent_core.db import get_db_context, MCPServer
    from netagent_core.mcp import MCPClient
    from netagent_core.utils import decrypt_value

    logger.info(f"Creating Jira ticket in {project_key}: {summary}")

    # Find Atlassian MCP server
    with get_db_context() as db:
        mcp_server = db.query(MCPServer).filter(
            MCPServer.name.ilike("%atlassian%"),
            MCPServer.enabled == True,
        ).first()

        if not mcp_server:
            # Try searching by type
            mcp_server = db.query(MCPServer).filter(
                MCPServer.name.ilike("%jira%"),
                MCPServer.enabled == True,
            ).first()

        if not mcp_server:
            logger.warning("No Atlassian/Jira MCP server configured")
            return {
                "success": False,
                "error": "No Atlassian MCP server configured",
                "project_key": project_key,
                "summary": summary,
            }

        # Get auth token
        auth_token = None
        encryption_key = os.getenv("ENCRYPTION_KEY")
        if mcp_server.auth_config_encrypted and encryption_key:
            try:
                auth_token = decrypt_value(mcp_server.auth_config_encrypted, encryption_key)
            except Exception as e:
                logger.error(f"Failed to decrypt MCP server auth: {e}")

        server_url = mcp_server.base_url
        auth_type = mcp_server.auth_type

    # Create MCP client and call tool
    async def _create_ticket():
        client = MCPClient(
            base_url=server_url,
            auth_type=auth_type,
            auth_token=auth_token,
        )

        try:
            await client.initialize()

            # Build the arguments for the Jira create issue tool
            tool_args = {
                "project_key": project_key,
                "summary": summary,
                "description": description,
                "issue_type": issue_type,
            }

            if labels:
                tool_args["labels"] = labels

            if custom_fields:
                tool_args["fields"] = custom_fields

            # Call the MCP tool
            # Tool name may vary based on MCP-Atlassian server implementation
            result = await client.call_tool("jira_create_issue", tool_args)

            return {
                "success": True,
                "result": result,
                "project_key": project_key,
                "summary": summary,
            }

        except Exception as e:
            logger.error(f"Failed to create Jira ticket via MCP: {e}")
            return {
                "success": False,
                "error": str(e),
                "project_key": project_key,
                "summary": summary,
            }

    # Run async function in sync context
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_create_ticket())
    finally:
        loop.close()
