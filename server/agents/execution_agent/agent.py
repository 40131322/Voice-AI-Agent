"""Execution Agent implementation."""

from pathlib import Path
from typing import List, Optional, Dict, Any

from ...services.execution import get_execution_agent_logs
from ...logging_config import logger
from .roles import CALENDAR_AGENT, GMAIL_AGENT, ROLE_CALENDAR, resolve_role


# Load system prompt templates from files, one per role.
_DIR = Path(__file__).parent


def _load_template(filename: str) -> Optional[str]:
    path = _DIR / filename
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return None


_FALLBACK_TEMPLATE = """You are an execution agent responsible for completing specific tasks using available tools.

Agent Name: {agent_name}
Purpose: {agent_purpose}

Analyze what needs to be done, use the appropriate tools, and provide clear status updates on your actions."""

# Gmail/general agents share the original prompt; calendar agents get their own.
SYSTEM_PROMPT_TEMPLATE = _load_template("system_prompt.md") or _FALLBACK_TEMPLATE
CALENDAR_PROMPT_TEMPLATE = _load_template("system_prompt_calendar.md") or _FALLBACK_TEMPLATE

# Appended to every specialized agent so they know how to collaborate.
_INTER_AGENT_GUIDANCE = f"""

# Working With Other Agents
You are one of several specialized agents and can delegate to another agent using the `message_agent` tool, which returns that agent's reply.
- `{GMAIL_AGENT}` handles sending and searching email.
- `{CALENDAR_AGENT}` handles calendar and scheduling.
When a task needs a capability you don't have (e.g. you manage the calendar but need to email an invite, or vice versa), call `message_agent` with the other agent's name and clear, self-contained instructions, then use its response to finish your task."""


class ExecutionAgent:
    """Manages state and history for an execution agent."""

    # Initialize execution agent with name, conversation limits, and log store access
    def __init__(
        self,
        name: str,
        conversation_limit: Optional[int] = None
    ):
        """
        Initialize an execution agent.

        Args:
            name: Human-readable agent name (e.g., 'conversation with keith')
            conversation_limit: Optional limit on past conversations to include (None = all)
        """
        self.name = name
        self.conversation_limit = conversation_limit
        self._log_store = get_execution_agent_logs()

    # Generate system prompt template with agent name and purpose derived from name
    def build_system_prompt(self) -> str:
        """Build the role-appropriate system prompt for this agent."""
        role = resolve_role(self.name)
        agent_purpose = f"Handle tasks related to: {self.name}"

        template = CALENDAR_PROMPT_TEMPLATE if role == ROLE_CALENDAR else SYSTEM_PROMPT_TEMPLATE
        prompt = template.format(agent_name=self.name, agent_purpose=agent_purpose)

        # Give the dedicated gmail/calendar agents awareness of how to collaborate.
        if self.name.strip().lower() in {GMAIL_AGENT, CALENDAR_AGENT}:
            prompt = f"{prompt}{_INTER_AGENT_GUIDANCE}"

        return prompt

    # Combine base system prompt with conversation history, applying conversation limits
    def build_system_prompt_with_history(self) -> str:
        """
        Build system prompt including agent history.

        Returns:
            System prompt with embedded history transcript
        """
        base_prompt = self.build_system_prompt()

        # Load history transcript
        transcript = self._log_store.load_transcript(self.name)

        if transcript:
            # Apply conversation limit if needed
            if self.conversation_limit and self.conversation_limit > 0:
                # Parse entries and limit them
                lines = transcript.split('\n')
                request_count = sum(1 for line in lines if '<agent_request' in line)

                if request_count > self.conversation_limit:
                    # Find where to cut
                    kept_requests = 0
                    cutoff_index = len(lines)
                    for i in range(len(lines) - 1, -1, -1):
                        if '<agent_request' in lines[i]:
                            kept_requests += 1
                            if kept_requests == self.conversation_limit:
                                cutoff_index = i
                                break
                    transcript = '\n'.join(lines[cutoff_index:])

            return f"{base_prompt}\n\n# Execution History\n\n{transcript}"

        return base_prompt

    # Format current instruction as user message for LLM consumption
    def build_messages_for_llm(self, current_instruction: str) -> List[Dict[str, str]]:
        """
        Build message array for LLM call.

        Args:
            current_instruction: Current instruction from interaction agent

        Returns:
            List of messages in OpenRouter format
        """
        return [
            {"role": "user", "content": current_instruction}
        ]

    # Log the agent's final response to the execution log store
    def record_response(self, response: str) -> None:
        """Record agent's response to the log."""
        self._log_store.record_agent_response(self.name, response)

    # Log tool invocation and results with truncated content for readability
    def record_tool_execution(self, tool_name: str, arguments: str, result: str) -> None:
        """Record tool execution details."""
        self._log_store.record_action(self.name, f"Calling {tool_name} with: {arguments[:200]}")
        # Record the tool response
        self._log_store.record_tool_response(self.name, tool_name, result[:500])
