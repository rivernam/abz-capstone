from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MATERIAL = ROOT / "재료"
DB_PATH = MATERIAL / "db" / "기준정보.sqlite"
SERVER_LOG_DIR = MATERIAL / "서버로그"
DOCS_DIR = MATERIAL / "문서"
RECORD_DIR = ROOT / "기록"
