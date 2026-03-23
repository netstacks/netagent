"""Alert pipeline for AI NOC - normalization, dedup, and triage routing."""

from .normalizer import (
    normalize_syslog,
    normalize_splunk,
    normalize_snmp_trap,
    normalize_webhook,
    compute_correlation_key,
)

__all__ = [
    "normalize_syslog",
    "normalize_splunk",
    "normalize_snmp_trap",
    "normalize_webhook",
    "compute_correlation_key",
]
