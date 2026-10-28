"""Metadata attached to every results/*.json (CLAUDE.md rule 2): git commit, full command,
date, config contents, data versions (sha256 of the input files) and package versions."""
import datetime as dt
import hashlib
import platform
import subprocess
import sys
from pathlib import Path

TAIPEI = dt.timezone(dt.timedelta(hours=8))


def now_taipei():
    return dt.datetime.now(TAIPEI)


def run_stamp():
    """Date and time for result file names, so a new run never overwrites an old result."""
    return now_taipei().strftime("%Y%m%d_%H%M")


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def git_state():
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                                check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True, check=True).stdout.strip()
        return {"commit": commit, "uncommitted_changes": bool(dirty)}
    except Exception:  # no repository yet, or git missing
        return {"commit": None, "uncommitted_changes": None,
                "note": "not run inside a git repository"}


def package_versions(names):
    from importlib import metadata
    out = {}
    for name in names:
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            out[name] = None
    return out


def run_meta(config_path=None, data_files=(), packages=("pymupdf", "pillow", "numpy", "pyyaml")):
    config_path = Path(config_path) if config_path else None
    return {
        "date": now_taipei().isoformat(timespec="seconds"),
        "command": " ".join(["python"] + sys.argv),
        "git": git_state(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "packages": package_versions(packages),
        "config_file": str(config_path) if config_path else None,
        "config": config_path.read_text(encoding="utf-8") if config_path and config_path.is_file() else None,
        "data_sha256": {str(p): sha256_file(p) for p in data_files if Path(p).is_file()},
    }
