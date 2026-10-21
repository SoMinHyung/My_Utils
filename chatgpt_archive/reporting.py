"""도구 출력은 유한 크기의 요약, 전체 진단은 기존 DB/last_run.json에 보존."""
from copy import deepcopy

LIST_LIMIT = 10


def compact_report(report):
    result = {}
    counts = {}
    for key, value in report.items():
        if isinstance(value, list):
            counts[key] = len(value)
            result[key] = [_item(item) for item in value[:LIST_LIMIT]]
        else:
            result[key] = value
    result["report_counts"] = counts
    result["report_compact"] = True
    result["details_command"] = "status"
    result["verification_queue_command"] = "verification-queue"
    return result


def _item(value):
    if not isinstance(value, dict):
        return value[:200] if isinstance(value, str) else value
    result = deepcopy(value)
    for key, item in list(result.items()):
        if isinstance(item, list):
            result[key + "_count"] = len(item)
            result[key] = [_item(v) for v in item[:3]]
        elif isinstance(item, str):
            result[key] = item[:200]
        elif isinstance(item, dict):
            result[key] = _item(item)
    return result
