"""Utility modules."""

# Import encryption utilities directly (no fastapi dependency)
from .encryption import encrypt_value, decrypt_value, get_fernet

# Lazy import for audit utilities (requires fastapi via alb_auth)
# Only import when actually needed to avoid breaking worker


def get_setting(db, key: str, default=None):
    """Get a setting value from the database.

    Args:
        db: SQLAlchemy session
        key: Setting key name
        default: Default value if setting not found

    Returns:
        Setting value or default
    """
    from netagent_core.db import Settings
    setting = db.query(Settings).filter(Settings.key == key).first()
    if setting and setting.value is not None:
        return setting.value.get("value", default)
    return default


def __getattr__(name):
    """Lazy import for audit utilities."""
    if name in ("audit_log", "AuditEventType"):
        from .audit import audit_log, AuditEventType
        globals()["audit_log"] = audit_log
        globals()["AuditEventType"] = AuditEventType
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "encrypt_value",
    "decrypt_value",
    "get_fernet",
    "get_setting",
    "audit_log",
    "AuditEventType",
]
