"""Package and apply a source release without touching installation secrets."""
import os
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

DIRECTORIES = {"alart", "web", "db", "tests"}
ROOT_FILES = {"VERSION", "Dockerfile", "compose.yaml", "Caddyfile.template", "requirements.txt", ".env.sample", ".gitignore"}
ROOT_SCRIPTS = {"install.sh", "update.sh", "backup.sh", "restore.sh", "schedule-backups.sh"}
ROOT_DOCS = {"README.md", "DEPLOYMENT.md", "OPERATIONS.md", "INTEGRATIONS.md", "SECURITY.md", "ARCHITECTURE.md", "DEVICE_LIMIT_FEASIBILITY.md", "PRODUCTION_READINESS.md"}
MAX_FILE = 10 * 1024 * 1024
MAX_TOTAL = 100 * 1024 * 1024


def allowed(name):
    if "\\" in name or ":" in name:
        return False
    path = PurePosixPath(name)
    if str(path) != name or path.is_absolute() or len(path.parts) == 0 or any(part in ("", ".", "..") for part in path.parts):
        return False
    if len(path.parts) == 1:
        return name in ROOT_FILES or name in ROOT_SCRIPTS or name in ROOT_DOCS
    return path.parts[0] in DIRECTORIES and "__pycache__" not in path.parts and path.suffix != ".pyc"


def pack(destination, root=Path.cwd()):
    root = Path(root)
    files = []
    for path in root.iterdir():
        if path.is_file() and allowed(path.name):
            files.append(path)
        elif path.is_dir() and path.name in DIRECTORIES:
            files.extend(candidate for candidate in path.rglob("*") if candidate.is_file() and allowed(candidate.relative_to(root).as_posix()))
    if not (root / "VERSION").is_file():
        raise ValueError("VERSION is required")
    with tarfile.open(destination, "w:gz") as archive:
        for path in sorted(files):
            if path.is_symlink() or path.stat().st_size > MAX_FILE:
                raise ValueError(f"Unsupported release member: {path}")
            archive.add(path, arcname=path.relative_to(root).as_posix(), recursive=False)


def apply(source, root=Path.cwd()):
    root = Path(root).resolve()
    with tarfile.open(source, "r:gz") as archive:
        entries = archive.getmembers()
        names = [entry.name for entry in entries]
        if len(entries) > 10000 or len(names) != len(set(names)) or "VERSION" not in names:
            raise ValueError("Release has duplicate files or no VERSION")
        total = 0
        for entry in entries:
            if not entry.isfile() or not allowed(entry.name) or entry.size > MAX_FILE:
                raise ValueError(f"Unsupported release member: {entry.name}")
            total += entry.size
        if total > MAX_TOTAL:
            raise ValueError("Release is too large")
        with tempfile.TemporaryDirectory(prefix="alart-release-", dir=root) as temporary:
            staged = Path(temporary)
            for entry in entries:
                target = staged / entry.name
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(entry) as stream, target.open("wb") as output:
                    shutil.copyfileobj(stream, output)
                if target.stat().st_size != entry.size:
                    raise ValueError("Incomplete release member")
            for entry in entries:
                target = root / entry.name
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged / entry.name, target)
                target.chmod(0o755 if target.suffix == ".sh" else 0o644)


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ("pack", "apply"):
        raise SystemExit("Usage: python -m alart.release pack|apply ARCHIVE.tar.gz")
    globals()[sys.argv[1]](sys.argv[2])


if __name__ == "__main__":
    main()
