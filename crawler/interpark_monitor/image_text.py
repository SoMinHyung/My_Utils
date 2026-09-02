"""저장된 image에서 Codex CLI로 문자열만 추출한다."""

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


CODEX_APP_BIN = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
IMAGE_TEXT_PATTERN = re.compile(r"[A-Za-z0-9?]+")
IMAGE_TEXT_PROMPT = (
    "첨부된 image를 직접 보고 보이는 문자열을 정확히 판독하세요. "
    "파일 수정, 명령 실행, 브라우저 조작은 하지 마세요. "
    "text에는 설명, 따옴표, 공백 없이 판독한 문자열만 넣으세요. "
    "확신할 수 없는 문자는 ?로 표시하세요."
)
IMAGE_TEXT_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {
            "type": "string",
            "description": "image에서 판독한 문자열",
        }
    },
    "required": ["text"],
    "additionalProperties": False,
}


def _find_codex_binary() -> str | None:
    configured = os.environ.get("CODEX_BIN")
    if configured:
        configured_path = Path(configured).expanduser()
        if configured_path.is_file() and os.access(configured_path, os.X_OK):
            return str(configured_path.resolve())

        resolved = shutil.which(configured)
        if resolved:
            return resolved

    resolved = shutil.which("codex")
    if resolved:
        return resolved

    if CODEX_APP_BIN.is_file() and os.access(CODEX_APP_BIN, os.X_OK):
        return str(CODEX_APP_BIN)

    return None


def _codex_environment() -> dict[str, str]:
    """인증·네트워크에 필요한 값만 전달하고 API key는 전달하지 않는다."""
    allowed_names = {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "CODEX_HOME",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "CODEX_CA_CERTIFICATE",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
    }
    child_env = {
        name: value
        for name, value in os.environ.items()
        if name in allowed_names
    }
    child_env["NO_COLOR"] = "1"
    return child_env


def _uses_chatgpt_login(codex_bin: str, child_env: dict[str, str]) -> bool:
    try:
        status = subprocess.run(
            [codex_bin, "login", "status"],
            text=True,
            capture_output=True,
            timeout=10,
            env=child_env,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"Codex 로그인 상태 확인 실패: {type(exc).__name__}")
        return False

    status_text = f"{status.stdout}\n{status.stderr}".casefold()
    return status.returncode == 0 and "logged in using chatgpt" in status_text


def extract_image_text_with_codex(
    image_path: Path,
    *,
    timeout_seconds: int = 120,
) -> str | None:
    """ChatGPT로 로그인된 Codex CLI를 사용해 image 문자열만 반환한다."""
    image_path = image_path.expanduser().resolve()
    if not image_path.is_file() or image_path.suffix.casefold() != ".png":
        print("유효한 PNG image 파일이 없어 텍스트 추출을 건너뜁니다.")
        return None
    if image_path.stat().st_size == 0:
        print("image 파일이 비어 있어 텍스트 추출을 건너뜁니다.")
        return None

    codex_bin = _find_codex_binary()
    if not codex_bin:
        print("Codex CLI를 찾지 못해 image 텍스트를 추출하지 않습니다.")
        return None

    child_env = _codex_environment()
    if not _uses_chatgpt_login(codex_bin, child_env):
        print("ChatGPT 로그인 상태가 아니므로 image 텍스트를 추출하지 않습니다.")
        return None


    try:
        with tempfile.TemporaryDirectory(prefix="interpark_image_text_") as temp_dir:
            temp_path = Path(temp_dir).resolve()
            schema_path = temp_path / "response_schema.json"
            output_path = temp_path / "response.json"
            schema_path.write_text(
                json.dumps(IMAGE_TEXT_SCHEMA, ensure_ascii=False),
                encoding="utf-8",
            )

            command = [
                codex_bin,
                "exec",
                "--ephemeral",
                "--ignore-user-config",
                "--sandbox",
                "read-only",
                "--skip-git-repo-check",
                "--color",
                "never",
                "-C",
                str(temp_path),
                "--image",
                str(image_path),
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
                "-",
            ]
            completed = subprocess.run(
                command,
                input=IMAGE_TEXT_PROMPT,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                cwd=temp_path,
                env=child_env,
                check=False,
            )

            if completed.returncode != 0 or not output_path.is_file():
                print(
                    "Codex image 텍스트 추출 실패: "
                    f"returncode={completed.returncode}"
                )
                return None

            payload = json.loads(output_path.read_text(encoding="utf-8"))

    except subprocess.TimeoutExpired:
        print(f"Codex image 텍스트 추출 시간 초과({timeout_seconds}초)")
        return None
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Codex image 텍스트 처리 실패: {type(exc).__name__}")
        return None

    if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
        print("Codex가 유효한 image 텍스트를 반환하지 않았습니다.")
        return None

    image_text = "".join(payload["text"].split())
    if not image_text or len(image_text) > 32 or not IMAGE_TEXT_PATTERN.fullmatch(image_text):
        print("Codex가 유효한 image 문자열을 반환하지 않았습니다.")
        return None

    return image_text
