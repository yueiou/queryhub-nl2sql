"""检查Git暂存区或当前提交，发现问题时仅输出位置，不输出敏感值。

python scripts/check_public_release.py          # 暂存区
python scripts/check_public_release.py --head   # 当前提交，用于CI
"""
import argparse
from pathlib import Path
import re
import subprocess
import sys


def git(*args):
    return subprocess.check_output(["git", *args])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--head", action="store_true")
    args = parser.parse_args()
    root = Path(git("rev-parse", "--show-toplevel").decode().strip())
    raw = git("ls-tree", "-r", "--name-only", "-z", "HEAD") if args.head else git("ls-files", "--cached", "-z")
    files = [x.decode("utf-8") for x in raw.split(b"\0") if x]
    if not files:
        print("No files to inspect.")
        return 1
    patterns = {
        "credential token": re.compile(rb"(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16})"),
        "private key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        "personal absolute path": re.compile(rb"(?i)(?:[A-Z]:[\\/]+Users[\\/]+|\x2fUsers\x2f|\x2fhome\x2f)[^\s\"'<>]+"),
    }
    # 用本地配置实际的密钥做额外字节匹配，值不输出，也不写入报告。
    local_secrets = []
    env_path = root / "backend/.env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8-sig").splitlines():
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            key, value = line.split("=", 1)
            value = value.strip().strip("\"'")
            if any(part in key.upper() for part in ("API_KEY", "TOKEN", "PASSWORD", "SECRET")) and len(value) >= 12:
                local_secrets.append(value.encode())
    issues = []
    for file in files:
        parts = Path(file).parts
        name = Path(file).name
        if ((name == ".env" or name.startswith(".env.")) and name != ".env.example") or \
           any(x in parts for x in ("node_modules", "__pycache__", ".preview", ".release", ".tools")) or \
           file.startswith(("backend/data/", "backend/eval/results/", "frontend/dist/")) or \
           Path(file).suffix.lower() in {".db", ".sqlite", ".sqlite3", ".pem", ".key", ".pfx", ".p12", ".pyc", ".csv"}:
            issues.append((file, "forbidden runtime/private file"))
            continue
        data = git("show", ("HEAD:" if args.head else ":") + file)
        if len(data) > 10 * 1024 * 1024:
            issues.append((file, "unexpected file larger than 10 MiB"))
        for label, pattern in patterns.items():
            if pattern.search(data):
                issues.append((file, label))
        if any(secret in data for secret in local_secrets):
            issues.append((file, "value matches local credential"))
    for file, reason in issues:
        print(f"FAIL {file}: {reason}")
    print(f"Inspected {len(files)} Git files; findings: {len(issues)}")
    return int(bool(issues))


if __name__ == "__main__":
    sys.exit(main())
