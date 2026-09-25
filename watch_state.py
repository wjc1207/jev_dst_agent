"""Watch DST's client log and print JEV telemetry records as readable JSON."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Iterator, Optional

import telemetry


PREFIX = "[JEV_DST_STATE]"


def default_log_path() -> Path:
    return Path.home() / "Documents" / "Klei" / "DoNotStarveTogether" / "client_log.txt"


def parse_state_line(line: str) -> Optional[dict]:
    return telemetry.StateAssembler().feed(line)


def follow(path: Path, from_start: bool = False) -> Iterator[str]:
    while not path.exists():
        print(f"Waiting for log file: {path}", file=sys.stderr)
        time.sleep(1)

    with path.open("r", encoding="utf-8", errors="replace") as stream:
        if not from_start:
            stream.seek(0, os.SEEK_END)
        last_position = stream.tell()
        pending = ""

        while True:
            line = stream.readline()
            if line:
                last_position = stream.tell()
                pending += line
                if pending.endswith("\n"):
                    yield pending
                    pending = ""
                continue

            try:
                size = path.stat().st_size
            except FileNotFoundError:
                size = 0
            if size < last_position:
                stream.seek(0)
                last_position = 0
                pending = ""
            time.sleep(0.2)


def compact_summary(state: dict) -> str:
    world = state.get("world", {})
    player = state.get("player", {})
    vitals = player.get("vitals", {})
    inventory = player.get("inventory", {}).get("items", [])
    counts = {}
    for item in inventory:
        prefab = item.get("prefab", "unknown")
        counts[prefab] = counts.get(prefab, 0) + int(item.get("count", 1))
    inventory_text = ", ".join(f"{name}={count}" for name, count in sorted(counts.items())) or "empty"
    return (
        f"day={world.get('day')} phase={world.get('phase')} "
        f"hp={vitals.get('health')} hunger={vitals.get('hunger')} "
        f"sanity={vitals.get('sanity')} inventory=[{inventory_text}] "
        f"nearby={len(state.get('nearby', []))}"
    )


def run_self_test() -> int:
    sample = 'prefix [JEV_DST_STATE]{"schema":1,"world":{"day":1,"phase":"day"}}\n'
    parsed = parse_state_line(sample)
    assert parsed is not None
    assert parsed["schema"] == 1
    assert parsed["world"]["day"] == 1
    assert parse_state_line("ordinary game log\n") is None
    print("self-test passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, default=default_log_path(), help="Path to client_log.txt")
    parser.add_argument("--from-start", action="store_true", help="Read existing records before following")
    parser.add_argument("--full", action="store_true", help="Print full formatted JSON instead of a summary")
    parser.add_argument("--once", action="store_true", help="Exit after the first telemetry record")
    parser.add_argument("--self-test", action="store_true", help="Run parser checks and exit")
    args = parser.parse_args()

    if args.self_test:
        return run_self_test()

    print(f"Watching {args.log}", file=sys.stderr)
    assembler = telemetry.StateAssembler()
    for line in follow(args.log, args.from_start):
        state = assembler.feed(line)
        if state is None:
            continue
        if args.full:
            print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(compact_summary(state))
        if args.once:
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
