"""Confluence API client for fetching wiki pages."""

import os
import logging
import re
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from html.parser import HTMLParser

import httpx

logger = logging.getLogger(__name__)


class HTMLToTextParser(HTMLParser):
    """Convert HTML to plain text."""

    def __init__(self):
        super().__init__()
        self.text = []
        self.skip_tags = {"script", "style", "head", "meta"}
        self.current_tag = None

    def handle_starttag(self, tag, attrs):
        self.current_tag = tag
        if tag in ("p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"):
            self.text.append("\n")

    def handle_endtag(self, tag):
        if tag in ("p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"):
            self.text.append("\n")
        self.current_tag = None

    def handle_data(self, data):
        if self.current_tag not in self.skip_tags:
            self.text.append(data)

    def get_text(self) -> str:
        text = "".join(self.text)
        # Clean up whitespace
        text = re.sub(r"\n\s*\n", "\n\n", text)
        text = re.sub(r" +", " ", text)
        return text.strip()


def html_to_text(html: str) -> str:
    """Convert HTML to plain text."""
    parser = HTMLToTextParser()
    parser.feed(html)
    return parser.get_text()


@dataclass
class ConfluencePage:
    """Represents a Confluence page."""

    id: str
    title: str
    url: str
    body: str  # Plain text content
    space_key: str
    parent_id: Optional[str] = None


