"""publish_report: 일일 리포트 HTML을 저장소에 올리고 발송 레코드를 outbox에 남긴다 (쓰기).

무엇을 하는지: 리포트를 기록/storage/reports/YYYY-MM-DD.html에 저장하고 기록/outbox.jsonl에 한 줄 추가한다.
언제 쓰는지: 리포트를 다 만든 뒤 마지막에 한 번. 같은 날짜로 다시 부르면 아무것도 하지 않고 처음 결과를 돌려준다(멱등 키
daily-report:{날짜}). 고친 리포트를 다시 보내려면 force=True로 부르고, 이때도 outbox에는 새 레코드가 따로 남는다.
한계: 실제 메일이나 메신저로 보내지 않는다. 수신자 그룹은 코드에서 고정한다.
"""
import json
import re
from datetime import datetime
from pathlib import Path

from abzlib import paths

RECIPIENTS = "client-ops"


def publish_report(report_date, html, storage_dir=None, outbox=None, force=False):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", report_date or ""):
        return {"is_error": True, "error": f"날짜 형식이 아니다: {report_date!r}", "next": "YYYY-MM-DD로 다시 부른다."}
    if not html or "<html" not in html:
        return {"is_error": True, "error": "HTML 본문이 비어 있거나 HTML이 아니다.", "next": "리포트를 다시 만든다."}
    storage_dir = Path(storage_dir or paths.STORAGE_DIR)
    outbox = Path(outbox or paths.OUTBOX)
    key = f"daily-report:{report_date}" + (f":force:{datetime.now():%Y%m%d%H%M%S}" if force else "")
    if not force and outbox.exists():
        for line in outbox.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            if rec["idempotency_key"] == key:
                return {"published": False, "replayed": True, **rec}
    path = storage_dir / "reports" / f"{report_date}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    rec = {"idempotency_key": key, "recipients": RECIPIENTS, "subject": f"[ABZ] 클라이언트 일일 리포트 {report_date}",
           "report_path": str(path.relative_to(storage_dir.parent) if storage_dir.parent in path.parents else path),
           "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    outbox.parent.mkdir(parents=True, exist_ok=True)
    with open(outbox, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {"published": True, "replayed": False, **rec}
