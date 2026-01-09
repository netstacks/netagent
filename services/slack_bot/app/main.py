"""Slack bot service - handles interactive messages and approvals."""

import os
import logging
import re

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

import sys
sys.path.insert(0, '/app/shared')

from handlers import register_handlers

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize Slack app
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN")

if not SLACK_BOT_TOKEN or not SLACK_APP_TOKEN:
    logger.error("SLACK_BOT_TOKEN and SLACK_APP_TOKEN are required")
    exit(1)

app = App(token=SLACK_BOT_TOKEN)

# Register all handlers
register_handlers(app)


def route_mention_to_handler(clean_text: str, user: str, say, logger) -> bool:
    """Route app mention to appropriate handler.

    Returns True if a handler matched, False otherwise.
    """
    import httpx

    api_base = os.getenv("API_BASE_URL", "http://api:8001")
    lower_text = clean_text.lower()

    # Check approvals command
    if any(kw in lower_text for kw in ["pending approval", "my approvals", "check approval", "show approval"]):
        try:
            with httpx.Client(timeout=10) as client:
                response = client.get(
                    f"{api_base}/api/approvals?status=pending&limit=10",
                    headers={"X-Amzn-Oidc-Identity": user},
                )
                data = response.json()

                approvals = data.get("items", [])
                if not approvals:
                    say(f"<@{user}> No pending approvals found.")
                else:
                    blocks = [
                        {
                            "type": "section",
                            "text": {"type": "mrkdwn", "text": f"*Pending Approvals ({len(approvals)}):*"}
                        }
                    ]
                    for approval in approvals[:5]:  # Limit to 5
                        blocks.append({
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"• *{approval.get('action_type', 'Action')}*: {approval.get('description', 'No description')[:100]}"
                            },
                            "accessory": {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "Review"},
                                "action_id": f"review_{approval['id']}",
                                "value": str(approval["id"]),
                            }
                        })
                    say(blocks=blocks)
                return True
        except Exception as e:
            logger.error(f"Failed to fetch approvals: {e}")
            say(f"<@{user}> Sorry, I couldn't fetch approvals. Please try again.")
            return True

    # Run workflow command
    if any(kw in lower_text for kw in ["run workflow", "start workflow", "execute workflow"]):
        # Extract workflow name if provided
        workflow_name = None
        for pattern in [r"run workflow[:\s]+(.+)", r"start workflow[:\s]+(.+)", r"execute workflow[:\s]+(.+)"]:
            match = re.search(pattern, lower_text, re.IGNORECASE)
            if match:
                workflow_name = match.group(1).strip()
                break

        if workflow_name:
            try:
                with httpx.Client(timeout=10) as client:
                    # Search for the workflow
                    response = client.get(
                        f"{api_base}/api/workflows?search={workflow_name}&enabled=true&limit=5"
                    )
                    data = response.json()
                    workflows = data.get("items", [])

                    if not workflows:
                        say(f"<@{user}> No workflow found matching '{workflow_name}'")
                    elif len(workflows) == 1:
                        # Run it directly
                        run_response = client.post(
                            f"{api_base}/api/workflows/{workflows[0]['id']}/run",
                            json={"trigger_data": {"slack_user": user, "requested_via": "slack_mention"}},
                            headers={"X-Amzn-Oidc-Identity": user},
                        )
                        if run_response.status_code == 200:
                            run_data = run_response.json()
                            say(f":rocket: <@{user}> Started workflow *{workflows[0]['name']}*! Run ID: `{run_data.get('id')}`")
                        else:
                            say(f"<@{user}> Failed to start workflow: {run_response.text}")
                    else:
                        # Multiple matches - show options
                        blocks = [
                            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Multiple workflows match '{workflow_name}':*"}}
                        ]
                        for wf in workflows:
                            blocks.append({
                                "type": "section",
                                "text": {"type": "mrkdwn", "text": f"• *{wf['name']}*"},
                                "accessory": {
                                    "type": "button",
                                    "text": {"type": "plain_text", "text": "Run"},
                                    "action_id": f"run_workflow_{wf['id']}",
                                    "value": str(wf["id"]),
                                }
                            })
                        say(blocks=blocks)
                    return True
            except Exception as e:
                logger.error(f"Failed to run workflow: {e}")
                say(f"<@{user}> Sorry, I couldn't start the workflow. Please try again.")
                return True
        else:
            say(
                f"<@{user}> Please specify a workflow name, e.g.:\n"
                "`@netagent run workflow: My Workflow Name`"
            )
            return True

    # List workflows command
    if any(kw in lower_text for kw in ["list workflow", "show workflow", "what workflow"]):
        try:
            with httpx.Client(timeout=10) as client:
                response = client.get(f"{api_base}/api/workflows?enabled=true&limit=10")
                data = response.json()
                workflows = data.get("items", [])

                if not workflows:
                    say(f"<@{user}> No workflows available.")
                else:
                    workflow_list = "\n".join([f"• *{wf['name']}*" for wf in workflows])
                    say(f"<@{user}> Available workflows:\n{workflow_list}")
                return True
        except Exception as e:
            logger.error(f"Failed to list workflows: {e}")
            say(f"<@{user}> Sorry, I couldn't fetch workflows. Please try again.")
            return True

    # List agents command
    if any(kw in lower_text for kw in ["list agent", "show agent", "what agent"]):
        try:
            with httpx.Client(timeout=10) as client:
                response = client.get(f"{api_base}/api/agents?enabled=true&limit=10")
                data = response.json()
                agents = data.get("items", [])

                if not agents:
                    say(f"<@{user}> No agents available.")
                else:
                    agent_list = "\n".join([f"• *{a['name']}*: {a.get('description', 'No description')[:80]}" for a in agents])
                    say(f"<@{user}> Available agents:\n{agent_list}")
                return True
        except Exception as e:
            logger.error(f"Failed to list agents: {e}")
            say(f"<@{user}> Sorry, I couldn't fetch agents. Please try again.")
            return True

    # Help command
    if any(kw in lower_text for kw in ["help", "what can you do", "commands"]):
        say(
            f"<@{user}> Here's what I can do:\n\n"
            "*Approvals:*\n"
            "• `check approvals` - View pending approvals\n\n"
            "*Workflows:*\n"
            "• `list workflows` - Show available workflows\n"
            "• `run workflow: <name>` - Start a workflow\n\n"
            "*Agents:*\n"
            "• `list agents` - Show available agents\n\n"
            "*Other:*\n"
            "• Use the `/netagent` shortcut to run workflows with a modal"
        )
        return True

    # No handler matched
    return False


@app.event("app_mention")
def handle_app_mention(event, say, logger):
    """Handle @netagent mentions."""
    user = event.get("user")
    text = event.get("text", "")

    # Remove the mention
    clean_text = re.sub(r"<@[A-Z0-9]+>", "", text).strip()

    if not clean_text:
        say(
            f"Hi <@{user}>! I'm NetAgent. You can:\n"
            "• Ask me to check approvals\n"
            "• Start a workflow\n"
            "• Get help with network issues\n\n"
            "Type `@netagent help` for more commands."
        )
        return

    # Route to appropriate handler based on text
    if not route_mention_to_handler(clean_text, user, say, logger):
        # No specific handler matched - send to a general agent or acknowledge
        say(
            f"<@{user}> I'm not sure how to help with that. "
            "Try `@netagent help` to see what I can do."
        )


@app.event("message")
def handle_message(event, logger):
    """Handle direct messages."""
    # Only respond to direct messages (not in channels)
    if event.get("channel_type") != "im":
        return

    logger.debug(f"Received DM: {event.get('text')}")


def main():
    """Start the Slack bot."""
    logger.info("Starting NetAgent Slack Bot")
    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.start()


if __name__ == "__main__":
    main()
