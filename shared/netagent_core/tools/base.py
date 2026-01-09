"""Base tool class for agent tools."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class ToolResult:
    """Result from a tool execution."""
    success: bool
    output: str
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "metadata": self.metadata,
        }


class BaseTool(ABC):
    """Base class for all agent tools.

    Tools provide capabilities to agents, such as SSH commands,
    file operations, API calls, memory access, etc.
    """

    # Tool metadata - override in subclasses
    name: str = "base_tool"
    description: str = "A base tool"
    parameters: Dict[str, Any] = {}

    # Execution settings
    requires_approval: bool = False
    risk_level: str = "low"  # low, medium, high

    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult:
        """Execute the tool with given parameters.

        Args:
            **kwargs: Tool-specific parameters

        Returns:
            ToolResult with success status and output
        """
        pass

    def get_schema(self) -> Dict[str, Any]:
        """Get the JSON schema for this tool."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "requires_approval": self.requires_approval,
            "risk_level": self.risk_level,
        }

    def validate_params(self, params: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate parameters against schema.

        Args:
            params: Parameters to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        required = self.parameters.get("required", [])
        for param in required:
            if param not in params:
                return False, f"Missing required parameter: {param}"
        return True, None
