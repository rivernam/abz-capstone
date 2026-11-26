"""LLM 호출 래퍼. 서버 러너와 시험에서 같은 인터페이스를 쓴다.

키는 환경변수(ANTHROPIC_API_KEY)에서 SDK가 읽는다. 코드와 기록에 키를 남기지 않는다.
"""
import json
import os
import re
from pathlib import Path

from abzlib import paths


class LLMError(Exception):
    pass


def load_system_prompt(skill_name):
    """CLAUDE.md와 Skill 본문(frontmatter 제외)을 이어 시스템 프롬프트로 쓴다."""
    parts = []
    claude_md = paths.ROOT / "CLAUDE.md"
    if claude_md.exists():
        parts.append(claude_md.read_text(encoding="utf-8"))
    skill = (paths.SKILLS_DIR / skill_name / "SKILL.md").read_text(encoding="utf-8")
    parts.append(re.sub(r"\A---\n.*?\n---\n", "", skill, flags=re.S))
    return "\n\n".join(parts)


class ClaudeJSON:
    """구조화 출력(JSON schema)으로 한 번 호출한다."""

    def __init__(self, model=None, effort="medium", max_tokens=16000):
        import anthropic
        self.client = anthropic.Anthropic()
        self.model = model or os.environ.get("ABZ_REPORT_MODEL", "claude-haiku-5-5")
        self.effort = effort
        self.max_tokens = max_tokens

    def call(self, system, user, schema):
        import anthropic
        try:
            resp = self.client.messages.create(
                model=self.model, max_tokens=self.max_tokens, system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
            )
        except anthropic.RateLimitError as e:
            raise LLMError(f"요청 한도 초과: {e}") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"API 오류 {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError(f"연결 실패: {e}") from e
        if resp.stop_reason == "refusal":
            raise LLMError(f"모델이 응답을 거절했다: {resp.stop_details}")
        if resp.stop_reason == "max_tokens":
            raise LLMError("출력이 max_tokens에서 잘렸다.")
        text = next(b.text for b in resp.content if b.type == "text")
        usage = {"model": resp.model, "input_tokens": resp.usage.input_tokens,
                 "output_tokens": resp.usage.output_tokens}
        return json.loads(text), usage
