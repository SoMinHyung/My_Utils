"""python3 -m chatgpt_archive [--output DIRECTORY] {state,ingest,export,status}"""
import argparse
import json
import sqlite3
import sys

from .archive import Archive, ArchiveError, DEFAULT_OUTPUT, archive_lock, read_archive


def main():
    parser = argparse.ArgumentParser(description="Codex에서 조회한 ChatGPT 대화를 Markdown/TXT로 보관")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="백업 폴더")
    parser.add_argument("command", choices=("state", "ingest", "export", "status", "repair-references",
                                           "repair-history", "repair-ui", "verification-queue"))
    parser.add_argument("--thread-id", action="append", default=[], help="복원할 대화 ID (반복 가능)")
    parser.add_argument("--apply", action="store_true", help="이전 저장본 복원 결과를 실제 반영")
    parser.add_argument("--compact", action="store_true", help="전체 결과는 보존하고 출력 목록만 제한")
    args = parser.parse_args()
    try:
        if args.thread_id and args.command != "repair-history":
            raise ArchiveError("--thread-id는 repair-history 전용입니다.")
        if args.apply and args.command not in ("repair-history", "repair-ui"):
            raise ArchiveError("--apply는 repair-history/repair-ui 전용입니다.")
        from .capture_io import read_capture
        ui_capture = read_capture(sys.stdin) if args.command == "repair-ui" else None
        if args.command == "repair-ui" and not args.apply:
            from .ui_repair import preview_ui_repair
            result = preview_ui_repair(args.output, ui_capture)
        elif args.command == "repair-history" and not args.apply:
            from .history_repair import preview_repairs
            result = preview_repairs(args.output, args.thread_id)
        elif args.command in ("state", "status", "verification-queue"):
            result = read_archive(args.output, args.command)
        else:
            with archive_lock(args.output), Archive(args.output) as archive:
                if args.command == "ingest":
                    result = archive.ingest(json.load(sys.stdin))
                elif args.command == "repair-references":
                    result = archive.repair_references()
                elif args.command == "repair-history":
                    result = archive.repair_history(args.thread_id)
                elif args.command == "repair-ui":
                    from .ui_repair import apply_ui_repair
                    result = apply_ui_repair(archive, ui_capture)
                else:
                    archive.export_all()
                    result = {"output": str(archive.output), "exported": len(archive.state()["conversations"])}
        if args.compact:
            from .reporting import compact_report
            result = compact_report(result)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ArchiveError, OSError, ValueError, sqlite3.Error) as exc:
        # 대화 본문의 단어로 재시도를 판단하지 않고 실제 권한 오류만 구분한다.
        permission_denied = isinstance(exc, PermissionError) or (
            isinstance(exc, sqlite3.Error) and (getattr(exc, "sqlite_errorcode", 0) & 255) == 8)
        print(json.dumps({"error": str(exc), "error_code": "permission_denied" if permission_denied else "archive_error"},
                         ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
