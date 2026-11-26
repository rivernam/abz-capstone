"""가상 세계 위치 묶음.

쓰기 Tool과 시험은 루트 경로를 인자로 받는다. 시험은 tmp_path에 generate()로 만든 세계를 쓰고,
실제 실행은 저장소 루트(paths.ROOT)를 쓴다. 하위 경로 구조는 abzlib/paths.py와 같다.
"""
from datetime import datetime
from pathlib import Path

from abzlib import paths


class World:
    def __init__(self, root=None, clock=None):
        self.root = Path(root) if root else paths.ROOT
        self._clock = clock

    def _p(self, p):
        return self.root / Path(p).relative_to(paths.ROOT)

    def now(self):
        """가상 재시작 시각. 시험에서는 clock으로 고정한다."""
        return (self._clock() if self._clock else datetime.now()).replace(microsecond=0)

    db_path = property(lambda s: s._p(paths.DB_PATH))
    log_dir = property(lambda s: s._p(paths.SERVER_LOG_DIR))
    pattern_table = property(lambda s: s._p(paths.PATTERN_TABLE))
    defaults_table = property(lambda s: s._p(paths.DEFAULTS_TABLE))
    pc_dir = property(lambda s: s._p(paths.PC_DIR))
    board_db = property(lambda s: s._p(paths.BOARD_DB))
    board_html_dir = property(lambda s: s._p(paths.BOARD_HTML_DIR))
    history_db = property(lambda s: s._p(paths.ACTION_HISTORY))
    hidden_path = property(lambda s: s._p(paths.HIDDEN_CAUSES))
    runs_dir = property(lambda s: s._p(paths.RUNS_DIR))
    source_dir = property(lambda s: s._p(paths.SOURCE_DIR))
