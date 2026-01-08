# shared/netagent_core/redis_events.py
"""Redis pub/sub utilities for real-time session events."""

import json
import logging
import os

import redis
from redis import Redis

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Channel patterns
SESSION_EVENTS_CHANNEL = "session:{session_id}:events"
SESSIONS_LIVE_CHANNEL = "sessions:live"
SESSION_CANCEL_KEY = "session:{session_id}:cancel_flag"


def get_redis_client() -> Redis:
    """Get a Redis client instance."""
    return redis.from_url(REDIS_URL, decode_responses=True)


def publish_session_event(session_id: int, event_type: str, data: dict) -> None:
    """Publish an event for a specific session.

    Args:
        session_id: The session ID
        event_type: Type of event (e.g., 'approval_resolved', 'progress', 'completed')
        data: Event data
    """
    client = get_redis_client()
    channel = SESSION_EVENTS_CHANNEL.format(session_id=session_id)
    message = json.dumps({"type": event_type, **data})
    client.publish(channel, message)
    logger.debug(f"Published {event_type} to {channel}")


def publish_live_session_event(event_type: str, data: dict) -> None:
    """Publish an event to the global live sessions channel.

    Args:
        event_type: Type of event (e.g., 'session_started', 'session_completed')
        data: Event data including session_id
    """
    client = get_redis_client()
    message = json.dumps({"type": event_type, **data})
    client.publish(SESSIONS_LIVE_CHANNEL, message)
    logger.debug(f"Published {event_type} to {SESSIONS_LIVE_CHANNEL}")


def set_cancel_flag(session_id: int, ttl_seconds: int = 3600) -> None:
    """Set the cancellation flag for a session.

    Args:
        session_id: The session ID
        ttl_seconds: Time-to-live for the flag (default 1 hour)
    """
    client = get_redis_client()
    key = SESSION_CANCEL_KEY.format(session_id=session_id)
    client.set(key, "1", ex=ttl_seconds)
    logger.info(f"Set cancel flag for session {session_id}")


def check_cancel_flag(session_id: int) -> bool:
    """Check if a session has been cancelled.

    Args:
        session_id: The session ID

    Returns:
        True if session is cancelled, False otherwise
    """
    client = get_redis_client()
    key = SESSION_CANCEL_KEY.format(session_id=session_id)
    return client.get(key) is not None


def clear_cancel_flag(session_id: int) -> None:
    """Clear the cancellation flag for a session.

    Args:
        session_id: The session ID
    """
    client = get_redis_client()
    key = SESSION_CANCEL_KEY.format(session_id=session_id)
    client.delete(key)
