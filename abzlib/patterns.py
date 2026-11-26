"""오류 메시지 패턴표."""
import csv


def load_patterns(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def match_pattern(message, patterns):
    """메시지가 message_pattern으로 시작하면(대소문자 구분) 그 패턴 행. 없으면 None."""
    for p in patterns:
        if message.startswith(p["message_pattern"]):
            return p
    return None
