"""문의의 설비 PC ID 필드를 조치 대상 목록으로 푼다(구현명세 §6 대상 해석).

지원 표기: PC-A0101 단독, PC-A0101~0110, PC-A0101~PC-A0110, 쉼표 목록(섞어 쓸 수 있음).
본문은 대상이 아니다. 본문에 나온 PC는 body_only로만 알려 준다.
"""
import re

from abzlib.pcid import normalize_pc_id

MAX_TARGETS = 20          # 한 문의에서 자동 조치하는 대수 상한
MAX_RANGE = 100           # 범위 하나로 펼칠 수 있는 최대 대수(잘못된 범위가 수천 대로 펼쳐지는 것을 막음)
_ID_IN_TEXT = re.compile(r"PC[-_ ]?[A-Za-z][-_ ]?\d{4}", re.I)


def _expand(token):
    if "~" not in token:
        n = normalize_pc_id(token)
        return [n] if n else None
    a, b = (x.strip() for x in token.split("~", 1))
    start = normalize_pc_id(a)
    if start is None:
        return None
    end = start[:4] + b if re.fullmatch(r"\d{4}", b) else normalize_pc_id(b)
    if end is None or end[:4] != start[:4]:
        return None
    lo, hi = int(start[4:]), int(end[4:])
    if hi < lo or hi - lo + 1 > MAX_RANGE:
        return None
    return [f"{start[:4]}{n:04d}" for n in range(lo, hi + 1)]


def parse_pc_field(field, body=""):
    """{targets: 앞에서부터 최대 20대, over_limit: 상한을 넘은 PC, invalid: 해석하지 못한 표기, body_only: 본문에만 나온 PC}."""
    ids, invalid = [], []
    for tok in (t.strip() for t in re.split(r"[,，\n]", field or "")):
        if not tok:
            continue
        got = _expand(tok)
        if got is None:
            invalid.append(tok)
        else:
            ids += got
    seen = set()
    uniq = [x for x in ids if not (x in seen or seen.add(x))]
    body_ids = []
    for m in _ID_IN_TEXT.findall(body or ""):
        n = normalize_pc_id(m)
        if n and n not in seen and n not in body_ids:
            body_ids.append(n)
    return {"targets": uniq[:MAX_TARGETS], "over_limit": uniq[MAX_TARGETS:], "invalid": invalid, "body_only": body_ids}
