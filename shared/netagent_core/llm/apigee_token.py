"""Apigee OAuth token manager for Gemini API access.

Handles OAuth client credentials flow with automatic token refresh.
Token TTL is 30 minutes, refresh happens 60 seconds before expiry.
"""

import os
import time
import base64
import logging
import threading
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


@dataclass
class TokenInfo:
    """OAuth token information."""

    access_token: str
    token_type: str
    expires_at: float  # Unix timestamp


class ApigeeTokenManager:
    """Thread-safe OAuth token manager for Apigee.

    Usage:
        token_manager = ApigeeTokenManager()
        token = token_manager.get_token()  # Returns valid access token
    """

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        token_url: Optional[str] = None,
        refresh_buffer_seconds: int = 60,
    ):
        """Initialize token manager.

        Args:
            client_id: Apigee OAuth client ID (or APIGEE_CLIENT_ID env var)
            client_secret: Apigee OAuth client secret (or APIGEE_CLIENT_SECRET env var)
            token_url: Apigee token endpoint URL (or APIGEE_TOKEN_URL env var)
            refresh_buffer_seconds: Refresh token this many seconds before expiry
        """
        self.client_id = client_id or os.getenv("APIGEE_CLIENT_ID")
        self.client_secret = client_secret or os.getenv("APIGEE_CLIENT_SECRET")
        self.token_url = token_url or os.getenv("APIGEE_TOKEN_URL")

        if not all([self.client_id, self.client_secret, self.token_url]):
            raise ValueError(
                "Apigee credentials required. Set APIGEE_CLIENT_ID, "
                "APIGEE_CLIENT_SECRET, and APIGEE_TOKEN_URL environment variables."
            )

        self.refresh_buffer = refresh_buffer_seconds
        self._token: Optional[TokenInfo] = None
        self._lock = threading.Lock()

    def _is_token_valid(self) -> bool:
        """Check if current token is valid and not about to expire."""
        if not self._token:
            return False

        # Check if token expires within the buffer period
        return time.time() < (self._token.expires_at - self.refresh_buffer)

    def _fetch_token(self) -> TokenInfo:
        """Fetch new token from Apigee OAuth endpoint."""
        # Create Basic auth header
        credentials = f"{self.client_id}:{self.client_secret}"
        encoded = base64.b64encode(credentials.encode()).decode()

        headers = {
            "Authorization": f"Basic {encoded}",
            "Content-Type": "application/x-www-form-urlencoded",
        }

        data = {
            "grant_type": "client_credentials",
        }

        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(
                    self.token_url,
                    headers=headers,
                    data=data,
                )
                response.raise_for_status()

                token_data = response.json()

                # Calculate expiry time
                expires_in = token_data.get("expires_in", 1800)  # Default 30 min
                expires_at = time.time() + expires_in

                return TokenInfo(
                    access_token=token_data["access_token"],
                    token_type=token_data.get("token_type", "Bearer"),
                    expires_at=expires_at,
                )

        except httpx.HTTPStatusError as e:
            logger.error(f"Token fetch failed: {e.response.status_code} - {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Token fetch error: {e}")
            raise

    def get_token(self) -> str:
        """Get valid access token, refreshing if necessary.

        This method is thread-safe.

        Returns:
            Valid access token string
        """
        # Quick check without lock
        if self._is_token_valid():
            return self._token.access_token

        # Need to refresh - acquire lock
        with self._lock:
            # Double-check after acquiring lock
            if self._is_token_valid():
                return self._token.access_token

            logger.info("Refreshing Apigee OAuth token")
            self._token = self._fetch_token()
            logger.info(f"Token refreshed, expires at {self._token.expires_at}")

            return self._token.access_token

    def force_refresh(self) -> str:
        """Force token refresh regardless of expiry.

        Returns:
            New access token string
        """
        with self._lock:
            logger.info("Forcing Apigee OAuth token refresh")
            self._token = self._fetch_token()
            return self._token.access_token

    @property
    def token_expires_at(self) -> Optional[float]:
        """Get current token expiry timestamp."""
        return self._token.expires_at if self._token else None

    @property
    def token_expires_in(self) -> Optional[float]:
        """Get seconds until current token expires."""
        if not self._token:
            return None
        return max(0, self._token.expires_at - time.time())


# Global singleton instance
_token_manager: Optional[ApigeeTokenManager] = None


def get_token_manager() -> ApigeeTokenManager:
    """Get or create global token manager instance."""
    global _token_manager
    if _token_manager is None:
        _token_manager = ApigeeTokenManager()
    return _token_manager
