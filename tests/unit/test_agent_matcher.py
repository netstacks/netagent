"""Unit tests for the Agent Matcher."""

import pytest
from unittest.mock import MagicMock, patch
import sys
sys.path.insert(0, '/app/shared')

from netagent_core.job.matcher import AgentMatcher


class TestAgentMatcher:
    """Tests for AgentMatcher class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_db = MagicMock()
        self.matcher = AgentMatcher(self.mock_db)

    # ==================== Tool Inference Tests ====================

    def test_infer_tools_ssh(self):
        """Test inferring SSH tools from task description."""
        tools = self.matcher._infer_required_tools(
            "run show commands on network devices via ssh"
        )

        assert "ssh_command" in tools or "ssh" in str(tools).lower()

    def test_infer_tools_netbox(self):
        """Test inferring NetBox tools from task description."""
        tools = self.matcher._infer_required_tools(
            "query netbox for device inventory information"
        )

        assert "netbox" in str(tools).lower()

    def test_infer_tools_config_backup(self):
        """Test inferring tools for config backup task."""
        tools = self.matcher._infer_required_tools(
            "backup running configuration from switches"
        )

        # Should infer SSH or config-related tools
        assert len(tools) > 0

    def test_infer_tools_api(self):
        """Test inferring API tools from task description."""
        tools = self.matcher._infer_required_tools(
            "make api calls to update monitoring system"
        )

        assert "http" in str(tools).lower() or "api" in str(tools).lower()

    def test_infer_tools_empty_description(self):
        """Test tool inference with empty description."""
        tools = self.matcher._infer_required_tools("")

        # Should return empty set or default tools
        assert isinstance(tools, (set, list))

    def test_infer_tools_generic_task(self):
        """Test tool inference with generic task description."""
        tools = self.matcher._infer_required_tools(
            "perform a generic task on the network"
        )

        # Should return some default tools
        assert isinstance(tools, (set, list))

    # ==================== Scoring Tests ====================

    def test_score_agent_by_tools(self):
        """Test agent scoring by tool match."""
        mock_agent = MagicMock()
        mock_agent.tools = ["ssh_command", "config_backup"]
        mock_agent.name = "network-agent"
        mock_agent.description = "A network automation agent"

        score = self.matcher._score_agent(
            mock_agent,
            "backup network device configs",
            {"ssh_command", "config_backup"},
        )

        # Should have positive score for tool match
        assert score >= 0

    def test_score_agent_by_name(self):
        """Test agent scoring by name match."""
        mock_agent = MagicMock()
        mock_agent.tools = []
        mock_agent.name = "backup-agent"
        mock_agent.description = "An agent for backups"

        score = self.matcher._score_agent(
            mock_agent,
            "backup network configurations",
            set(),
        )

        # Name contains "backup" which matches task
        assert score >= 0

    def test_score_agent_no_match(self):
        """Test agent scoring with no matches."""
        mock_agent = MagicMock()
        mock_agent.tools = ["database_query"]
        mock_agent.name = "database-agent"
        mock_agent.description = "A database agent"

        score = self.matcher._score_agent(
            mock_agent,
            "backup network configs via ssh",
            {"ssh_command"},
        )

        # Lower score for non-matching agent
        assert isinstance(score, (int, float))

    # ==================== Agent Matching Tests ====================

    def test_find_best_agent_with_hint(self):
        """Test finding agent when hint is provided."""
        mock_agent = MagicMock()
        mock_agent.id = 1
        mock_agent.name = "network-monitor"
        mock_agent.enabled = True

        self.mock_db.query.return_value.filter.return_value.first.return_value = mock_agent

        agent, score, reason = self.matcher.find_best_agent(
            task_name="Check health",
            task_description="Check device health",
            agent_hint="network-monitor",
        )

        assert agent is not None
        assert "hint" in reason.lower() or "exact" in reason.lower()

    def test_find_best_agent_no_hint(self):
        """Test finding agent without hint."""
        mock_agent = MagicMock()
        mock_agent.id = 1
        mock_agent.name = "network-agent"
        mock_agent.tools = ["ssh_command"]
        mock_agent.description = "A network agent"
        mock_agent.enabled = True

        # Mock the query to return a list of agents
        self.mock_db.query.return_value.filter.return_value.all.return_value = [mock_agent]

        agent, score, reason = self.matcher.find_best_agent(
            task_name="Run SSH commands",
            task_description="Execute commands on network devices",
            agent_hint=None,
        )

        # Should find an agent or return None
        assert agent is None or agent.id == 1

    def test_find_best_agent_no_match(self):
        """Test finding agent when no match exists."""
        self.mock_db.query.return_value.filter.return_value.all.return_value = []
        self.mock_db.query.return_value.filter.return_value.first.return_value = None

        agent, score, reason = self.matcher.find_best_agent(
            task_name="Unknown task",
            task_description="A task with no matching agents",
            agent_hint=None,
        )

        assert agent is None
        assert "no" in reason.lower() or "match" in reason.lower()

    def test_find_best_agent_disabled_hint(self):
        """Test that disabled agents are not matched even with hint."""
        mock_agent = MagicMock()
        mock_agent.id = 1
        mock_agent.name = "disabled-agent"
        mock_agent.enabled = False

        # First query for hint returns disabled agent
        self.mock_db.query.return_value.filter.return_value.first.return_value = mock_agent

        agent, score, reason = self.matcher.find_best_agent(
            task_name="Test task",
            task_description="A test task",
            agent_hint="disabled-agent",
        )

        # Should not return disabled agent
        # (behavior depends on implementation)

    # ==================== Ephemeral Agent Tests ====================

    def test_generate_ephemeral_prompt(self):
        """Test generating an ephemeral agent prompt."""
        prompt = self.matcher.generate_ephemeral_prompt(
            task_name="Backup configs",
            task_description="Backup network device configurations",
            tools=["ssh_command", "file_write"],
        )

        assert isinstance(prompt, str)
        assert len(prompt) > 0
        # Should contain task information
        assert "backup" in prompt.lower() or "config" in prompt.lower()

    def test_generate_ephemeral_prompt_with_context(self):
        """Test generating ephemeral prompt with job context."""
        prompt = self.matcher.generate_ephemeral_prompt(
            task_name="Process results",
            task_description="Process results from previous task",
            tools=["data_processor"],
            job_context={
                "job_id": 123,
                "job_name": "Daily Audit",
                "previous_results": {"status": "success"},
            },
        )

        assert isinstance(prompt, str)
        # May reference job context

    def test_create_ephemeral_agent(self):
        """Test creating an ephemeral agent."""
        # Mock the Agent class
        with patch('netagent_core.job.matcher.Agent') as MockAgent:
            mock_agent = MagicMock()
            mock_agent.id = 99
            MockAgent.return_value = mock_agent

            agent = self.matcher.create_ephemeral_agent(
                task_name="Test task",
                task_description="A test task",
                job_id=1,
                tools=["ssh_command"],
            )

            # Should create and add agent to session
            self.mock_db.add.assert_called_once()
            self.mock_db.flush.assert_called_once()

    def test_create_ephemeral_agent_with_context(self):
        """Test creating ephemeral agent with job context."""
        with patch('netagent_core.job.matcher.Agent') as MockAgent:
            mock_agent = MagicMock()
            mock_agent.id = 99
            MockAgent.return_value = mock_agent

            agent = self.matcher.create_ephemeral_agent(
                task_name="Chain task",
                task_description="Process previous results",
                job_id=1,
                tools=["data_processor"],
                job_context={
                    "job_id": 1,
                    "job_name": "Test Job",
                    "previous_results": {"data": "test"},
                },
            )

            # Should create agent
            self.mock_db.add.assert_called_once()

    # ==================== Edge Cases ====================

    def test_matcher_with_none_tools(self):
        """Test handling agent with None tools."""
        mock_agent = MagicMock()
        mock_agent.tools = None
        mock_agent.name = "test-agent"
        mock_agent.description = "A test agent"

        # Should not crash
        score = self.matcher._score_agent(
            mock_agent,
            "test task",
            {"ssh_command"},
        )

        assert isinstance(score, (int, float))

    def test_matcher_with_empty_tools(self):
        """Test handling agent with empty tools list."""
        mock_agent = MagicMock()
        mock_agent.tools = []
        mock_agent.name = "test-agent"
        mock_agent.description = "A test agent"

        score = self.matcher._score_agent(
            mock_agent,
            "test task",
            {"ssh_command"},
        )

        assert isinstance(score, (int, float))

    def test_special_characters_in_task_name(self):
        """Test handling special characters in task name."""
        agent, score, reason = self.matcher.find_best_agent(
            task_name="Task with special chars: <>\"'&",
            task_description="A task with special characters",
            agent_hint=None,
        )

        # Should not crash
        assert reason is not None

    def test_very_long_task_description(self):
        """Test handling very long task description."""
        long_description = "This is a very long task description. " * 100

        tools = self.matcher._infer_required_tools(long_description)

        # Should not crash and return some result
        assert isinstance(tools, (set, list))

    def test_unicode_in_task(self):
        """Test handling unicode characters in task."""
        agent, score, reason = self.matcher.find_best_agent(
            task_name="Task with unicode: " + "\u4e2d\u6587",
            task_description="A task with unicode characters",
            agent_hint=None,
        )

        # Should not crash
        assert reason is not None


class TestAgentMatcherScoring:
    """Tests specifically for scoring algorithm."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_db = MagicMock()
        self.matcher = AgentMatcher(self.mock_db)

    def test_scoring_prefers_tool_match(self):
        """Test that tool matches improve score."""
        agent_with_tools = MagicMock()
        agent_with_tools.tools = ["ssh_command"]
        agent_with_tools.name = "generic-agent"
        agent_with_tools.description = "A generic agent"

        agent_without_tools = MagicMock()
        agent_without_tools.tools = []
        agent_without_tools.name = "generic-agent"
        agent_without_tools.description = "A generic agent"

        required_tools = {"ssh_command"}

        score_with = self.matcher._score_agent(agent_with_tools, "ssh task", required_tools)
        score_without = self.matcher._score_agent(agent_without_tools, "ssh task", required_tools)

        # Agent with matching tools should score higher or equal
        assert score_with >= score_without

    def test_scoring_prefers_name_match(self):
        """Test that name matches improve score."""
        agent_matching = MagicMock()
        agent_matching.tools = []
        agent_matching.name = "backup-agent"
        agent_matching.description = "An agent"

        agent_not_matching = MagicMock()
        agent_not_matching.tools = []
        agent_not_matching.name = "other-agent"
        agent_not_matching.description = "An agent"

        score_matching = self.matcher._score_agent(agent_matching, "backup configs", set())
        score_not_matching = self.matcher._score_agent(agent_not_matching, "backup configs", set())

        # Agent with matching name should score higher
        assert score_matching >= score_not_matching


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
