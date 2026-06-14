"""Read and filter the local daemon system-trace timeline."""

import json
import time
from collections import deque
from datetime import datetime, timezone

from src.config.paths import trace_dir


def register(subparsers, _config) -> None:
    parser = subparsers.add_parser("trace", help="inspect the local Core trace timeline")
    parser.add_argument("--run")
    parser.add_argument("--execution")
    parser.add_argument("--layer")
    parser.add_argument("--direction")
    parser.add_argument("--kind")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--follow", action="store_true")
    parser.add_argument("--raw", action="store_true")
    parser.set_defaults(handler=run)


def run(args, _config) -> int:
    """Print retained matching records, optionally following today's file."""
    if args.limit <= 0:
        raise ValueError("--limit must be greater than zero")
    root = trace_dir()
    matches = deque(maxlen=args.limit)
    for path in sorted(root.glob("*/daemon.jsonl")):
        for record in _read_records(path):
            if _matches(record, args):
                matches.append(record)
    for record in matches:
        _print_record(record, raw=args.raw)
    if args.follow:
        _follow(root, args)
    return 0


def _read_records(path):
    try:
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    yield value
    except FileNotFoundError:
        return


def _matches(record: dict, args) -> bool:
    return all(
        (
            not args.run or record.get("run_id") == args.run,
            not args.execution or record.get("execution_id") == args.execution,
            not args.layer or record.get("layer") == args.layer,
            not args.direction or record.get("direction") == args.direction,
            not args.kind or record.get("kind") == args.kind,
        )
    )


def _print_record(record: dict, *, raw: bool) -> None:
    if raw:
        print(json.dumps(record, ensure_ascii=False))
        return
    timestamp = str(record.get("timestamp", ""))[11:23]
    data = record.get("data") or {}
    summary = " ".join(f"{key}={value}" for key, value in list(data.items())[:6])
    print(
        f"{timestamp:<12} {str(record.get('direction', '')):<18} "
        f"{str(record.get('layer', '')):<10} {str(record.get('kind', '')):<30} {summary}"
    )


def _follow(root, args, *, path_provider=None, sleep=None) -> None:
    """Follow complete JSONL records and switch files at the UTC date boundary."""
    path_provider = path_provider or (lambda: _today_path(root))
    sleep = sleep or time.sleep
    path = path_provider()
    offset = path.stat().st_size if path.exists() else 0
    try:
        while True:
            next_path = path_provider()
            if next_path != path:
                path, offset = next_path, 0
            if path.exists():
                with path.open("r", encoding="utf-8") as stream:
                    stream.seek(offset)
                    while line := stream.readline():
                        line_start = offset
                        # A concurrently appended final line may be visible
                        # before its newline. Re-read it on the next poll.
                        if not line.endswith("\n"):
                            offset = line_start
                            break
                        offset = stream.tell()
                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(record, dict) and _matches(record, args):
                            _print_record(record, raw=args.raw)
            sleep(0.25)
    except KeyboardInterrupt:
        return


def _today_path(root):
    day = datetime.now(timezone.utc).date().isoformat()
    return root / day / "daemon.jsonl"
