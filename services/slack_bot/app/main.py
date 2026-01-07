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
            "• Get help with network issues"
        )
        return

    # TODO: Route to appropriate handler based on text
    say(f"Thanks <@{user}>! I received: {clean_text}")


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
