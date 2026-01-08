"""NetAgent Core - Shared library for NetAgent platform."""

__version__ = "0.1.0"

from .redis_events import (
    get_redis_client,
    publish_session_event,
    publish_live_session_event,
    set_cancel_flag,
    check_cancel_flag,
    clear_cancel_flag,
    REDIS_URL,
    SESSIONS_LIVE_CHANNEL,
)
