#!/usr/bin/env python3
"""Local project records CLI with cooperating Windows, macOS and Linux writers."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import shutil
import shlex
import sys
import tempfile
import time

if os.name == "nt":
    import msvcrt
else:
    import fcntl

sys.dont_write_bytecode = True
import core


REQUIRED = ("state.json", "CONTEXT.md", "integration.md", "scripts/project.py", "scripts/core.py")
RUNTIME_FILES = ("core.py", "project.py", "ontology.py", "acceptance.py")
INTEGRATION = """# Proje çalışma kaydı

Bu projeye devam ederken önce `.project/state.json` dosyasını oku.
Güncel özet ve kanıt durumunu görmek için proje kökünden çalıştır:

```sh
python3 .project/scripts/project.py context .
```

Windows PowerShell'de `python3` yerine `py -3` (veya Python 3.10+
olduğu doğrulanan `python`) kullan. macOS/Linux'ta `python3` kullan.

Yetkili kayıt `.project/state.json`; `.project/CONTEXT.md` türetilen görünümdür.
Şema 3 ontolojisi ve somut kayıtlar için `python3 .project/scripts/project.py
ontology .` çalıştır; `.project/ONTOLOJİ.md` bunun üretilmiş görünümüdür.
Görev/karar değişikliklerini `.project/scripts/project.py apply` ile, okuduğun
revizyonu `--expected-revision` olarak belirterek işle. Komut seçenekleri için
`python3 .project/scripts/project.py --help` kullan.
Önerileri kabul edilmiş karar sayma. Kontrol başarısını kullanıcı kabulü sayma.
Değişimin etkisini yazmadan görmek için `preview . --event <eylem.json>
--expected-revision <revizyon>` kullan. CLI yazıcıları işletim sistemi kilidiyle sıraya
girer; kayıt dosyasına dışarıdan veya elle paralel durum yazımı yapma.
"""


def emit(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def no_symlink(path: Path) -> None:
    """Check existing path components without first resolving symlinks."""
    for item in (path, *path.parents):
        # macOS ships these system aliases; do not relax project-local links.
        if sys.platform == "darwin" and str(item) in {"/tmp", "/var", "/etc"}:
            if item.resolve() == Path("/private") / item.name:
                continue
        if core.is_link(item):
            raise ValueError(f"Symlink conflict: {item}")


def root_path(value: str, *, missing: bool = False) -> Path:
    root = Path(os.path.abspath(os.path.expanduser(value)))
    no_symlink(root)
    if root.exists() and not root.is_dir():
        raise ValueError(f"Project root is not a directory: {root}")
    if not root.exists() and not missing:
        raise ValueError(f"Project root does not exist: {root}")
    # Missing parents are allowed for init only, but existing ancestors must be dirs.
    for parent in root.parents:
        if parent.exists() and not parent.is_dir():
            raise ValueError(f"Root ancestor is not a directory: {parent}")
    return root


def normal_file(path: Path) -> None:
    no_symlink(path)
    if not path.is_file():
        raise ValueError(f"Required regular file missing or invalid: {path}")


def optional_file(path: Path) -> None:
    no_symlink(path)
    if path.exists() and not path.is_file():
        raise ValueError(f"File conflict: {path}")


def read_json(path: Path) -> dict:
    normal_file(path)
    def reject_constant(value: str) -> None:
        raise ValueError(f"Invalid JSON constant: {value}")
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result
    with path.open(encoding="utf-8-sig") as handle:
        value = json.load(handle, parse_constant=reject_constant, object_pairs_hook=unique_object)
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def write_new(path: Path, content: str) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def atomic_write(path: Path, content: str) -> None:
    optional_file(path)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        optional_file(path)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def json_text(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


@contextlib.contextmanager
def writer_lock(value: str):
    """Serialize CLI writers for one lexical project root without changing its tree.

    POSIX uses flock; Windows locks byte zero with msvcrt. These coordinate
    cooperating writers, not authentication. A crash releases the descriptor.
    """
    root = os.path.abspath(os.path.expanduser(value))
    no_symlink(Path(root))
    if sys.platform == "darwin" or os.name == "nt":
        root = os.path.normcase(str(Path(root).resolve()))
    user = (str(os.getuid()) if os.name != "nt" else
            hashlib.sha256(str(Path.home()).encode()).hexdigest()[:16])
    directory = Path(tempfile.gettempdir()).resolve() / f"proje-baslat-locks-{user}"
    directory.mkdir(mode=0o700, exist_ok=True)
    no_symlink(directory)
    info = directory.stat()
    if not directory.is_dir() or (os.name != "nt" and
            (info.st_uid != os.getuid() or info.st_mode & 0o077)):
        raise ValueError("Writer lock directory must be private and owned by current user")
    name = hashlib.sha256(root.encode()).hexdigest() + ".lock"
    lock_path = directory / name
    optional_file(lock_path)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if os.name == "nt":
            # Locking a byte beyond EOF is supported; no initialization write
            # can race with another process already holding the lock.
            deadline = time.monotonic() + 30
            while True:
                try:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Writer lock busy; retry after the current writer finishes")
                    time.sleep(0.05)
        else:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def load_project(root: Path, *, complete: bool = False) -> dict:
    folder = root / ".project"
    no_symlink(folder)
    if not folder.is_dir():
        raise ValueError("Project is not initialized: .project directory missing or invalid")
    for entry in folder.rglob("*"):
        if core.is_link(entry):
            raise ValueError(f"Managed symlink conflict: {entry}")
    required = REQUIRED if complete else ("state.json", "scripts/project.py", "scripts/core.py")
    for relative in required:
        normal_file(folder / relative)
    for relative in ("CONTEXT.md", "ONTOLOJİ.md", "integration.md", "scripts/ontology.py", "scripts/acceptance.py"):
        optional_file(folder / relative)
    state = read_json(folder / "state.json")
    core.validate(state)
    if complete and state["schema_version"] == 3:
        normal_file(folder / "scripts" / "ontology.py")
        normal_file(folder / "scripts" / "acceptance.py")
    return state


def integration_status(root: Path, *, created: bool = False) -> dict:
    overrides = [str(path) for path in (root / "AGENTS.override.md",) if path.exists()]
    arguments = [str(root / ".project/scripts/project.py"), "context", str(root)]
    command = ("& " + " ".join("'" + arg.replace("'", "''") + "'"
                               for arg in [sys.executable, *arguments])
               if os.name == "nt" else "python3 " + shlex.join(arguments))
    return {
        "continue_command": command,
        "continue_shell": "powershell" if os.name == "nt" else "posix",
        "agents_created": created,
        "existing_agents_preserved": not created and (root / "AGENTS.md").exists(),
        "integration": "created" if created and not overrides else "review_required",
        "overrides": overrides,
        "note": (
            "Root override may shadow AGENTS.md; review .project/integration.md."
            if overrides else
            "Review .project/integration.md against existing project instructions."
            if not created else
            "Root guidance created; automatic discovery in other working directories is not guaranteed."
        ),
    }


def initialize(args: argparse.Namespace) -> int:
    root = root_path(args.root, missing=True)
    for name in ("AGENTS.md", "AGENTS.override.md"):
        optional_file(root / name)
    folder = root / ".project"
    no_symlink(folder)
    if folder.exists():
        try:
            existing = load_project(root, complete=True)
        except (ValueError, OSError) as error:
            raise ValueError(f"Existing installation conflict; nothing changed: {error}") from error
        emit({"ok": True, "result": "already_initialized", "revision": existing["revision"],
              "root": str(root), **integration_status(root)})
        return 0

    spec = read_json(Path(os.path.abspath(args.spec)))
    core.validate(spec)
    if spec["schema_version"] not in (1, 2, 3) or spec["revision"] != 0 or spec["history"] != []:
        raise ValueError("Initial spec requires schema_version=1, 2 or 3, revision=0, history=[]")
    if any(task["status"] != "todo" or task["evidence"] for task in spec["tasks"]):
        raise ValueError("Initial tasks must be todo with empty evidence")
    if any(decision["status"] not in ("proposed", "accepted") for decision in spec["decisions"]):
        raise ValueError("Initial decisions must be proposed or accepted")
    for decision in spec["decisions"]:
        if decision["status"] == "accepted" and (
            not decision["accepted_by"] or not decision["source"].strip()
        ):
            raise ValueError("Initial accepted decision requires accepted_by and source")
    source = Path(__file__).resolve().parent
    for name in RUNTIME_FILES:
        normal_file(source / name)
    # Render and validate before creating the destination root or staging files.
    context = core.render_context(spec, root)
    ontology_view = core.render_ontology(spec, root) if spec["schema_version"] == 3 else None
    root.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".project-stage-", dir=root))
    committed = False
    try:
        write_new(stage / "state.json", json_text(spec))
        write_new(stage / "CONTEXT.md", context)
        if ontology_view is not None:
            write_new(stage / "ONTOLOJİ.md", ontology_view)
        write_new(stage / "integration.md", INTEGRATION)
        (stage / "scripts").mkdir()
        for name in RUNTIME_FILES:
            shutil.copyfile(source / name, stage / "scripts" / name)
        # This precondition is not a lock: callers must serialize writers.
        if folder.exists() or folder.is_symlink():
            raise ValueError("Installation target appeared during init; nothing overwritten")
        stage.rename(folder)
        committed = True
    finally:
        if not committed and stage.exists():
            shutil.rmtree(stage)
    created = False
    warning = None
    try:
        optional_file(root / "AGENTS.md")
        write_new(root / "AGENTS.md", INTEGRATION)
        created = True
    except FileExistsError:
        pass
    except (OSError, ValueError) as error:
        warning = f"State initialized; AGENTS integration not written: {error}"
    result = {"ok": True, "result": "initialized", "revision": 0,
              "root": str(root), "state_committed": True, **integration_status(root, created=created)}
    if warning:
        result["warning"] = warning
    emit(result)
    return 0


def write_new_bytes(path: Path, content: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def upgrade_command(args: argparse.Namespace) -> int:
    """Replace runtime files, preserving state and permanent original backups.

    One writer only. Each replacement is atomic, but the whole set is not: caught
    installation errors trigger best-effort restoration. A process interruption
    can require manual restoration from the reported backup directory.
    """
    root = root_path(args.root)
    state = load_project(root)
    source = Path(os.path.abspath(__file__)).parent
    no_symlink(source)
    destination = root / ".project" / "scripts"
    names = RUNTIME_FILES
    incoming = {}
    previous = {}
    for name in names:
        normal_file(source / name)
        incoming[name] = (source / name).read_bytes()
        optional_file(destination / name)
        previous[name] = (destination / name).read_bytes() if (destination / name).exists() else None
    common = {"root": str(root), "revision": state["revision"], "state_changed": False}
    if incoming == previous:
        emit({"ok": True, "result": "noop", "backup": None, **common})
        return 0
    if source.name == "scripts" and source.parent.name == ".project":
        raise ValueError("Run upgrade from the source skill package, not a project's copied runtime")

    folder = root / ".project"
    backups = folder / "runtime-backups"
    no_symlink(backups)
    backups.mkdir(exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix="upgrade-", dir=backups))
    stage = None
    replaced = []
    try:
        # Preserve all originals before replacing any file. Never execute
        # the target project's potentially obsolete runtime to read its data.
        for name in names:
            if previous[name] is not None:
                write_new_bytes(backup / name, previous[name])
        write_new(backup / "manifest.json", json_text({"files": {
            name: {"previously_present": previous[name] is not None} for name in names}}))
        write_new(backup / "README.txt",
                  "Original runtime before upgrade. With no project writer running, "
                  "restore BOTH core.py and project.py; for ontology.py and acceptance.py, "
                  "restore each only if manifest.json says previously_present; otherwise remove it. "
                  "Destination: ../../scripts/. "
                  "state.json was not changed by this upgrade.\n")
        stage = Path(tempfile.mkdtemp(prefix=".runtime-stage-", dir=folder))
        for name in names:
            write_new_bytes(stage / name, incoming[name])
            if previous[name] is not None:
                write_new_bytes(stage / (name + ".restore"), previous[name])
        for name in names:
            optional_file(destination / name)
            os.replace(stage / name, destination / name)
            replaced.append(name)
    except (OSError, ValueError) as error:
        restore_errors = []
        for name in reversed(replaced):
            try:
                normal_file(destination / name)
                if previous[name] is None:
                    (destination / name).unlink()
                else:
                    os.replace(stage / (name + ".restore"), destination / name)
            except (OSError, ValueError) as restore_error:
                restore_errors.append(f"{name}: {restore_error}")
        emit({"ok": False, "result": "restore_failed" if restore_errors else "restored",
              "backup": str(backup), "error": str(error), "restore_errors": restore_errors,
              "recovery": "Stop project writers and restore BOTH original runtime files, then restore "
                          "or remove ontology.py and acceptance.py according to the backup manifest."
                          if restore_errors else "Previous runtime preserved; retry upgrade after fixing the error.",
              **common})
        return 1
    finally:
        if stage is not None:
            # Temporary cleanup must not obscure the runtime/rollback result.
            shutil.rmtree(stage, ignore_errors=True)
    emit({"ok": True, "result": "updated", "backup": str(backup), **common})
    return 0


def inspect_command(args: argparse.Namespace) -> int:
    root = root_path(args.root)
    state = load_project(root)
    report = core.inspect_state(state, root)
    if args.command == "ontology":
        if args.json:
            emit({"schema_version": state["schema_version"], "revision": state["revision"],
                  **{key: report.get(key, state.get(key)) for key in
                     ("ontology", "objects", "relations", "tasks", "object_status")}})
        else:
            print(core.render_ontology(state, root), end="")
        return 0
    view_warnings = []
    if not (root / ".project" / "CONTEXT.md").exists():
        view_warnings.append("CONTEXT.md is missing; live context is computed from state.json.")
    if view_warnings:
        report["view_warnings"] = view_warnings
    if args.command == "check":
        warnings = report.get("warnings", [])
        emit({"ok": not bool(warnings), "result": "checked", **report})
        return 1 if warnings else 0
    if args.json:
        emit(report)
    else:
        for warning in view_warnings:
            print(f"> {warning}\n")
        print(core.render_context(state, root), end="")
    return 0


def apply_command(args: argparse.Namespace) -> int:
    root = root_path(args.root)
    state = load_project(root)
    if state["revision"] != args.expected_revision:
        raise ValueError(f"Revision conflict: expected {args.expected_revision}, current {state['revision']}")
    event = read_json(Path(os.path.abspath(args.event)))
    next_state = core.apply_event(state, event, root)
    core.validate(next_state)
    observed_digest = core.preview_digest(state, event, root, next_state)
    if getattr(args, "preview_digest", None) and args.preview_digest != observed_digest:
        raise ValueError("Preview digest conflict: state, event or referenced files changed; preview again")
    folder = root / ".project"
    # Compute the view before committing, so semantic/render errors cannot partially apply.
    context = core.render_context(next_state, root)
    ontology_view = core.render_ontology(next_state, root) if next_state["schema_version"] == 3 else None
    if core.preview_digest(state, event, root) != observed_digest:
        raise ValueError("Files changed during apply; preview again")
    backup = None
    if event.get("action") == "migrate_ontology":
        backups = folder / "migration-backups"
        no_symlink(backups)
        backups.mkdir(exist_ok=True)
        backup = Path(tempfile.mkdtemp(prefix=f"revision-{state['revision']}-", dir=backups))
        write_new_bytes(backup / "state.json", (folder / "state.json").read_bytes())
        write_new(backup / "README.txt", "State before ontology migration. Stop writers before "
                  "restoring state.json; use a runtime compatible with its schema.\n")
    atomic_write(folder / "state.json", json_text(next_state))
    try:
        atomic_write(folder / "CONTEXT.md", context)
        if ontology_view is not None:
            atomic_write(folder / "ONTOLOJİ.md", ontology_view)
    except (OSError, ValueError) as error:
        emit({"ok": False, "result": "state_committed_view_failed", "state_committed": True,
              "revision": next_state["revision"], "error": str(error),
              "backup": str(backup) if backup else None,
              "recovery": "Do not retry this event. Read current state with the context command."})
        return 1
    emit({"ok": True, "result": "applied", "state_committed": True,
          "backup": str(backup) if backup else None,
          "revision": next_state["revision"], "action": event.get("action")})
    return 0


def preview_command(args: argparse.Namespace) -> int:
    root = root_path(args.root)
    state = load_project(root)
    if state["revision"] != args.expected_revision:
        raise ValueError(f"Revision conflict: expected {args.expected_revision}, current {state['revision']}")
    event = read_json(Path(os.path.abspath(args.event)))
    report = core.preview_event(state, event, root)
    emit({**report, "ok": True, "result": "previewed", "state_committed": False,
          "revision": state["revision"]})
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Initialize from a v1/v2/v3 spec; preserve existing records")
    init.add_argument("root")
    init.add_argument("--spec", required=True)
    upgrade = commands.add_parser("upgrade", help="Upgrade copied runtime from this source package; serialize writers")
    upgrade.add_argument("root")
    check = commands.add_parser("check", help="Validate structure and current evidence")
    check.add_argument("root")
    context = commands.add_parser("context", help="Read current context, recomputing evidence state")
    context.add_argument("root")
    context.add_argument("--json", action="store_true")
    ontology = commands.add_parser("ontology", help="Read ontology definitions and live object context")
    ontology.add_argument("root")
    ontology.add_argument("--json", action="store_true")
    preview = commands.add_parser("preview", help="Validate an event and inspect its impact without writing")
    preview.add_argument("root")
    preview.add_argument("--event", required=True)
    preview.add_argument("--expected-revision", type=int, required=True)
    apply = commands.add_parser("apply", help="Apply one event; caller must serialize writers")
    apply.add_argument("root")
    apply.add_argument("--event", required=True)
    apply.add_argument("--expected-revision", type=int, required=True)
    apply.add_argument("--preview-digest", help="Require the exact state/event/file observations from preview")
    return result


def main(argv: list[str] | None = None) -> int:
    # Pipes on Windows otherwise inherit an ANSI code page and lose Turkish.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    try:
        if args.command in {"init", "apply", "upgrade"}:
            with writer_lock(args.root):
                return {"init": initialize, "apply": apply_command,
                        "upgrade": upgrade_command}[args.command](args)
        if args.command == "preview":
            return preview_command(args)
        return inspect_command(args)
    except (ValueError, OSError, TypeError, KeyError) as error:
        emit({"ok": False, "result": "error", "error": str(error)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
