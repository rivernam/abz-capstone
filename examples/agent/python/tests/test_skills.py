"""Skills 시험. 가짜 클라이언트를 쓰므로 API 키가 필요 없다."""
import json
import types

import claude_agent
import openai_compat_agent
import order_tools
import skills

B = types.SimpleNamespace


class FakeClient:
    def __init__(self, script):
        self.script, self.requests = script, []
        self.messages = self
        self.chat = B(completions=self)

    def create(self, **request):
        self.requests.append(json.loads(json.dumps(request, default=vars, ensure_ascii=False)))
        return self.script[len(self.requests) - 1]


def make_skill(directory, name, header, body):
    folder = directory / name
    folder.mkdir()
    (folder / "SKILL.md").write_text(f"---\n{header}\n---\n\n{body}", encoding="utf-8")


def test_bundled_skills_load_with_name_and_description():
    loaded = skills.load_skills()
    assert [skill.name for skill in loaded] == ["delivery-delay", "order-cancel"]
    assert all(skill.description for skill in loaded)


def test_catalog_lists_descriptions_but_not_bodies():
    catalog = skills.skill_catalog(skills.load_skills())
    assert "- order-cancel: " in catalog and "read_skill" in catalog
    assert "영업일 기준 3일" not in catalog  # 본문은 읽을 때만 들어온다


def test_skills_without_name_or_description_are_skipped(tmp_path):
    make_skill(tmp_path, "good", "name: good\ndescription: 쓸 때: 항상", "본문")
    make_skill(tmp_path, "no-description", "name: no-description", "본문")
    (tmp_path / "no-header").mkdir()
    (tmp_path / "no-header" / "SKILL.md").write_text("머리말 없음", encoding="utf-8")

    loaded = skills.load_skills(tmp_path)
    assert [(skill.name, skill.description) for skill in loaded] == [("good", "쓸 때: 항상")]  # 값 안의 콜론은 그대로 둔다


def test_read_skill_returns_body_and_refuses_unknown_names(tmp_path):
    make_skill(tmp_path, "good", "name: good\ndescription: d", "# 절차\n1. 확인한다")
    tool = skills.read_skill_tool(skills.load_skills(tmp_path))

    assert order_tools.run_tool("read_skill", {"name": "good"}, [tool]) == ("# 절차\n1. 확인한다", False)
    for name in ["missing", "../good", str(tmp_path / "good" / "SKILL.md")]:
        output, is_error = order_tools.run_tool("read_skill", {"name": name}, [tool])
        assert is_error and "good" in output  # 쓸 수 있는 이름을 알려 준다


def test_claude_loop_serves_skill_body_as_tool_result():
    loaded = skills.load_skills()
    client = FakeClient([
        B(stop_reason="tool_use", content=[B(type="tool_use", id="t1", name="read_skill", input={"name": "order-cancel"})]),
        B(stop_reason="end_turn", content=[B(type="text", text="안내했습니다.")]),
    ])
    result = claude_agent.run_agent(
        "환불 언제 되나요?", client=client, on_tool=lambda *call: None,
        system=order_tools.SYSTEM + skills.skill_catalog(loaded),
        tools=[*order_tools.TOOLS, skills.read_skill_tool(loaded)])

    assert result.ok
    first, second = client.requests
    assert "- order-cancel: " in first["system"] and "영업일 기준 3일" not in first["system"]
    assert [tool["name"] for tool in first["tools"]] == ["get_order", "cancel_order", "read_skill"]
    tool_result = second["messages"][-1]["content"][0]
    assert tool_result["is_error"] is False and "영업일 기준 3일" in tool_result["content"]


def test_openai_loop_serves_skill_body_as_tool_message():
    loaded = skills.load_skills()
    client = FakeClient([
        B(choices=[B(finish_reason="tool_calls", message=B(content=None, tool_calls=[
            B(id="c1", function=B(name="read_skill", arguments='{"name": "delivery-delay"}'))]))]),
        B(choices=[B(finish_reason="stop", message=B(content="안내했습니다.", tool_calls=None))]),
    ])
    result = openai_compat_agent.run_agent(
        "언제 와요?", client=client, model="m", on_tool=lambda *call: None,
        system=order_tools.SYSTEM + skills.skill_catalog(loaded),
        tools=[*order_tools.TOOLS, skills.read_skill_tool(loaded)])

    assert result.ok
    first, second = client.requests
    assert "- delivery-delay: " in first["messages"][0]["content"]
    assert [tool["function"]["name"] for tool in first["tools"]][-1] == "read_skill"
    assert second["messages"][-1]["role"] == "tool" and "적립금 2,000원" in second["messages"][-1]["content"]
