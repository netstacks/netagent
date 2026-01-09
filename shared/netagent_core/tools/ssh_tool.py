"""SSH command tool for network device access.

Provides read-only SSH access to network devices using stored credentials.
Supports command filtering to prevent configuration changes.
"""

import asyncio
import fnmatch
import logging
import re
from typing import List, Optional, Dict, Any

from sqlalchemy.orm import Session

from ..llm.agent_executor import ToolDefinition

logger = logging.getLogger(__name__)

# Dangerous command patterns that should be blocked
BLOCKED_PATTERNS = [
    r"^conf",  # configure, conf t
    r"^write",  # write mem
    r"^copy",  # copy running-config
    r"^delete",
    r"^erase",
    r"^format",
    r"^reload",
    r"^reboot",
    r"^shutdown$",  # interface shutdown is ok in show commands
    r"^no\s+",  # no commands
    r"^clear\s+(?!counters)",  # clear except clear counters (which is read-only info)
    r"^debug",
    r"^undebug",
    r"^terminal\s+(?!length|width)",  # terminal except display settings
    r"^request\s+system",
    r"^set\s+",
    r"^edit\s+",
    r"^commit",
    r"^rollback",
]

# Safe command patterns (show, display, ping, traceroute)
SAFE_PATTERNS = [
    r"^show\s+",
    r"^display\s+",
    r"^get\s+",
    r"^ping\s+",
    r"^traceroute\s+",
    r"^tracert\s+",
    r"^who$",
    r"^whoami$",
    r"^uptime$",
    r"^hostname$",
    r"^uname\s+",
    r"^cat\s+/proc/",  # Linux system info
    r"^ip\s+(addr|route|link|neigh)",  # Linux networking
    r"^netstat\s+",
    r"^ss\s+",
    r"^arp\s+",
    r"^route\s+",
    r"^ifconfig\s*",
]


def is_command_safe(command: str) -> tuple[bool, str]:
    """Check if a command is safe to execute.

    Args:
        command: The command to check

    Returns:
        Tuple of (is_safe, reason)
    """
    command = command.strip().lower()

    # Check blocked patterns first
    for pattern in BLOCKED_PATTERNS:
        if re.match(pattern, command, re.IGNORECASE):
            return False, f"Command matches blocked pattern: {pattern}"

    # Check if command matches safe patterns
    for pattern in SAFE_PATTERNS:
        if re.match(pattern, command, re.IGNORECASE):
            return True, "Command matches safe pattern"

    # Default: block unknown commands
    return False, "Command does not match any known safe pattern"


def is_device_allowed(
    hostname: str,
    allowed_patterns: List[str],
) -> bool:
    """Check if a device hostname matches allowed patterns.

    Args:
        hostname: Device hostname to check
        allowed_patterns: List of glob patterns (e.g., ["router-*", "switch-nyc-*"])

    Returns:
        True if device matches any allowed pattern
    """
    hostname = hostname.lower()
    for pattern in allowed_patterns:
        if fnmatch.fnmatch(hostname, pattern.lower()):
            return True
    return False


