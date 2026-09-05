"""Check tracked release content without printing potential secret values.

This is a hygiene check, not a replacement for a dedicated secret scanner.
"""

from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PARTS = {"paper", "results", "models", "vendor", "third_party", "node_modules", ".venv"}
FORBIDDEN_SUFFIXES = {".tex", ".bib", ".pdf", ".pt", ".pth", ".safetensors", ".pem", ".key", ".sqlite", ".db"}
SECRET_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{30,}"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"/(?:home|Users)/[A-Za-z0-9_.-]+/"),
    re.compile(r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
)


def main() -> int:
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    paths = [Path(p) for p in tracked if p]
    if not paths:
        print("release check: no tracked files", file=sys.stderr)
        return 1
    findings = []
    for relative in paths:
        path = ROOT / relative
        if path.is_symlink() or set(relative.parts) & FORBIDDEN_PARTS or relative.suffix in FORBIDDEN_SUFFIXES:
            findings.append((str(relative), "excluded artifact or symlink"))
            continue
        if relative.name.startswith(".env"):
            findings.append((str(relative), "environment file"))
        if path.stat().st_size > 1_000_000:
            findings.append((str(relative), "oversized release file"))
        if relative.suffix == ".png":
            continue
        content = path.read_text(encoding="utf-8")
        if any(pattern.search(content) for pattern in SECRET_PATTERNS):
            findings.append((str(relative), "possible secret or private machine reference"))
    for path, reason in findings:
        print(f"{path}: {reason}", file=sys.stderr)
    if findings:
        return 1
    print(f"release check: {len(paths)} tracked files; no excluded artifacts or matching secrets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
