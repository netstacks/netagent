"""Slack bot handlers for interactive messages."""

import os
import logging
import httpx

logger = logging.getLogger(__name__)

API_BASE_URL = os.getenv("API_BASE_URL", "http://api:8001")


def register_handlers(app):
    """Register all Slack handlers."""

    @app.action(pattern="^approve_\\d+$")
    def handle_approve(ack, body, client, logger):
        """Handle approval button click."""
        ack()

        action = body["actions"][0]
        approval_id = action["value"]
        user = body["user"]["id"]
        user_name = body["user"].get("username", user)

        logger.info(f"Approval {approval_id} approved by {user_name}")

        try:
            # Call API to approve
            with httpx.Client() as http_client:
                response = http_client.post(
                    f"{API_BASE_URL}/api/approvals/{approval_id}/approve",
                    json={"note": f"Approved via Slack by {user_name}"},
                    headers={
                        # In production, would need proper auth
                        "X-Amzn-Oidc-Identity": user,
                    }
                )

                if response.status_code == 200:
                    # Update the message
                    client.chat_update(
                        channel=body["channel"]["id"],
                        ts=body["message"]["ts"],
                        text=f":white_check_mark: Approved by <@{user}>",
                        blocks=[
                            {
                                "type": "section",
                                "text": {
                                    "type": "mrkdwn",
                                    "text": f":white_check_mark: *Approved* by <@{user}>",
                                }
                            }
                        ]
                    )
                else:
                    client.chat_postMessage(
                        channel=body["channel"]["id"],
                        thread_ts=body["message"]["ts"],
                        text=f"Failed to approve: {response.text}",
                    )

        except Exception as e:
            logger.error(f"Failed to process approval: {e}")
            client.chat_postMessage(
                channel=body["channel"]["id"],
                thread_ts=body["message"]["ts"],
                text=f"Error processing approval: {str(e)}",
            )

    @app.action(pattern="^reject_\\d+$")
    def handle_reject(ack, body, client, logger):
        """Handle rejection button click."""
        ack()

        action = body["actions"][0]
        approval_id = action["value"]
        user = body["user"]["id"]
        user_name = body["user"].get("username", user)

        logger.info(f"Approval {approval_id} rejected by {user_name}")

        try:
            # Call API to reject
            with httpx.Client() as http_client:
                response = http_client.post(
                    f"{API_BASE_URL}/api/approvals/{approval_id}/reject",
                    json={"note": f"Rejected via Slack by {user_name}"},
                    headers={
                        "X-Amzn-Oidc-Identity": user,
                    }
                )

                if response.status_code == 200:
                    # Update the message
                    client.chat_update(
                        channel=body["channel"]["id"],
                        ts=body["message"]["ts"],
                        text=f":x: Rejected by <@{user}>",
                        blocks=[
                            {
                                "type": "section",
                                "text": {
                                    "type": "mrkdwn",
                                    "text": f":x: *Rejected* by <@{user}>",
                                }
                            }
                        ]
                    )
                else:
                    client.chat_postMessage(
                        channel=body["channel"]["id"],
                        thread_ts=body["message"]["ts"],
                        text=f"Failed to reject: {response.text}",
                    )

        except Exception as e:
            logger.error(f"Failed to process rejection: {e}")
            client.chat_postMessage(
                channel=body["channel"]["id"],
                thread_ts=body["message"]["ts"],
                text=f"Error processing rejection: {str(e)}",
            )

    @app.shortcut("netagent_run_workflow")
    def handle_run_workflow_shortcut(ack, shortcut, client):
        """Handle workflow shortcut."""
        ack()

        # Open a modal to select workflow
        client.views_open(
            trigger_id=shortcut["trigger_id"],
            view={
                "type": "modal",
                "callback_id": "run_workflow_modal",
                "title": {"type": "plain_text", "text": "Run Workflow"},
                "submit": {"type": "plain_text", "text": "Run"},
                "blocks": [
                    {
                        "type": "input",
                        "block_id": "workflow_select",
                        "element": {
                            "type": "external_select",
                            "action_id": "workflow_id",
                            "placeholder": {
                                "type": "plain_text",
                                "text": "Select a workflow"
                            },
                            "min_query_length": 0,
                        },
                        "label": {"type": "plain_text", "text": "Workflow"}
                    }
                ]
            }
        )

    @app.options("workflow_id")
    def load_workflow_options(ack):
        """Load workflow options for external select."""
        try:
            with httpx.Client() as http_client:
                response = http_client.get(
                    f"{API_BASE_URL}/api/workflows?enabled=true&limit=50"
                )
                data = response.json()

                options = [
                    {
                        "text": {"type": "plain_text", "text": w["name"]},
                        "value": str(w["id"]),
                    }
                    for w in data.get("items", [])
                ]

                ack(options=options)

        except Exception as e:
            logger.error(f"Failed to load workflows: {e}")
            ack(options=[])

    @app.view("run_workflow_modal")
    def handle_run_workflow_submission(ack, body, view, client, logger):
        """Handle workflow run modal submission."""
        ack()

        user = body["user"]["id"]
        workflow_id = view["state"]["values"]["workflow_select"]["workflow_id"]["selected_option"]["value"]

        try:
            with httpx.Client() as http_client:
                response = http_client.post(
                    f"{API_BASE_URL}/api/workflows/{workflow_id}/run",
                    json={"trigger_data": {"slack_user": user}},
                    headers={
                        "X-Amzn-Oidc-Identity": user,
                    }
                )

                if response.status_code == 200:
                    data = response.json()
                    client.chat_postMessage(
                        channel=user,  # DM to user
                        text=f":rocket: Workflow started! Run ID: {data.get('id')}",
                    )
                else:
                    client.chat_postMessage(
                        channel=user,
                        text=f":x: Failed to start workflow: {response.text}",
                    )

        except Exception as e:
            logger.error(f"Failed to run workflow: {e}")
            client.chat_postMessage(
                channel=user,
                text=f":x: Error: {str(e)}",
            )

    logger.info("Slack handlers registered")
