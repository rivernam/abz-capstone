from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MATERIAL = ROOT / "재료"
DB_PATH = MATERIAL / "db" / "기준정보.sqlite"
SERVER_LOG_DIR = MATERIAL / "서버로그"
DOCS_DIR = MATERIAL / "문서"
PATTERN_TABLE = DOCS_DIR / "오류메시지패턴표.csv"
RECORD_DIR = ROOT / "기록"
STORAGE_DIR = RECORD_DIR / "storage"
OUTBOX = RECORD_DIR / "outbox.jsonl"
RUNS_DIR = RECORD_DIR / "runs"
SKILLS_DIR = ROOT / ".claude" / "skills"