class SSHCommandTool:
    """Tool for executing read-only SSH commands on network devices."""

    name = "ssh_command"
    description = """Execute a read-only command on a network device via SSH.
Only show/display commands and basic diagnostics (ping, traceroute) are allowed.
Configuration commands are blocked for safety."""

    parameters = {
        "type": "object",
        "properties": {
            "hostname": {
                "type": "string",
                "description": "Hostname or IP address of the network device",
            },
            "command": {
                "type": "string",
                "description": "Command to execute (must be read-only, e.g., show commands)",
            },
        },
        "required": ["hostname", "command"],
    }

    requires_approval = False
    risk_level = "low"

    def __init__(
        self,
        allowed_device_patterns: List[str] = None,
        db_session_factory=None,
        encryption_key: str = None,
    ):
        """Initialize SSH tool.

        Args:
            allowed_device_patterns: Glob patterns for allowed devices
            db_session_factory: Factory for database sessions (to get credentials)
            encryption_key: Key for decrypting stored credentials
        """
        self.allowed_device_patterns = allowed_device_patterns or ["*"]
        self.db_session_factory = db_session_factory
        self.encryption_key = encryption_key

    async def execute(self, hostname: str, command: str) -> str:
        """Execute SSH command on device.

        Args:
            hostname: Device hostname or IP
            command: Command to execute

        Returns:
            Command output or error message
        """
        # Validate device is allowed
        if not is_device_allowed(hostname, self.allowed_device_patterns):
            return f"Error: Device '{hostname}' is not in the allowed device list"

        # Validate command is safe
        is_safe, reason = is_command_safe(command)
        if not is_safe:
            return f"Error: Command blocked - {reason}. Only read-only commands (show, display, ping, traceroute) are allowed."

        # Get credentials for device
        credentials = await self._get_credentials(hostname)
        if not credentials:
            return f"Error: No credentials found for device '{hostname}'"

        # Execute command
        try:
            output = await self._ssh_execute(
                hostname=hostname,
                command=command,
                username=credentials["username"],
                password=credentials["password"],
                device_type=credentials.get("device_type", "autodetect"),
                port=credentials.get("port", 22),
            )
            return output
        except Exception as e:
            logger.error(f"SSH execution error: {e}")
            return f"Error executing command: {str(e)}"

    async def _get_credentials(self, hostname: str) -> Optional[Dict[str, Any]]:
        """Get credentials for a device from database.

        Args:
            hostname: Device hostname

        Returns:
            Credentials dict or None
        """
        if not self.db_session_factory:
            # Return mock credentials for testing
            return {
                "username": "admin",
                "password": "admin",
                "device_type": "autodetect",
                "port": 22,
            }

        try:
            from ..db import DeviceCredential
            from ..utils.encryption import decrypt_value

            with self.db_session_factory() as db:
                # Find matching credentials (highest priority first)
                credentials = (
                    db.query(DeviceCredential)
                    .filter(DeviceCredential.enabled == True)
                    .order_by(DeviceCredential.priority.desc())
                    .all()
                )

                for cred in credentials:
                    # Check if hostname matches any pattern
                    for pattern in cred.device_patterns:
                        # Support both glob patterns (fnmatch) and regex patterns
                        matched = False
                        # First try fnmatch (glob-style: *, ?, [])
                        if fnmatch.fnmatch(hostname.lower(), pattern.lower()):
                            matched = True
                        # If not matched and pattern looks like regex, try regex
                        elif any(c in pattern for c in ['.', '^', '$', '+', '\\', '|', '(', ')']):
                            try:
                                import re
                                if re.match(pattern, hostname, re.IGNORECASE):
                                    matched = True
                            except re.error:
                                pass  # Invalid regex, ignore
                        if matched:
                            return {
                                "username": decrypt_value(cred.username_encrypted),
                                "password": decrypt_value(cred.password_encrypted),
                                "device_type": cred.device_type,
                                "port": cred.port,
                            }

                return None
        except Exception as e:
            logger.error(f"Error getting credentials: {e}")
            return None

    async def _ssh_execute(
        self,
        hostname: str,
        command: str,
        username: str,
        password: str,
        device_type: str = "autodetect",
        port: int = 22,
    ) -> str:
        """Execute SSH command using netmiko.

        Args:
            hostname: Device hostname/IP
            command: Command to run
            username: SSH username
            password: SSH password
            device_type: Netmiko device type
            port: SSH port

        Returns:
            Command output
        """
        try:
            import netmiko
            from netmiko import ConnectHandler
            from netmiko.exceptions import NetmikoTimeoutException, NetmikoAuthenticationException
        except ImportError:
            return "Error: netmiko library not installed"

        # Run in thread pool to avoid blocking
        loop = asyncio.get_event_loop()

        def _connect_and_run():
            device = {
                "device_type": device_type,
                "host": hostname,
                "username": username,
                "password": password,
                "port": port,
                "timeout": 30,
            }

            try:
                with ConnectHandler(**device) as conn:
                    output = conn.send_command(
                        command,
                        read_timeout=60,
                        strip_command=True,
                        strip_prompt=True,
                    )
                    return output
            except NetmikoTimeoutException:
                return f"Error: Connection to {hostname} timed out"
            except NetmikoAuthenticationException:
                return f"Error: Authentication failed for {hostname}"
            except Exception as e:
                return f"Error: {str(e)}"

        try:
            output = await loop.run_in_executor(None, _connect_and_run)
            return output
        except Exception as e:
            return f"Error: {str(e)}"


def create_ssh_tool(
    allowed_device_patterns: List[str] = None,
    db_session_factory=None,
    encryption_key: str = None,
) -> ToolDefinition:
    """Create SSH tool definition for agent executor.

    Args:
        allowed_device_patterns: Device patterns this agent can access
        db_session_factory: Database session factory
        encryption_key: Encryption key for credentials

    Returns:
        ToolDefinition for the SSH tool
    """
    tool = SSHCommandTool(
        allowed_device_patterns=allowed_device_patterns,
        db_session_factory=db_session_factory,
        encryption_key=encryption_key,
    )

    return ToolDefinition(
        name=tool.name,
        description=tool.description,
        parameters=tool.parameters,
        handler=tool.execute,
        requires_approval=tool.requires_approval,
        risk_level=tool.risk_level,
    )
