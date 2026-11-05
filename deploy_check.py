"""Pre-deploy check — run before pushing to GitHub.

    python deploy_check.py

Verifies the three things that actually break a Render deploy: a required file
missing from the repo, a credential committed, or the blueprint still describing
the old two-service layout.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

REQUIRED = [
    "requirements.txt", "render.yaml", "seed_training_data.csv",
    "categories.csv", "ml/train.py", "app/main.py", "app/db.py",
    "app/analytics.py", "app/dashboard.py", "app/monitor.py",
    "app/emailer.py", "app/ratelimit.py",
    "app/assistant/llm.py", "app/assistant/tools.py",
    "app/static/index.html", "app/static/officer.html",
    "app/static/assistant.html", "app/static/tracker.js",
]

NEVER_TRACK = ["facility_report.db", "ml/model.pkl", ".env",
               "llm_webapp/.env", "test_online_pg.py"]

SECRET_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "OpenAI-style API key"),
    (re.compile(r"OPENAI_API_KEY\s*=\s*[\"']([^\"']+)[\"']"), "hardcoded key assignment"),
    (re.compile(r"OFFICER_TOKEN\s*=\s*[\"']([^\"']{6,})[\"']"), "hardcoded officer token"),
    (re.compile(r"postgres(?:ql)?://[^\s\"']*:[^\s\"'@]+@"), "database URL with password"),
]

# Values that are obviously examples or test fixtures, not credentials.
PLACEHOLDERS = {"testtoken123", "something-strong", "pilot2026", "changeme",
                "your-token", "your-strong-token"}

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("PASS  " if ok else "FAIL  ") + name + (f"  {detail}" if detail else ""))


def tracked() -> list[str]:
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                             text=True, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("FAIL  not a git repository — run `git init` first")
        sys.exit(1)
    return [line.strip() for line in out.splitlines() if line.strip()]


def main() -> int:
    files = tracked()
    check("repository has tracked files", bool(files), f"{len(files)} files")

    for rel in REQUIRED:
        check(f"required file tracked: {rel}", (ROOT / rel).exists() and rel in files)

    for rel in NEVER_TRACK:
        check(f"not tracked: {rel}", rel not in files)

    offenders = []
    for rel in files:
        path = ROOT / rel
        if not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pattern, label in SECRET_PATTERNS:
            for match in pattern.finditer(text):
                value = match.group(1).strip() if match.groups() else ""
                if value and (value in PLACEHOLDERS or "<" in value or "…" in value):
                    continue
                if value and value.upper() in {"OS.GETENV", "ENVIRON"}:
                    continue
                offenders.append(f"{rel}: {label}")
    check("no credentials in tracked files", not offenders, "; ".join(offenders))

    blueprint = (ROOT / "render.yaml").read_text(encoding="utf-8", errors="ignore")
    services = len(re.findall(r"^\s{2}- type:\s*web", blueprint, re.M))
    check("blueprint defines exactly one web service", services == 1, f"found {services}")
    check("blueprint has a database block", "databases:" in blueprint)
    check("blueprint wires DATABASE_URL", "DATABASE_URL" in blueprint)
    check("blueprint keeps OFFICER_TOKEN out of the file", "sync: false" in blueprint)

    failed = [name for name, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
