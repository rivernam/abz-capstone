"""환경변수로 고른 API로 에이전트를 돌린다. Skills와 MCP 샘플이 함께 쓴다.

LLM_PROVIDER=openai 이면 OpenAI 호환 API, 그 외에는 Claude API다.
"""
import os

from agent_result import AgentResult


def run_agent_from_env(inquiry: str, **options) -> AgentResult:
    """options(system, tools, on_tool)는 두 루프가 똑같이 받는다."""
    if os.environ.get("LLM_PROVIDER") == "openai":
        from openai_compat_agent import run_agent
    else:
        from claude_agent import run_agent
    return run_agent(inquiry, **options)
