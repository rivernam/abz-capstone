"""가상 PC 폴더(재료/pc/{PC ID}/) 읽기와 쓰기.

파일 이름은 코드에서 고정한다(client.log, client.properties, jvm.args, service.json, backup/).
PC ID는 정규화된 형식만 받으므로 다른 경로로 나갈 수 없다.
"""
import csv
import json
import re
import shutil
from datetime import datetime

from abzlib.pcid import normalize_pc_id

PROXY_RE = re.compile(r"-Dabz\.proxy\.host=(\S+)\s+-Dabz\.proxy\.port=(\d+)")
LOG_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (\w+) (.*)$")
TS = "%Y-%m-%d %H:%M:%S"


def pc_path(world, pc_id):
    pc = normalize_pc_id(pc_id)
    if pc is None or pc != pc_id:
        return None
    d = world.pc_dir / pc
    return d if d.is_dir() else None


def read_service(world, pc):
    return json.loads((world.pc_dir / pc / "service.json").read_text(encoding="utf-8"))


def write_service(world, pc, status, since):
    (world.pc_dir / pc / "service.json").write_text(
        json.dumps({"status": status, "since": since}, ensure_ascii=False) + "\n", encoding="utf-8")


def read_props(world, pc):
    out = {}
    for line in (world.pc_dir / pc / "client.properties").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def set_prop(world, pc, key, value):
    """키 한 줄만 바꾼다(없으면 끝에 더한다). 다른 줄과 주석은 그대로 둔다."""
    f = world.pc_dir / pc / "client.properties"
    lines = f.read_text(encoding="utf-8").splitlines()
    done = False
    for i, line in enumerate(lines):
        if "=" in line and not line.lstrip().startswith("#") and line.split("=", 1)[0].strip() == key:
            lines[i] = f"{key}={value}"
            done = True
    if not done:
        lines.append(f"{key}={value}")
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_jvm(world, pc):
    return (world.pc_dir / pc / "jvm.args").read_text(encoding="utf-8").strip()


def jvm_proxy(args):
    m = PROXY_RE.search(args or "")
    return f"{m.group(1)}:{m.group(2)}" if m else None


def with_proxy(args, value):
    """프록시 인자 두 개만 바꾼다. 나머지 인자(-Xmx 등)는 그대로 둔다."""
    host, port = value.rsplit(":", 1)
    toks = [t for t in args.split() if not t.startswith(("-Dabz.proxy.host=", "-Dabz.proxy.port="))]
    at = toks.index("-jar") if "-jar" in toks else len(toks)
    return " ".join(toks[:at] + [f"-Dabz.proxy.host={host}", f"-Dabz.proxy.port={port}"] + toks[at:])


def write_jvm(world, pc, args):
    (world.pc_dir / pc / "jvm.args").write_text(args + "\n", encoding="utf-8")


def read_log(world, pc):
    out = []
    for line in (world.pc_dir / pc / "client.log").read_text(encoding="utf-8").splitlines():
        m = LOG_RE.match(line)
        if m:
            out.append((datetime.strptime(m.group(1), TS), m.group(2), m.group(3)))
    return out


def append_log(world, pc, entries):
    with open(world.pc_dir / pc / "client.log", "a", encoding="utf-8") as f:
        for ts, level, msg in entries:
            f.write(f"{ts:{TS}} {level} {msg}\n")


def last_error(world, pc):
    """마지막 'client started' 뒤의 마지막 ERROR 메시지. 없으면 None."""
    log = read_log(world, pc)
    start = max((i for i, (_, _, m) in enumerate(log) if m.startswith("client started")), default=-1)
    errs = [m for _, lv, m in log[start + 1:] if lv == "ERROR"]
    return errs[-1] if errs else None


def backup(world, pc, fname, tag):
    src = world.pc_dir / pc / fname
    dst = world.pc_dir / pc / "backup" / f"{fname}.{tag}.bak"
    dst.parent.mkdir(exist_ok=True)
    shutil.copy2(src, dst)
    return dst


def restore(world, pc, fname, backup_name):
    shutil.copy2(world.pc_dir / pc / "backup" / backup_name, world.pc_dir / pc / fname)


def load_defaults(world):
    """{(버전, 키): (기본값, 허용 여부)}"""
    with open(world.defaults_table, encoding="utf-8") as f:
        return {(r["version"], r["key"]): (r["default"], r["allowed"]) for r in csv.DictReader(f)}


def pattern_key(defaults, version):
    return next((k for (v, k), (_, allowed) in defaults.items() if v == version and allowed == "computed"), None)


def verify_client_health(world, pc, since):
    """재시작 후 성공 조건(구현명세 §6): service.json이 running, since 이후 client.log에 'client started'가 있고 그 뒤 ERROR가 없음."""
    svc = read_service(world, pc)
    log = [x for x in read_log(world, pc) if x[0] >= since]
    starts = [i for i, (_, _, m) in enumerate(log) if m.startswith("client started")]
    if svc.get("status") != "running":
        return {"healthy": False, "reason": f"service.json 상태가 {svc.get('status')}"}
    if not starts:
        return {"healthy": False, "reason": "재시작 시각 이후 client started 줄이 없음"}
    errs = [m for _, lv, m in log[starts[-1] + 1:] if lv == "ERROR"]
    if errs:
        return {"healthy": False, "reason": f"재시작 후 오류: {errs[-1]}"}
    return {"healthy": True, "reason": "실행 중, 재시작 후 오류 없음"}
