"""NOL 좌석 페이지의 데이터 구조를 읽기 전용으로 조사한다.

응답 본문, 요청 헤더, 쿠키는 저장하지 않는다. fetch/XHR JSON은 URL의
query/fragment를 제거한 뒤 구조와 좌석 관련 key 경로만 요약한다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError


SEAT_KEY_PATTERN = re.compile(
    r"seat|block|grade|remain|available|availability|stock|inventory|"
    r"count|status|section|area|floor",
    re.IGNORECASE,
)
SAFE_ENUM_KEY_PATTERN = re.compile(
    r"status|state|available|availability|grade|block|section|area|floor",
    re.IGNORECASE,
)
LONG_PATH_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9_-]{24,}$")
JSON_RESPONSE_TYPES = {"xhr", "fetch"}
MAX_JSON_BYTES = 5_000_000
MAX_NETWORK_RECORDS = 100
MAX_CANDIDATE_PATHS = 40
MAX_DOM_CANDIDATES = 50

FRAME_SELECTORS = {
    "canvas": "canvas",
    "visible_canvas": "canvas:visible",
    "svg": "svg",
    "visible_svg": "svg:visible",
    "svg_circle": "svg circle",
    "svg_rect": "svg rect",
    "svg_path": "svg path",
    "svg_use": "svg use",
    "map_area": "map area",
    "seat_class": '[class*="seat" i]',
    "seat_id": '[id*="seat" i]',
    "data_seat_id": "[data-seat-id]",
    "data_seat": "[data-seat]",
    "seat_aria_label": '[aria-label*="좌석"]',
    "button": "button",
    "role_button": '[role="button"]',
}
SEAT_CANDIDATE_SELECTOR = ", ".join(
    [
        '[data-seat-id]',
        '[data-seat]',
        '[aria-label*="좌석"]',
        '[class*="seat" i]',
        '[id*="seat" i]',
    ]
)


def safe_url(url: str) -> str:
    """userinfo/query/fragment와 token처럼 긴 path segment를 제거한다."""
    parts = urlsplit(url)
    safe_segments = []
    for segment in parts.path.split("/"):
        if len(segment) > 48 or LONG_PATH_SEGMENT_PATTERN.fullmatch(segment):
            safe_segments.append("<redacted>")
        else:
            safe_segments.append(segment)
    safe_path = "/".join(safe_segments)
    hostname = parts.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    try:
        port = f":{parts.port}" if parts.port is not None else ""
    except ValueError:
        port = ""
    return urlunsplit((parts.scheme, f"{hostname}{port}", safe_path, "", ""))


def _safe_key(key) -> str:
    key_text = str(key)
    if (
        len(key_text) > 48
        or key_text.isdigit()
        or LONG_PATH_SEGMENT_PATTERN.fullmatch(key_text)
    ):
        return "<dynamic-key>"
    return key_text


def _value_summary(key: str, value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return f"bool={value}"
    if isinstance(value, (int, float)):
        return f"number={value}"
    if isinstance(value, str):
        normalized = " ".join(value.split())
        if (
            SAFE_ENUM_KEY_PATTERN.search(key)
            and len(normalized) <= 24
            and re.fullmatch(r"[A-Za-z0-9가-힣 ._-]+", normalized)
        ):
            return f"string={normalized!r}"
        return f"string(len={len(value)})"
    if isinstance(value, list):
        return f"array(len={len(value)})"
    if isinstance(value, dict):
        keys = [_safe_key(item) for item in list(value)[:12]]
        suffix = ", ..." if len(value) > len(keys) else ""
        return f"object(keys=[{', '.join(keys)}{suffix}])"
    return type(value).__name__


def json_shape(payload) -> str:
    """본문 값은 노출하지 않고 JSON의 최상위 구조만 설명한다."""
    if isinstance(payload, dict):
        keys = [_safe_key(item) for item in list(payload)[:20]]
        suffix = ", ..." if len(payload) > len(keys) else ""
        return f"object(keys=[{', '.join(keys)}{suffix}])"
    if isinstance(payload, list):
        if not payload:
            return "array(len=0)"
        return f"array(len={len(payload)}, item={json_shape(payload[0])})"
    return type(payload).__name__


def find_candidate_paths(payload, *, limit: int = MAX_CANDIDATE_PATHS) -> list[dict[str, str]]:
    """좌석/수량 관련 key의 경로와 값의 안전한 요약만 반환한다."""
    matches: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def walk(value, path: str, depth: int) -> None:
        if len(matches) >= limit or depth > 7:
            return

        if isinstance(value, dict):
            for key, child in list(value.items())[:80]:
                key_text = _safe_key(key)
                child_path = f"{path}.{key_text}"
                if SEAT_KEY_PATTERN.search(key_text):
                    summary = _value_summary(key_text, child)
                    marker = (child_path, summary)
                    if marker not in seen:
                        seen.add(marker)
                        matches.append({"path": child_path, "summary": summary})
                        if len(matches) >= limit:
                            return
                walk(child, child_path, depth + 1)
        elif isinstance(value, list):
            # 배열 원소는 보통 같은 schema이므로 앞의 두 개만 조사한다.
            for child in value[:2]:
                walk(child, f"{path}[]", depth + 1)

    walk(payload, "$", 0)
    return matches


class SeatResponseDiagnostics:
    """좌석 페이지가 받은 JSON 응답의 구조만 수집하는 listener."""

    def __init__(self, print_fn=print, max_records: int = MAX_NETWORK_RECORDS):
        self.print_fn = print_fn
        self.max_records = max_records
        self.records: list[dict] = []
        self._pending = []
        self._armed = False
        self._context = None
        self._listener = self.handle_response

    def install(self, context) -> "SeatResponseDiagnostics":
        self._context = context
        context.on("response", self._listener)
        return self

    def arm(self) -> None:
        self._armed = True

    def uninstall(self) -> None:
        self._armed = False
        if self._context is not None:
            try:
                self._context.remove_listener("response", self._listener)
            except Exception:
                pass
        self._context = None

    def handle_response(self, response) -> None:
        """이벤트 처리 중에는 body를 읽지 않고 참조만 제한적으로 보관한다."""
        if not self._armed or len(self._pending) >= self.max_records:
            return

        try:
            resource_type = response.request.resource_type
            if resource_type not in JSON_RESPONSE_TYPES:
                return
            if not 200 <= response.status < 300:
                return

            headers = response.headers
            content_type = headers.get("content-type", "").casefold()
            if "json" not in content_type:
                return

            content_length_text = headers.get("content-length", "")
            if content_length_text.isdigit() and int(content_length_text) > MAX_JSON_BYTES:
                self.print_fn(
                    f"[SEAT-NET] 큰 JSON 응답은 건너뜀: {safe_url(response.url)}"
                )
                return

            self._pending.append(response)
        except Exception:
            # 이벤트 콜백에서는 예외 원문이나 URL을 출력하지 않는다.
            return

    def drain(self) -> None:
        """보관한 응답 body를 좌석맵 로딩 후 안전한 구조로 요약한다."""
        self._armed = False
        pending = self._pending
        self._pending = []

        for response in pending:
            if len(self.records) >= self.max_records:
                return
            try:
                payload = response.json()
                resource_type = response.request.resource_type
                record = {
                    "url": safe_url(response.url),
                    "status": response.status,
                    "resource_type": resource_type,
                    "shape": json_shape(payload),
                    "candidate_paths": find_candidate_paths(payload),
                }
                self.records.append(record)

                candidates = record["candidate_paths"]
                candidate_text = ", ".join(
                    f"{item['path']}({item['summary']})" for item in candidates[:8]
                )
                if not candidate_text:
                    candidate_text = "없음"
                self.print_fn(
                    f"[SEAT-NET] {record['status']} {record['url']} "
                    f"shape={record['shape']} candidates={candidate_text}"
                )
            except Exception:
                # 응답이 만료/종료됐어도 진단 흐름은 계속한다.
                self.print_fn("[SEAT-NET] JSON 구조 확인 실패")


def wait_for_manual_image_completion(
    page,
    input_selector: str,
    *,
    timeout_ms: int = 300_000,
    print_fn=print,
) -> bool:
    """사람이 브라우저에서 image 입력을 마쳐 입력창이 사라질 때까지 기다린다."""
    image_input = page.locator(input_selector).first
    try:
        if image_input.count() == 0 or not image_input.is_visible():
            return True

        print_fn("브라우저에서 image 문자를 직접 입력하고 입력완료를 눌러주세요.")
        image_input.wait_for(state="hidden", timeout=timeout_ms)
        page.wait_for_timeout(1_000)
        print_fn("image 모달이 닫혔습니다. 좌석 구조 진단을 시작합니다.")
        return True
    except PlaywrightTimeoutError:
        print_fn("수동 image 입력 대기 시간이 초과되어 좌석 구조 진단을 건너뜁니다.")
        return False


def _inspect_frame(frame, index: int, *, max_candidates: int) -> dict:
    frame_report = {
        "index": index,
        "url": safe_url(frame.url),
        "counts": {},
        "seat_candidates": [],
        "svg_groups": [],
    }

    for name, selector in FRAME_SELECTORS.items():
        try:
            frame_report["counts"][name] = frame.locator(selector).count()
        except Exception:
            frame_report["counts"][name] = -1

    try:
        frame_report["seat_candidates"] = frame.locator(
            SEAT_CANDIDATE_SELECTOR
        ).evaluate_all(
            r"""
            (elements, limit) => elements.slice(0, limit).map((element) => {
                const style = window.getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                const attributeNames = Array.from(element.attributes)
                    .map((attribute) => attribute.name)
                    .filter((name) => (
                        name.startsWith("data-") &&
                        /seat|status|state|block|grade|area|section|available|remain|stock/i.test(name)
                    ));
                const stateData = {};
                for (const attribute of Array.from(element.attributes)) {
                    if (
                        /status|state|available|disabled|grade|block|section|area|floor/i.test(attribute.name) &&
                        attribute.value.length <= 40
                    ) {
                        stateData[attribute.name] = attribute.value;
                    }
                }
                const rawClass = element.getAttribute("class") || "";
                const ariaLabel = element.getAttribute("aria-label") || "";
                const classHints = rawClass.split(/\s+/)
                    .filter((token) => /seat|available|disabled|block|section|grade|area/i.test(token))
                    .slice(0, 12)
                    .map((token) => token.length <= 48 ? token : `<${token.length} chars>`);
                return {
                    tag: element.tagName.toLowerCase(),
                    idPresent: Boolean(element.getAttribute("id")),
                    classHints,
                    role: element.getAttribute("role"),
                    ariaLabelPresent: Boolean(ariaLabel),
                    disabled: Boolean(element.disabled),
                    ariaDisabled: element.getAttribute("aria-disabled"),
                    visible: (
                        rect.width > 0 && rect.height > 0 &&
                        style.display !== "none" && style.visibility !== "hidden"
                    ),
                    pointerEvents: style.pointerEvents,
                    cursor: style.cursor,
                    attributeNames,
                    stateData,
                };
            })
            """,
            max_candidates,
        )
    except Exception:
        pass

    try:
        frame_report["svg_groups"] = frame.locator(
            "svg circle, svg rect, svg path"
        ).evaluate_all(
            r"""
            (elements) => {
                const groups = new Map();
                for (const element of elements) {
                    const key = JSON.stringify({
                        tag: element.tagName.toLowerCase(),
                        classHints: (element.getAttribute("class") || "")
                            .split(/\s+/)
                            .filter((token) => /seat|available|disabled|block|section|grade|area/i.test(token))
                            .slice(0, 8),
                        fill: (element.getAttribute("fill") || "").slice(0, 40),
                        role: element.getAttribute("role"),
                    });
                    groups.set(key, (groups.get(key) || 0) + 1);
                }
                return Array.from(groups.entries())
                    .map(([key, count]) => ({...JSON.parse(key), count}))
                    .sort((a, b) => b.count - a.count)
                    .slice(0, 30);
            }
            """
        )
    except Exception:
        pass

    return frame_report


def inspect_seat_page(page, *, print_fn=print) -> dict:
    """모든 frame에서 좌석 DOM/SVG/canvas 후보를 읽기 전용으로 집계한다."""
    report = {
        "page_url": safe_url(page.url),
        "frames": [],
    }

    for index, frame in enumerate(page.frames):
        try:
            frame_report = _inspect_frame(
                frame,
                index,
                max_candidates=MAX_DOM_CANDIDATES,
            )
        except Exception as exc:
            print_fn(f"[SEAT-DOM] frame={index} 조사 실패: {type(exc).__name__}")
            continue

        report["frames"].append(frame_report)
        nonzero_counts = {
            key: value
            for key, value in frame_report["counts"].items()
            if value != 0
        }
        print_fn(
            f"[SEAT-DOM] frame={index} url={frame_report['url']} "
            f"counts={json.dumps(nonzero_counts, ensure_ascii=False)}"
        )
        for candidate in frame_report["seat_candidates"][:10]:
            print_fn(
                "[SEAT-CANDIDATE] "
                + json.dumps(candidate, ensure_ascii=False, sort_keys=True)
            )
        if frame_report["svg_groups"]:
            print_fn(
                "[SEAT-SVG] "
                + json.dumps(
                    frame_report["svg_groups"][:10],
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )

    return report


def save_seat_diagnostics(
    network_records: list[dict],
    dom_report: dict | None,
    output_path: str | Path = "images/seat_diagnostics.json",
) -> Path:
    """민감한 요청 정보가 제외된 진단 결과만 고정 파일에 덮어쓴다."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"network": network_records, "dom": dom_report},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path.resolve()
