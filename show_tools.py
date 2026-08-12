"""Ask the MCP server to introduce itself: name, version, and its tools.

    python show_tools.py

Speaks the actual MCP handshake over stdio -- initialize, then tools/list --
so the output is the server's own answer, not a list copied out of the source.
Useful as a demo shot, and as a first check when a client says "not connected".
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"


def send(proc, message: dict) -> None:
    proc.stdin.write(json.dumps(message) + "\n")
    proc.stdin.flush()


def read_reply(proc, want_id: int, timeout_lines: int = 40) -> dict | None:
    """Read newline-delimited JSON until the reply with `want_id` shows up."""
    for _ in range(timeout_lines):
        line = proc.stdout.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue  # servers sometimes log to stdout; skip noise
        if msg.get("id") == want_id:
            return msg
    return None


def main() -> int:
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    proc = subprocess.Popen(
        [str(PYTHON) if PYTHON.is_file() else sys.executable, "-m", "target_mcp.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=str(ROOT),
    )

    try:
        send(proc, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "show_tools", "version": "1.0"},
            },
        })
        init = read_reply(proc, 1)
        if init is None:
            print("No response to initialize -- the server did not start.")
            return 1

        info = (init.get("result") or {}).get("serverInfo") or {}
        print(f"server : {info.get('name', '?')}  v{info.get('version', '?')}")
        print(f"command: {PYTHON.name} -m target_mcp.server\n")

        send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})

        reply = read_reply(proc, 2)
        tools = ((reply or {}).get("result") or {}).get("tools") or []
        print(f"tools  : {len(tools)}\n")
        for tool in tools:
            first_line = (tool.get("description") or "").strip().split("\n")[0]
            params = list(((tool.get("inputSchema") or {}).get("properties") or {}))
            sig = ", ".join(params)
            print(f"  {tool['name']}({sig})")
            print(f"      {first_line}\n")
        return 0 if tools else 1
    finally:
        proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
