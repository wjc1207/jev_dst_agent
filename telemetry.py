"""Reassemble complete DST telemetry records from bounded client-log lines."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional, Tuple


STATE_PREFIX = "[JEV_DST_STATE]"
CHUNK_PREFIX = "[JEV_DST_CHUNK]"
MAX_TAIL_BYTES = 2_000_000


class StateAssembler:
    def __init__(self) -> None:
        self.pending: dict[str, dict] = {}

    def feed(self, line: str) -> Optional[dict]:
        marker = line.find(STATE_PREFIX)
        if marker >= 0:
            try:
                value = json.loads(line[marker + len(STATE_PREFIX):].strip())
            except json.JSONDecodeError:
                return None
            return value if isinstance(value, dict) else None

        marker = line.find(CHUNK_PREFIX)
        if marker < 0:
            return None
        # DST appends a tab to each printed log line, including every chunk.
        fields = line[marker + len(CHUNK_PREFIX):].rstrip("\t\r\n").split(":", 3)
        if len(fields) != 4:
            return None
        frame_id, index_text, total_text, fragment = fields
        try:
            index = int(index_text)
            total = int(total_text)
        except ValueError:
            return None
        if not frame_id or not 1 <= index <= total <= 1000:
            return None
        if index == 1:
            if len(self.pending) >= 32:
                self.pending.pop(next(iter(self.pending)))
            self.pending[frame_id] = {"total": total, "parts": {}}
        frame = self.pending.get(frame_id)
        if frame is None or frame["total"] != total:
            return None
        frame["parts"][index] = fragment
        if len(frame["parts"]) != total:
            return None
        del self.pending[frame_id]
        try:
            value = json.loads("".join(frame["parts"][part] for part in range(1, total + 1)))
        except (KeyError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None


def latest_state(log_path: Path) -> Tuple[dict, int]:
    """Return the latest complete JSON record and its final byte offset."""
    if not log_path.exists():
        raise RuntimeError(f"DST log does not exist: {log_path}")
    with log_path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        end = stream.tell()
        start = max(0, end - MAX_TAIL_BYTES)
        stream.seek(start)
        data = stream.read()

    offset = start
    if start:
        first_newline = data.find(b"\n")
        if first_newline < 0:
            raise RuntimeError("No complete JEV telemetry record found in the log tail")
        offset += first_newline + 1
        data = data[first_newline + 1:]

    assembler = StateAssembler()
    latest: Optional[dict] = None
    latest_offset = 0
    for raw_line in data.splitlines(keepends=True):
        offset += len(raw_line)
        if not raw_line.endswith(b"\n"):
            continue
        state = assembler.feed(raw_line.decode("utf-8", errors="replace"))
        if state is not None:
            latest = state
            latest_offset = offset
    if latest is None:
        raise RuntimeError("No complete JEV telemetry record found; install the updated mod")
    return latest, latest_offset
