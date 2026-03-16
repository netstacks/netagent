"""Settings management routes."""

from typing import Optional, List
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from netagent_core.db import get_db, Settings
from netagent_core.auth import get_current_user, ALBUser
from netagent_core.utils import audit_log, AuditEventType

router = APIRouter()

# Approved Gemini models
APPROVED_MODELS = [
    {"value": "gemini-3-pro-preview", "label": "Gemini 3 Pro (Preview)", "group": "Gemini 3.x (Preview)"},
    {"value": "gemini-3-flash-preview", "label": "Gemini 3 Flash (Preview)", "group": "Gemini 3.x (Preview)"},
    {"value": "gemini-2.5-pro", "label": "Gemini 2.5 Pro", "group": "Gemini 2.5"},
    {"value": "gemini-2.5-flash", "label": "Gemini 2.5 Flash", "group": "Gemini 2.5"},
    {"value": "gemini-2.5-flash-lite", "label": "Gemini 2.5 Flash Lite", "group": "Gemini 2.5"},
    {"value": "gemini-2.5-flash-preview-09-2025", "label": "Gemini 2.5 Flash Preview (09-2025)", "group": "Gemini 2.5"},
    {"value": "gemini-2.5-flash-lite-preview-09-2025", "label": "Gemini 2.5 Flash Lite Preview (09-2025)", "group": "Gemini 2.5"},
    {"value": "gemini-2.5-flash-image", "label": "Gemini 2.5 Flash Image", "group": "Gemini 2.5 Multimodal"},
    {"value": "gemini-live-2.5-flash-native-audio", "label": "Gemini Live 2.5 Flash Native Audio", "group": "Gemini 2.5 Multimodal"},
    {"value": "gemini-2.5-pro-tts", "label": "Gemini 2.5 Pro TTS", "group": "Gemini 2.5 TTS"},
    {"value": "gemini-2.5-flash-tts", "label": "Gemini 2.5 Flash TTS", "group": "Gemini 2.5 TTS"},
    {"value": "gemini-2.5-flash-lite-preview-tts", "label": "Gemini 2.5 Flash Lite TTS (Preview)", "group": "Gemini 2.5 TTS"},
]

DEFAULT_SETTINGS = {
    "platform_name": "NetAgent",
    "default_model": "gemini-2.5-flash",
    "session_timeout": 30,
    "smtp_server": "",
    "smtp_port": 587,
    "smtp_from": "",
    "smtp_tls": True,
    "slack_channel": "",
    "slack_approval_channel": "",
    "confluence_url": "",
    "confluence_user": "",
    "jira_url": "",
    "jira_user": "",
    "jira_project": "",
    "approval_timeout": 24,
    "require_approval_high": True,
    "slack_approvals": True,
    "audit_retention": 365,
    "audit_tool_results": True,
    "knowledge_sync_interval": 60,  # Minutes between automatic knowledge base syncs (0 to disable)
}


class SettingsUpdate(BaseModel):
    """Model for updating settings."""
    platform_name: Optional[str] = None
    default_model: Optional[str] = None
    session_timeout: Optional[int] = None
    smtp_server: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_from: Optional[str] = None
    smtp_tls: Optional[bool] = None
    slack_channel: Optional[str] = None
    slack_approval_channel: Optional[str] = None
    confluence_url: Optional[str] = None
    confluence_user: Optional[str] = None
    confluence_token: Optional[str] = None
    jira_url: Optional[str] = None
    jira_user: Optional[str] = None
    jira_token: Optional[str] = None
    jira_project: Optional[str] = None
    approval_timeout: Optional[int] = None
    require_approval_high: Optional[bool] = None
    slack_approvals: Optional[bool] = None
    audit_retention: Optional[int] = None
    audit_tool_results: Optional[bool] = None
    knowledge_sync_interval: Optional[int] = None


def get_setting(db: Session, key: str, default=None):
    """Get a setting value from the database."""
    setting = db.query(Settings).filter(Settings.key == key).first()
    if setting and setting.value is not None:
        return setting.value.get("value", default)
    return default


