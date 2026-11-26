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
# 아이템 ② 문의 대응
PC_DIR = MATERIAL / "pc"
DEFAULTS_TABLE = DOCS_DIR / "설정기본값표.csv"
BOARD_DB = RECORD_DIR / "게시판.sqlite"
BOARD_HTML_DIR = STORAGE_DIR / "board"
ACTION_HISTORY = RECORD_DIR / "조치이력.sqlite"
HIDDEN_CAUSES = RECORD_DIR / "정답_PC원인.json"          # 가상 재시작과 채점 전용. 에이전트 Tool로 읽지 않는다.
INQUIRY_SET = RECORD_DIR / "문의시험세트.json"
INQUIRY_ANSWER = RECORD_DIR / "정답표_문의.json"
EVAL_DIR = RECORD_DIR / "평가"
