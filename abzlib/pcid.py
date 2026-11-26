import re

_PAT = re.compile(r"PC[-_ ]?([A-Z])[-_ ]?(\d{4})")


def normalize_pc_id(raw):
    """PC ID 표기 혼용(pc-a0101, PCA0101 ...)을 PC-A0101 형식으로 통일한다. 형식이 아니면 None."""
    if not isinstance(raw, str):
        return None
    m = _PAT.fullmatch(raw.strip().upper())
    if not m:
        return None
    return f"PC-{m.group(1)}{m.group(2)}"