class ConfluenceClient:
    """Client for Confluence REST API.

    Supports both Confluence Cloud and Server/Data Center.

    Usage:
        client = ConfluenceClient(
            base_url="https://wiki.example.com",
            username="user@example.com",
            api_token="your-api-token"
        )
        pages = await client.get_page_tree(parent_page_id="12345")
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        username: Optional[str] = None,
        api_token: Optional[str] = None,
    ):
        """Initialize Confluence client.

        Args:
            base_url: Confluence base URL (or CONFLUENCE_BASE_URL env var)
            username: Username for authentication (or CONFLUENCE_USERNAME env var)
            api_token: API token for authentication (or CONFLUENCE_API_TOKEN env var)
        """
        raw_url = (base_url or os.getenv("CONFLUENCE_BASE_URL", "")).rstrip("/")
        self.username = username or os.getenv("CONFLUENCE_USERNAME", "")
        self.api_token = api_token or os.getenv("CONFLUENCE_API_TOKEN", "")

        if not raw_url:
            raise ValueError("Confluence base URL is required")

        # Extract just the base URL (scheme + host) - strip any path components
        # Users sometimes provide full page URLs instead of just the wiki server URL
        from urllib.parse import urlparse
        parsed = urlparse(raw_url)
        self.base_url = f"{parsed.scheme}://{parsed.netloc}"

        # Determine if this is Confluence Cloud or Server
        self.is_cloud = "atlassian.net" in self.base_url

        # Build API base URL
        if self.is_cloud:
            self.api_base = f"{self.base_url}/wiki/rest/api"
        else:
            self.api_base = f"{self.base_url}/rest/api"

        logger.debug(f"Confluence base URL: {self.base_url}, API base: {self.api_base}")

    def _get_auth(self) -> Optional[httpx.BasicAuth]:
        """Get Basic authentication for requests (Confluence Cloud)."""
        if self.is_cloud and self.username and self.api_token:
            return httpx.BasicAuth(self.username, self.api_token)
        return None

    def _get_headers(self) -> dict:
        """Get request headers including Bearer token for Server/Data Center."""
        headers = {}
        # Confluence Server/Data Center uses Bearer token (Personal Access Token)
        if not self.is_cloud and self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        return headers

    async def get_page(self, page_id: str) -> ConfluencePage:
        """Get a single page by ID.

        Args:
            page_id: Confluence page ID

        Returns:
            ConfluencePage object
        """
        url = f"{self.api_base}/content/{page_id}"
        params = {"expand": "body.storage,space,ancestors"}

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                url,
                params=params,
                headers=self._get_headers(),
                auth=self._get_auth(),
            )
            response.raise_for_status()
            data = response.json()

        return self._parse_page(data)

    async def get_page_tree(
        self,
        parent_page_id: str,
        max_depth: int = 10,
    ) -> List[ConfluencePage]:
        """Get all pages under a parent page (recursive).

        Args:
            parent_page_id: ID of the parent page
            max_depth: Maximum depth to traverse

        Returns:
            List of ConfluencePage objects
        """
        pages = []
        await self._get_children_recursive(parent_page_id, pages, 0, max_depth)
        return pages

    async def _get_children_recursive(
        self,
        page_id: str,
        pages: List[ConfluencePage],
        current_depth: int,
        max_depth: int,
    ):
        """Recursively fetch child pages."""
        if current_depth > max_depth:
            return

        # Get the page itself first
        try:
            page = await self.get_page(page_id)
            pages.append(page)
            logger.debug(f"Fetched page: {page.title}")
        except Exception as e:
            logger.error(f"Failed to fetch page {page_id}: {e}")
            return

        # Get children
        children = await self.get_child_pages(page_id)
        for child in children:
            await self._get_children_recursive(
                child["id"],
                pages,
                current_depth + 1,
                max_depth,
            )

    async def get_child_pages(self, page_id: str) -> List[Dict[str, Any]]:
        """Get immediate child pages of a page.

        Args:
            page_id: Parent page ID

        Returns:
            List of child page metadata (id, title)
        """
        url = f"{self.api_base}/content/{page_id}/child/page"
        params = {"limit": 100}

        children = []
        async with httpx.AsyncClient(timeout=30.0) as client:
            while True:
                response = await client.get(
                    url,
                    params=params,
                    headers=self._get_headers(),
                    auth=self._get_auth(),
                )
                response.raise_for_status()
                data = response.json()

                for result in data.get("results", []):
                    children.append({
                        "id": result["id"],
                        "title": result["title"],
                    })

                # Check for more pages
                links = data.get("_links", {})
                if "next" in links:
                    url = f"{self.base_url}{links['next']}"
                    params = {}
                else:
                    break

        return children

    async def get_space_pages(
        self,
        space_key: str,
        limit: int = 500,
    ) -> List[ConfluencePage]:
        """Get all pages in a space.

        Args:
            space_key: Confluence space key
            limit: Maximum number of pages to return

        Returns:
            List of ConfluencePage objects
        """
        url = f"{self.api_base}/content"
        params = {
            "spaceKey": space_key,
            "type": "page",
            "expand": "body.storage,space,ancestors",
            "limit": min(limit, 100),
        }

        pages = []
        async with httpx.AsyncClient(timeout=30.0) as client:
            while len(pages) < limit:
                response = await client.get(
                    url,
                    params=params,
                    headers=self._get_headers(),
                    auth=self._get_auth(),
                )
                response.raise_for_status()
                data = response.json()

                for result in data.get("results", []):
                    pages.append(self._parse_page(result))

                # Check for more pages
                links = data.get("_links", {})
                if "next" in links and len(pages) < limit:
                    url = f"{self.base_url}{links['next']}"
                    params = {}
                else:
                    break

        return pages[:limit]

    async def search_pages(
        self,
        query: str,
        space_key: Optional[str] = None,
        limit: int = 25,
    ) -> List[Dict[str, Any]]:
        """Search for pages using CQL.

        Args:
            query: Search query
            space_key: Optional space to limit search
            limit: Maximum results

        Returns:
            List of search results with id, title, url
        """
        cql = f'type=page AND text ~ "{query}"'
        if space_key:
            cql += f' AND space="{space_key}"'

        url = f"{self.api_base}/content/search"
        params = {
            "cql": cql,
            "limit": limit,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                url,
                params=params,
                headers=self._get_headers(),
                auth=self._get_auth(),
            )
            response.raise_for_status()
            data = response.json()

        results = []
        for result in data.get("results", []):
            results.append({
                "id": result["id"],
                "title": result["title"],
                "url": f"{self.base_url}/wiki/spaces/{result['space']['key']}/pages/{result['id']}",
            })

        return results

    def _parse_page(self, data: Dict[str, Any]) -> ConfluencePage:
        """Parse API response into ConfluencePage."""
        body_html = data.get("body", {}).get("storage", {}).get("value", "")
        body_text = html_to_text(body_html)

        space_key = data.get("space", {}).get("key", "")

        # Build URL
        if self.is_cloud:
            url = f"{self.base_url}/wiki/spaces/{space_key}/pages/{data['id']}"
        else:
            url = f"{self.base_url}/pages/viewpage.action?pageId={data['id']}"

        # Get parent ID
        ancestors = data.get("ancestors", [])
        parent_id = ancestors[-1]["id"] if ancestors else None

        return ConfluencePage(
            id=data["id"],
            title=data["title"],
            url=url,
            body=body_text,
            space_key=space_key,
            parent_id=parent_id,
        )
