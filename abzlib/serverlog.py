"""서버 실행 로그 읽기와 이상 PC 판정(구현명세 §4, §5)."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

GAP = timedelta(minutes=30)
RESTART_LOOP_STARTS = 5


@dataclass(frozen=True)
class LogLine:
    ts: datetime
    pc_id: str | None          # 식별 실패 줄은 None
    event: str
    version: str
    conn: str                  # 접속 주소
    internal: str | None       # 설비망 IP, 없으면 None
    message: str = ""

    @property
    def day(self):
        return self.ts.strftime("%Y-%m-%d")


def parse_line(text):
    parts = text.rstrip("\n").split(" | ")
    if len(parts) not in (6, 7):
        raise ValueError(f"로그 형식이 아니다: {text!r}")
    return LogLine(
        ts=datetime.strptime(parts[0], "%Y-%m-%d %H:%M:%S"),
        pc_id=None if parts[1] == "-" else parts[1],
        event=parts[2], version=parts[3], conn=parts[4],
        internal=None if parts[5] == "-" else parts[5],
        message=parts[6] if len(parts) == 7 else "",
    )


def shift(day, n):
    return (date.fromisoformat(day) + timedelta(days=n)).isoformat()


def read_logs(log_dir, days):
    """여러 날짜의 로그를 시간순으로 읽는다. 없는 날짜는 건너뛰고 읽은 날짜 목록을 함께 돌려준다."""
    lines, found = [], []
    for d in sorted(set(days)):
        f = Path(log_dir) / f"server_{d}.txt"
        if not f.exists():
            continue
        found.append(d)
        with open(f, encoding="utf-8") as fh:
            lines += [parse_line(l) for l in fh if l.strip()]
    lines.sort(key=lambda l: l.ts)
    return lines, found


def anomalies(master, lines, day):
    """리포트 날짜의 이상 PC를 판정한다. {pc_id: {유형: 근거}}.

    - heartbeat_gap: 정상 신호 간격 30분 이상. 앞쪽 신호의 날짜로 귀속.
    - restart_loop: 그날 START 5회 이상.
    - error: 그날 ERROR 1건 이상.
    - no_report: 기준정보에 있는데 그날 식별된 로그가 한 줄도 없음.
    """
    hb, starts, errors, seen = {}, {}, {}, set()
    for l in lines:
        if l.pc_id not in master:
            continue
        if l.day == day:
            seen.add(l.pc_id)
            if l.event == "START":
                starts.setdefault(l.pc_id, []).append(l.ts)
            elif l.event == "ERROR":
                errors.setdefault(l.pc_id, []).append(l)
        if l.event == "HEARTBEAT":
            hb.setdefault(l.pc_id, []).append(l.ts)
    out = {}
    for pc in master:
        found = {}
        ts = hb.get(pc, [])
        gaps = [(a, b) for a, b in zip(ts, ts[1:]) if b - a >= GAP and a.strftime("%Y-%m-%d") == day]
        if gaps:
            found["heartbeat_gap"] = gaps
        if len(starts.get(pc, [])) >= RESTART_LOOP_STARTS:
            found["restart_loop"] = starts[pc]
        if pc in errors:
            found["error"] = errors[pc]
        if pc not in seen:
            found["no_report"] = True
        if found:
            out[pc] = found
    return out
