"""Skills: 지침을 필요할 때만 읽게 한다.

스킬은 SKILL.md가 든 폴더다. SKILL.md 맨 위에 name과 description을 적는다.

- 시스템 프롬프트에는 스킬 목록(이름과 설명)만 넣는다. 스킬이 많아도 평소 프롬프트가 짧다.
- 모델이 지금 일에 맞는 스킬을 고르면 read_skill Tool로 본문을 읽는다.
- 에이전트 루프는 바뀌지 않는다. 시스템 프롬프트에 목록을 붙이고 Tool 하나를 더할 뿐이다.

실행: python skills.py   (API 키 필요)
"""
import json
from dataclasses import dataclass
from pathlib import Path

from agent_runner import run_agent_from_env
from order_tools import SYSTEM, TOOLS, Tool, ToolError

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path  # SKILL.md 위치


def parse_skill(text: str) -> tuple[dict, str]:
    """SKILL.md를 (머리말, 본문)으로 나눈다. 머리말은 --- 사이의 "키: 값" 줄만 다룬다."""
    if not text.startswith("---"):
        return {}, text
    header, _, body = text[3:].partition("\n---")
    meta = {}
    for line in header.splitlines():
        key, separator, value = line.partition(":")
        if separator:
            meta[key.strip()] = value.strip()
    return meta, body.lstrip("\n")


def load_skills(directory: Path = SKILLS_DIR) -> list[Skill]:
    """폴더마다 SKILL.md의 머리말을 읽어 목록을 만든다. 이름이나 설명이 없는 스킬은 뺀다."""
    skills = []
    for path in sorted(Path(directory).glob("*/SKILL.md")):
        meta, _ = parse_skill(path.read_text(encoding="utf-8"))
        if meta.get("name") and meta.get("description"):
            skills.append(Skill(meta["name"], meta["description"], path))
    return skills


def skill_catalog(skills: list[Skill]) -> str:
    """시스템 프롬프트에 붙일 스킬 목록. 본문은 넣지 않는다."""
    lines = [f"- {skill.name}: {skill.description}" for skill in skills]
    return ("\n\n쓸 수 있는 스킬 목록이다. 문의에 맞는 스킬이 있으면 답하기 전에 read_skill로 본문을 읽고 그 절차를 따른다.\n"
            + "\n".join(lines))


def read_skill_tool(skills: list[Skill]) -> Tool:
    """스킬 본문을 돌려주는 Tool. 목록에 있는 이름만 읽으므로 임의의 파일을 읽게 할 수 없다."""
    by_name = {skill.name: skill for skill in skills}

    def read_skill(name: str) -> str:
        if name not in by_name:
            raise ToolError(f"스킬 {name}이(가) 없다. 쓸 수 있는 스킬: {', '.join(by_name)}")
        return parse_skill(by_name[name].path.read_text(encoding="utf-8"))[1]

    return Tool(
        "read_skill", "스킬 이름으로 그 스킬의 본문(절차와 안내 문구)을 읽는다.",
        {"type": "object",
         "properties": {"name": {"type": "string", "description": "스킬 목록에 있는 이름"}},
         "required": ["name"], "additionalProperties": False},
        read_skill)


# 문의마다 필요한 스킬이 다르다. 모델이 어떤 스킬을 골라 읽는지 Tool 호출 순서로 확인한다.
INQUIRIES = [
    "A-100 주문 금액이 얼마였죠?",                          # 스킬이 필요 없는 문의
    "A-200 주문 언제 도착하나요? 늦어지면 보상이 있나요?",    # delivery-delay
    "A-100 주문 취소해 주세요. 환불은 언제 되나요?",          # order-cancel
]


if __name__ == "__main__":
    skills = load_skills()
    system = SYSTEM + skill_catalog(skills)      # 목록만 붙인다
    tools = [*TOOLS, read_skill_tool(skills)]    # Tool 하나를 더한다
    for inquiry in INQUIRIES:
        calls = []
        result = run_agent_from_env(
            inquiry, system=system, tools=tools,
            on_tool=lambda name, args, output, is_error: calls.append(f"{name}({json.dumps(args, ensure_ascii=False)})"))
        print(f"문의: {inquiry}")
        print(f"Tool 호출: {' → '.join(calls) or '없음'}")
        print(f"답: {result.text if result.ok else f'[중단] {result.reason}'}\n")