def set_setting(db: Session, key: str, value):
    """Set a setting value in the database."""
    setting = db.query(Settings).filter(Settings.key == key).first()
    if setting:
        setting.value = {"value": value}
        setting.updated_at = datetime.utcnow()
    else:
        setting = Settings(key=key, value={"value": value})
        db.add(setting)
    db.commit()


@router.get("")
async def get_settings(
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Get all settings."""
    result = {}
    for key, default in DEFAULT_SETTINGS.items():
        result[key] = get_setting(db, key, default)
    return result


@router.put("")
async def update_settings(
    data: SettingsUpdate,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Update settings."""
    update_data = data.model_dump(exclude_unset=True)

    # Don't store empty password/token fields
    sensitive_fields = ["confluence_token", "jira_token"]
    for field in sensitive_fields:
        if field in update_data and not update_data[field]:
            del update_data[field]

    for key, value in update_data.items():
        set_setting(db, key, value)

    audit_log(
        db,
        AuditEventType.SETTINGS_UPDATED,
        user=user,
        resource_type="settings",
        action="update",
        details={"updated_keys": list(update_data.keys())},
    )

    return {"message": "Settings updated"}


@router.get("/models")
async def get_available_models(
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Get list of approved models and the default model."""
    default_model = get_setting(db, "default_model", "gemini-2.5-flash")
    return {
        "models": APPROVED_MODELS,
        "default": default_model,
    }


@router.post("/test-email")
async def test_email(
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Send a test email to the current user."""
    import os
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    smtp_server = get_setting(db, "smtp_server") or os.getenv("SMTP_SERVER")
    smtp_port = int(get_setting(db, "smtp_port") or os.getenv("SMTP_PORT", "25"))
    smtp_from = get_setting(db, "smtp_from") or os.getenv("SMTP_FROM", "netagent@localhost")

    if not smtp_server:
        raise HTTPException(status_code=400, detail="SMTP server not configured")

    def _send_test_email():
        """Send email in thread pool (smtplib is blocking)."""
        try:
            msg = MIMEMultipart()
            msg["From"] = smtp_from
            msg["To"] = user.email
            msg["Subject"] = "NetAgent Test Email"

            body = f"""Hello {user.email},

This is a test email from NetAgent to verify SMTP configuration is working correctly.

SMTP Server: {smtp_server}
SMTP Port: {smtp_port}
From Address: {smtp_from}

If you received this email, email notifications are working!

- NetAgent
"""
            msg.attach(MIMEText(body, "plain"))

            with smtplib.SMTP(smtp_server, smtp_port, timeout=10) as server:
                server.sendmail(smtp_from, [user.email], msg.as_string())

            return {
                "success": True,
                "message": f"Test email sent to {user.email}",
                "smtp_server": smtp_server,
            }
        except smtplib.SMTPException as e:
            return {
                "success": False,
                "message": f"SMTP error: {str(e)}",
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"Failed to send email: {str(e)}",
            }

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor(max_workers=1) as executor:
        result = await loop.run_in_executor(executor, _send_test_email)

    return result


@router.post("/test-confluence")
async def test_confluence(
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Test Confluence connection."""
    import os
    from netagent_core.knowledge.confluence_client import ConfluenceClient

    confluence_url = get_setting(db, "confluence_url") or os.getenv("CONFLUENCE_BASE_URL")
    confluence_username = get_setting(db, "confluence_username") or os.getenv("CONFLUENCE_USERNAME")
    confluence_token = get_setting(db, "confluence_token") or os.getenv("CONFLUENCE_API_TOKEN")

    if not confluence_url:
        raise HTTPException(status_code=400, detail="Confluence URL not configured")

    if not confluence_username or not confluence_token:
        raise HTTPException(status_code=400, detail="Confluence credentials not configured")

    try:
        client = ConfluenceClient(
            base_url=confluence_url,
            username=confluence_username,
            api_token=confluence_token,
        )

        # Test by searching for a simple query
        results = await client.search_pages("test", limit=1)

        return {
            "success": True,
            "message": "Confluence connection successful",
            "confluence_url": confluence_url,
            "is_cloud": client.is_cloud,
            "search_results": len(results),
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Confluence connection failed: {str(e)}",
            "confluence_url": confluence_url,
        }
