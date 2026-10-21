"""긴 UI 원문을 요약하지 않고 옮기기 위한 무손실·해시 검증 봉투."""
import base64
import hashlib
import io
import gzip
import json

from .archive import ArchiveError

MAX_CAPTURE_BYTES = 16 * 1024 * 1024


def read_capture(stream):
    value = json.load(stream)
    if not isinstance(value, dict):
        raise ArchiveError("화면 수집 입력은 JSON 객체여야 합니다.")
    if value.get("encoding") != "gzip-base64":
        return value
    try:
        compressed = base64.b64decode(value["data"], validate=True)
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as source:
            raw = source.read(MAX_CAPTURE_BYTES + 1)
        if (len(raw) > MAX_CAPTURE_BYTES or len(raw) != value["bytes"] or
                hashlib.sha256(raw).hexdigest() != value["sha256"]):
            raise ValueError("length/hash mismatch")
        result = json.loads(raw)
        if not isinstance(result, dict) or result.get("encoding"):
            raise ValueError("invalid capture")
        return result
    except (KeyError, TypeError, ValueError, OSError, EOFError) as exc:
        raise ArchiveError("화면 수집 전달 데이터가 잘렸거나 해시/크기가 다릅니다.") from exc
