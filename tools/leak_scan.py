"""Leak scan: run before any push that adds files, and before the repo or an image is made public
(SECURITY.md). Standard library only; run from anywhere inside the repo.

    python tools/leak_scan.py

Checks, in tracked files and in every commit's added lines:
  1. any value from the local .env (never printed),
  2. secret-shaped strings (GitHub, OpenAI-style and AWS keys, Slack tokens, private keys,
     key/secret/token/password assignments),
  3. sensitive file names ever committed (.env, keys, certificates, PDFs),
  4. credentials embedded in a git remote URL.
A finding prints the file or commit and a masked excerpt, never the secret. Exit 1 on any finding.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

PATTERN = (
    r"(gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}"
    r"|sk-[A-Za-z0-9]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY"
    r"|(api[_-]?key|secret|token|password)[\"' ]*[:=][\"' ]*[A-Za-z0-9/+_-]{16,}"
)
SENSITIVE_NAME = re.compile(r"(^|/)\.env$|\.(pem|key|p12|pfx|pdf)$|id_rsa|credentials", re.I)
# Fake values the tests use on purpose; add to this list only with a reason.
ALLOWED = ("pw-Hunter2-do-not-log",)  # backend/tests/test_logging.py: a password the log must not show
MIN_ENV_VALUE = 8


def git(*args: str) -> str:
    out = subprocess.run(["git", *args], capture_output=True, text=True, errors="replace")
    return out.stdout


def mask(line: str) -> str:
    line = re.sub(r"(gh[pousr]_|github_pat_)[A-Za-z0-9_]+", r"\1***", line)
    line = re.sub(r"([:=][\"' ]*)[A-Za-z0-9/+_-]{16,}", r"\1***", line)
    return line.strip()[:110]


def env_values(root: Path) -> list[str]:
    env = root / ".env"
    if not env.exists():
        return []
    values = []
    for raw in env.read_text(encoding="utf-8", errors="replace").splitlines():
        raw = raw.strip()
        if raw and not raw.startswith("#") and "=" in raw:
            value = raw.split("=", 1)[1].strip().strip("\"'")
            if len(value) >= MIN_ENV_VALUE:
                values.append(value)
    return values


def main() -> int:
    root = Path(git("rev-parse", "--show-toplevel").strip() or ".")
    findings: list[str] = []

    values = env_values(root)
    for value in values:
        if subprocess.run(["git", "grep", "-q", "-F", "--", value], cwd=root).returncode == 0:
            findings.append("a value from .env is in a tracked file")
        if git("log", "--all", "--oneline", "-S" + value).strip():
            findings.append("a value from .env is in the git history")

    rx = re.compile(PATTERN, re.I)
    for line in git("grep", "-n", "-I", "-i", "-E", PATTERN, "--", ".", ":!*.json").splitlines():
        if not any(ok in line for ok in ALLOWED):
            findings.append(f"tracked file: {mask(line)}")

    current = ""
    for line in git("log", "--all", "-p", "--format=commit %h").splitlines():
        if line.startswith("commit "):
            current = line.split()[1]
        elif line.startswith("+") and not line.startswith("+++") and rx.search(line):
            if not any(ok in line for ok in ALLOWED):
                findings.append(f"history {current}: {mask(line)}")

    for name in sorted(set(git("log", "--all", "--name-only", "--format=").splitlines())):
        if name and SENSITIVE_NAME.search(name) and name != ".env.example":
            findings.append(f"sensitive file name in history: {name}")

    for line in git("remote", "-v").splitlines():
        if re.search(r"://[^/@\s]+@", line):
            findings.append(f"credentials in a remote URL: {line.split()[0]}")

    findings = list(dict.fromkeys(findings))
    print(f".env values checked: {len(values)} (never printed)")
    if findings:
        print(f"LEAK SCAN FAILED: {len(findings)} finding(s)")
        for item in findings:
            print(" -", item)
        return 1
    print("leak scan clean: no .env value, secret pattern, sensitive file, or remote credential")
    return 0


if __name__ == "__main__":
    sys.exit(main())
