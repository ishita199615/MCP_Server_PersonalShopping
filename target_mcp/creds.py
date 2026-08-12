"""Reads the Target sign-in credentials out of the environment / target.env.

The only place in this package that touches credentials. Values are read at
call time, handed straight to the login form, and never logged, cached,
written to disk, or included in a tool's return value -- MCP tool output goes
into a model's context, so anything returned from here would be a leak.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# target.env first, .env second. Both are gitignored.
ENV_FILES = (PROJECT_ROOT / "target.env", PROJECT_ROOT / ".env")

EMAIL_VAR = "TARGET_EMAIL"
PASSWORD_VAR = "TARGET_PASSWORD"

MISSING = (
    f"Set {EMAIL_VAR} and {PASSWORD_VAR} in {ENV_FILES[0]}.\n"
    f"See target.env.example. The file is gitignored -- keep it that way."
)


class MissingCredentials(RuntimeError):
    """No email/password available to sign in with."""


def _load_env_files() -> None:
    """Populate os.environ from the env files without clobbering a real var.

    Hand-rolled rather than pulling in python-dotenv: it is twenty lines, and a
    credentials path is a bad place to add a dependency for convenience.
    """
    for path in ENV_FILES:
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # Strip one layer of matching quotes; passwords often need them.
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if key and key not in os.environ:
                os.environ[key] = value


def credentials() -> tuple[str, str]:
    """Return (email, password). Raises MissingCredentials if either is unset."""
    _load_env_files()
    email = (os.environ.get(EMAIL_VAR) or "").strip()
    password = os.environ.get(PASSWORD_VAR) or ""
    if not email or not password:
        raise MissingCredentials(MISSING)
    return email, password


def have_credentials() -> bool:
    """True if a sign-in could be attempted. Safe to expose in tool output."""
    try:
        credentials()
        return True
    except MissingCredentials:
        return False
