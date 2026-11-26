"""가상 재시작(구현명세 §4 가상 재시작). restart_client_service 내부에서만 쓴다.

숨은 원인 파일(기록/정답_PC원인.json)은 이 모듈만 읽는다. 에이전트에게 주는 Tool은 이 파일을 읽는 경로가 없다.
결과는 숨은 원인과 현재 설정으로 정하고, service.json과 client.log에 남긴다.
"""
import json
from datetime import timedelta

from abzlib import virtual_pc as vpc


def _hidden(world, pc):
    f = world.hidden_path
    if not f.exists():
        return {}
    return json.loads(f.read_text(encoding="utf-8")).get("pcs", {}).get(pc, {})


def _succeeds(world, pc, h, version):
    cond = h.get("restart_succeeds_if", "always")
    if cond == "always":
        return True
    if cond == "never":
        return False
    if cond == "config_key_restored":
        default = vpc.load_defaults(world).get((version, h["config_key"]), (None,))[0]
        return vpc.read_props(world, pc).get(h["config_key"]) == default
    if cond == "jvm_proxy_correct":
        return vpc.jvm_proxy(vpc.read_jvm(world, pc)) == h["expected_proxy"]
    if cond == "pattern_correct":
        return vpc.read_props(world, pc).get(h["config_key"]) == h["expected_pattern"]
    return False


def restart(world, pc, version, at):
    """가상으로 클라이언트를 재시작한다. at은 재시작 시각(초 단위)."""
    h = _hidden(world, pc)
    vpc.append_log(world, pc, [(at, "INFO", f"client started version={version}")])
    if _succeeds(world, pc, h, version):
        vpc.write_service(world, pc, "running", f"{at:%Y-%m-%d %H:%M:%S}")
        return
    end = at + timedelta(seconds=31)
    vpc.append_log(world, pc, [(at + timedelta(seconds=30), "ERROR", h.get("error_message") or "client exited unexpectedly"),
                               (end, "INFO", "client stopped (fatal)")])
    vpc.write_service(world, pc, "stopped", f"{end:%Y-%m-%d %H:%M:%S}")
