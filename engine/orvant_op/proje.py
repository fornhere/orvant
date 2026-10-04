"""Deterministic schema-3 project handoff; no intake/planning model calls.

The local project remains authoritative. A digest approval authorizes only the
selected tasks, exact files, pinned command oracle and bounded worker calls.
This is a local cooperating-writer protocol, not an authentication boundary.
"""
from __future__ import annotations

import argparse
import contextlib
from contextvars import ContextVar
import copy
import fcntl
import hashlib
import json
import multiprocessing
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from concurrent.futures import ProcessPoolExecutor

BINDING = "proje.json"
STATE = "proje-durum.json"
PAUSE = "proje-pause.json"
DEFINITION = ("id", "title", "object_ids", "input_ids", "output_ids", "depends_on",
              "decision_ids", "acceptance", "input_fields", "support_groups", "acceptance_rules")
RUNTIME = ("project.py", "core.py", "ontology.py", "acceptance.py", "graph.py", "derive.py")
ENGINE_FILES = ("proje.py", "yurutucu.py", "butce.py", "ayarlar.py", "operator/dongu.py",
                "yurutme/akis.py", "yurutme/korumali.py", "yurutme/zamanlayici.py",
                "yurutme/butce_defteri.py")
_EXECUTING = ContextVar("orvant_project_execution", default=False)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
        temporary = stream.name
    os.replace(temporary, path)


def _root(path):
    result = Path(path).expanduser().absolute()
    if any(p.is_symlink() for p in (result, *result.parents)):
        raise ValueError(f"symbolic root forbidden: {path}")
    return result.resolve()


def _file(root, relative):
    path = PurePosixPath(relative)
    if (not isinstance(relative, str) or not relative or path.is_absolute()
            or any(p in ("..", ".git", ".project", ".orvant") for p in path.parts)
            or str(path) != relative or "\\" in relative or "\x00" in relative
            or any(c in relative for c in "*?:")):
        raise ValueError(f"exact safe project-relative file required: {relative}")
    result = Path(root)
    for part in path.parts:
        result /= part
        if result.is_symlink():
            raise ValueError(f"symbolic file forbidden: {relative}")
    if result.exists() and not result.is_file():
        raise ValueError(f"regular file required: {relative}")
    return result


def _hash(root, relative):
    path = _file(root, relative)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def _git(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=False)
    if result.returncode:
        raise ValueError(result.stderr.decode(errors="replace").strip())
    return result.stdout


def _proje_on_kontrol_ayari(root):
    path = Path(root) / "orvant.toml"
    if not path.is_file():
        return {}
    with path.open("rb") as stream:
        value = tomllib.load(stream).get("proje", {})
    if not isinstance(value, dict):
        raise ValueError("orvant.toml [proje] tablo olmalı")
    unknown = set(value) - {"taban_ref", "guncellik"}
    if unknown:
        raise ValueError("bilinmeyen [proje] ayarı: " + ", ".join(sorted(unknown)))
    if "taban_ref" in value and (not isinstance(value["taban_ref"], str) or not value["taban_ref"].strip()):
        raise ValueError("[proje].taban_ref boş olmayan metin olmalı")
    if value.get("guncellik", "uyar") not in ("uyar", "engelle"):
        raise ValueError("[proje].guncellik uyar veya engelle olmalı")
    return value


def _varsayilan_taban_ref(root):
    symbolic = subprocess.run(["git", "-C", str(root), "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"],
                              capture_output=True, text=True, check=False)
    if symbolic.returncode == 0:
        return symbolic.stdout.strip().removeprefix("refs/remotes/")
    branch = _git(root, "branch", "--show-current").decode().strip()
    return "origin/" + branch if branch else None


def _ilk_oge(command):
    try:
        parts = shlex.split(command)
    except ValueError:
        return None
    index = 0
    while index < len(parts) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", parts[index]):
        index += 1
    if index < len(parts) and parts[index] == "env":
        index += 1
        while index < len(parts) and (parts[index].startswith("-") or
                                      re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", parts[index])):
            index += 1
    return parts[index] if index < len(parts) else None


def _on_kontrol(root, config):
    """Ağsız depo/ortam ön kontrolü; yalnız mevcut ref ve dosyalara bakar."""
    root = Path(root)
    setting = _proje_on_kontrol_ayari(root)
    mode = setting.get("guncellik", "uyar")
    base = setting.get("taban_ref") or _varsayilan_taban_ref(root)
    freshness = {"durum": "bilinmiyor", "taban_ref": base, "kip": mode}
    warnings, blockers = [], []
    if base:
        exists = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", "--quiet", base + "^{commit}"],
                                capture_output=True, check=False).returncode == 0
        if exists:
            counts = _git(root, "rev-list", "--left-right", "--count", "HEAD..." + base).decode().split()
            ahead, behind = map(int, counts)
            freshness.update(ileride=ahead, geride=behind)
            freshness["durum"] = "ayrismis" if ahead and behind else "geride" if behind else "guncel"
    if freshness["durum"] in ("geride", "ayrismis"):
        message = f"Depo HEAD {base} ile karşılaştırıldığında {freshness['durum']}."
        (blockers if mode == "engelle" else warnings).append(message)
    elif freshness["durum"] == "bilinmiyor":
        warnings.append(f"Depo güncelliği bilinmiyor: yerel {base or 'yukarı akış'} ref'i yok; fetch yapılmadı.")

    commands = []
    for task in config.get("tasks", []):
        commands.extend(task.get("setup", []))
        commands.extend(check.get("command") for check in task.get("checks", []) if check.get("command"))
        oracle = task.get("oracle", {})
        if oracle.get("command"):
            commands.append(oracle["command"])
    tools = []
    for command in commands:
        item = _ilk_oge(command)
        if not item:
            tools.append({"komut": None, "bulundu": False, "kaynak": "ayrıştırılamadı"})
            warnings.append(f"Doğrulama komutu ayrıştırılamadı: {command!r}")
            continue
        if "/" in item:
            candidate = Path(item) if Path(item).is_absolute() else root / item
            found = candidate.is_file() and os.access(candidate, os.X_OK)
            source = str(candidate)
        else:
            source = shutil.which(item)
            found = source is not None
        tools.append({"komut": item, "bulundu": found, "kaynak": source})
        if not found:
            warnings.append(f"Doğrulama aracı bulunamadı: {item}")
    tmpdir = os.environ.get("TMPDIR", tempfile.gettempdir())
    socket_risk = len(os.fsencode(tmpdir)) > 90
    if socket_risk:
        warnings.append(f"TMPDIR Unix soket yolu için uzunluk riski taşıyor ({len(os.fsencode(tmpdir))} bayt): {tmpdir}")
    summary = (f"Depo güncelliği: {freshness['durum']} ({base or 'ref yok'}, kip={mode}); "
               f"doğrulama araçları: {sum(t['bulundu'] for t in tools)}/{len(tools)} bulundu; "
               f"Unix soket yolu riski: {'var' if socket_risk else 'yok'}.")
    return {"depo_guncelligi": freshness,
            "ortam": {"dogrulama_araclari": tools, "tmpdir": tmpdir, "unix_soket_riski": socket_risk},
            "uyarilar": warnings, "engeller": blockers, "ozet": summary}


def _project(root, command, *args):
    result = subprocess.run([sys.executable, str(Path(root) / ".project/scripts/project.py"),
                             command, str(root), *map(str, args)], capture_output=True, text=True,
                            stdin=subprocess.DEVNULL, timeout=30)
    try:
        value = json.loads(result.stdout)
    except ValueError as exc:
        raise ValueError(f"project CLI failed: {result.stderr[-1000:]}") from exc
    if result.returncode or value.get("ok") is False:
        raise ValueError(value.get("error") or value.get("warnings") or "project command rejected")
    return value


def _event(root, action, task_id=None):
    context = _project(root, "context", "--json")
    value = {"actor": "agent:orvant-engine", "reason": "Native engine handoff with recorded evidence", **action}
    if task_id is not None:
        value["task_id"] = task_id
    with tempfile.TemporaryDirectory(prefix="orvant-project-event-") as temporary:
        path = Path(temporary) / "event.json"
        _write(path, value)
        preview = _project(root, "preview", "--event", path,
                           "--expected-revision", context["revision"])
        _project(root, "apply", "--event", path, "--expected-revision", context["revision"],
                 "--preview-digest", preview["preview_digest"])
    return preview


def _selection(context, raw, task_id, *, input_only=False):
    task = next((t for t in raw["tasks"] if t["id"] == task_id), None)
    if task is None or raw.get("schema_version") != 3:
        raise ValueError(f"schema-3 task required: {task_id}")
    if task.get("support_groups"):
        raise ValueError("explicit alternative-support review is required before engine handoff")
    selected = set(task["input_ids"] if input_only else task["object_ids"])
    # Same declared impact direction as the project ontology. Context is data,
    # never permission, and no file content or whole history is put in prompts.
    types = {r["id"]: r for r in raw["ontology"]["relation_types"]}
    changed = True
    while changed:
        previous = len(selected)
        for relation in raw["relations"]:
            impact = types[relation["type"]]["impact"]
            if impact in ("forward", "both") and relation["to"] in selected:
                selected.add(relation["from"])
            if impact in ("reverse", "both") and relation["from"] in selected:
                selected.add(relation["to"])
        changed = len(selected) != previous
    objects = [o for o in raw["objects"] if o["id"] in selected]
    relations = [r for r in raw["relations"] if r["from"] in selected and r["to"] in selected]
    object_types = {o["type"] for o in objects}
    relation_types = {r["type"] for r in relations}
    relation_types.update(t["id"] for t in raw["ontology"]["relation_types"]
                          if t["impact"] != "none" and
                          (t["from_type"] in object_types or t["to_type"] in object_types))
    shape = {"definition": {k: task[k] for k in DEFINITION if k in task},
             "objects": objects, "relations": relations,
             "ontology": {"object_types": [t for t in raw["ontology"]["object_types"] if t["id"] in object_types],
                          "relation_types": [t for t in raw["ontology"]["relation_types"] if t["id"] in relation_types]},
             "decisions": [d for d in raw["decisions"] if d["id"] in task["decision_ids"]]}
    computed = next(t for t in context["tasks"] if t["id"] == task_id)
    properties = {t["id"]: t["properties"] for t in raw["ontology"]["object_types"]}
    files = {value for o in objects for key, value in o["properties"].items()
             if properties[o["type"]][key]["type"] == "file"}
    output_files = {value for o in objects if o["id"] in task["output_ids"]
                    for key, value in o["properties"].items()
                    if properties[o["type"]][key]["type"] == "file"}
    return shape, computed, files, output_files


def load(session):
    path = Path(session) / BINDING
    return _read(path) if path.exists() else None


def _plan_contract(plan):
    result = copy.deepcopy(plan)
    for task in result["gorevler"]:
        task.pop("durum", None)
    return result


@contextlib.contextmanager
def _lock(session):
    root = _root(session)
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".proje.lock").open("a+") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("another project controller is running") from exc
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def suggestions(root, maximum=None):
    root = _root(root)
    derived = _project(root, "derive")
    lanes = _project(root, "lanes", *(["--max", maximum] if maximum is not None else []))
    if derived["revision"] != lanes["revision"]:
        raise ValueError("project changed while deriving proposals; retry")
    from .koordinasyon import cakismalari_bul
    for proposal in derived["proposals"]:
        kimlik = proposal.get("id") or proposal.get("task_id") or proposal.get("task") or ""
        dosyalar = proposal.get("write_scope") or proposal.get("writable") or proposal.get("files") or proposal.get("dosyalar") or []
        if isinstance(dosyalar, str):
            dosyalar = [dosyalar]
        proposal["cakismalar"] = cakismalari_bul(root, kimlik, dosyalar)
    categories = {}
    for proposal in derived["proposals"]:
        kind = proposal["kind"]
        categories[kind] = categories.get(kind, 0) + 1
    return {"revision": derived["revision"], "proposal_count": len(derived["proposals"]),
            "categories": dict(sorted(categories.items())), "proposals": derived["proposals"],
            "skipped": derived["skipped"], "lanes": lanes["lanes"],
            "queued_lanes": lanes["queued_lanes"], "state_committed": False,
            "single_writer": lanes["single_writer"]}



def _verification_records(root):
    # Invoke the installed skill runtime in a read-only subprocess. Derive does
    # not re-emit open tasks, so resolve their recorded metadata with its same
    # resolver/classifier rather than inventing commands from filenames.
    code = """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import derive
state = json.loads(Path(sys.argv[2]).read_text())
context = derive._CodeContext(state)
records = {}
for task in state['tasks']:
    identity = 'derived-' + derive._hash({'kind': 'inspect', 'ids': sorted(task['object_ids']),
                                         'criteria': task['acceptance'], 'rules': []})[:20]
    suffix = task['id'][len(identity) + 1:]
    verification = (task['id'] in context.verification_tasks or task['id'] == identity or
                    (task['id'].startswith(identity + '-') and suffix.isdigit()))
    if verification:
        record = context.resolve(task['object_ids'], verification_only=True)
        record['evidence_dirs'] = sorted({config['evidence_dir'] for key in task['object_ids']
            for config in [context.objects[key]['properties'].get('verification')]
            if isinstance(config, dict) and isinstance(config.get('evidence_dir'), str)})
        records[task['id']] = record
print(json.dumps({'revision': state['revision'], 'records': records}))
"""
    result = subprocess.run([sys.executable, '-B', '-c', code,
                             str(root / '.project/scripts'), str(root / '.project/state.json')],
                            capture_output=True, text=True, input='', timeout=30)
    if result.returncode:
        raise ValueError('verification metadata failed: ' + result.stderr[-1000:])
    return json.loads(result.stdout)


def _working_tree(root):
    # Status alone misses edits to an already dirty file. Hash tracked and
    # untracked contents as well; managed records have their own revision guard.
    status = _git(root, '--no-optional-locks', 'status', '--porcelain', '-z',
                  '--untracked-files=all', '--', '.', ':(exclude).project')
    names = _git(root, 'ls-files', '-z', '--cached', '--others', '--exclude-standard').decode().split('\0')
    files = {}
    for name in names:
        if name and not name.startswith('.project/'):
            files[name] = _hash(root, name)
    return _digest({'status': status.hex(), 'files': files,
                    'head': _git(root, 'rev-parse', 'HEAD').decode().strip()})


def _verification_source(root):
    # Prepared project sessions are siblings of the project/execution roots.
    bindings = []
    for path in sorted(root.parent.glob('*/' + BINDING)):
        binding = _read(path)
        contract = binding.get('contract', {})
        if contract.get('project') == str(root):
            bindings.append(contract)
    sources = {c['execution_repo'] for c in bindings}
    if len(sources) > 1:
        raise ValueError('ambiguous execution repositories for verification')
    source = _root(next(iter(sources))) if sources else root
    # Managed .project state may be untracked in the authoritative repository.
    exclusions = ['.project']
    if source == root:
        for obj in _read(root / '.project/state.json')['objects']:
            config = obj['properties'].get('verification')
            if isinstance(config, dict) and config.get('evidence_dir'):
                directory = config['evidence_dir']
                _file(root, directory + '/verification.json')
                exclusions.append(directory)
    dirty = _git(source, '--no-optional-locks', 'status', '--porcelain',
                 '--untracked-files=all', '--', '.',
                 *(':(exclude)' + path for path in exclusions))
    if dirty.strip():
        raise ValueError('verification execution repository must be clean')
    timeout = min((c['policy']['command_timeout'] for c in bindings), default=30)
    return source, timeout


def _verification_lane(jobs, source, timeout):
    results = []
    for job in jobs:
        result = {'task': job['task'], 'commands': [], 'done': False}
        with tempfile.TemporaryDirectory(prefix='orvant-verification-') as temporary:
            tree = Path(temporary) / 'execution'
            try:
                _git(source, 'clone', '--no-hardlinks', '--', str(source), str(tree))
                result['cwd'] = str(tree)
                paths = sorted(job['hashes'])
                result['hashes'] = {p: _hash(tree, p) for p in paths}
                if any(v is None for v in result['hashes'].values()):
                    raise ValueError('verification input missing from execution copy')
                if result['hashes'] != job['hashes']:
                    raise ValueError('execution inputs differ from authoritative project')
                for command in job['commands']:
                    started = time.monotonic()
                    try:
                        check = _command(command, tree, timeout, session=temporary)
                    except subprocess.TimeoutExpired as exc:
                        chunks = (exc.stdout or '', exc.stderr or '')
                        output = ''.join(c.decode(errors='replace') if isinstance(c, bytes) else c
                                         for c in chunks)
                        check = {'command': command, 'exit_code': None,
                                 'output': output[-6000:], 'error': 'command timeout'}
                    finished = time.monotonic()
                    check.update(started=started, finished=finished, duration=finished - started)
                    result['commands'].append(check)
                    if {p: _hash(tree, p) for p in paths} != result['hashes']:
                        raise ValueError('verification inputs changed in execution copy')
                    if check['exit_code'] != 0:
                        raise ValueError(check.get('error', 'verification command failed'))
                result['passed'] = True
            except (ValueError, OSError, RuntimeError) as exc:
                result.update(passed=False, error=str(exc))
        results.append(result)
    return results


def verify(root, *, tasks=None, maximum=None, execute=False, timeout=None):
    root = _root(root)
    maximum = 1 if maximum is None else maximum
    if type(maximum) is not int or maximum < 1:
        raise ValueError('--max must be positive')
    if execute:
        with _lock(root / '.project'):
            return _verify(root, tasks, maximum, True, timeout)
    return _verify(root, tasks, maximum, False, timeout)


def _verify(root, selected, maximum, execute, timeout):
    context = _project(root, 'context', '--json')
    metadata = _verification_records(root)
    lanes = _project(root, 'lanes')
    if len({context['revision'], metadata['revision'], lanes['revision']}) != 1:
        raise ValueError('project changed while planning verification')
    raw = _read(root / '.project/state.json')
    if raw['revision'] != context['revision']:
        raise ValueError('project changed while planning verification')
    views = {t['id']: t for t in context['tasks']}
    wanted = set(selected) if selected else set(views)
    if wanted - set(views):
        raise ValueError('unknown verification task: ' + ', '.join(sorted(wanted - set(views))))
    jobs, skipped, groups = [], [], []
    for lane in lanes['lanes'] + lanes['queued_lanes']:
        group = []
        for tid in lane['tasks'] + lane['queued_tasks']:
            if tid not in wanted:
                continue
            record = metadata['records'].get(tid)
            reason = None
            if tid not in context['ready']:
                reason = 'task not ready'
            elif record is None:
                reason = 'task is not recorded verification work'
            elif not record['verification'].get('commands'):
                reason = 'no recorded command; command not inferred'
            elif not record['write_scope'] or not record['evidence_dirs']:
                reason = 'no recorded evidence directory'
            elif record.get('truncated'):
                reason = 'verification metadata truncated'
            if reason:
                skipped.append({'task': tid, 'reason': reason})
                continue
            if not selected and len(groups) >= maximum:
                skipped.append({'task': tid, 'reason': 'max lanes'})
                continue
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', tid):
                raise ValueError('unsafe task id')
            evidence = record['evidence_dirs'][0] + '/' + tid + '/verification.json'
            _file(root, evidence)
            selection, _, declared_files, _ = _selection(context, raw, tid, input_only=True)
            paths = (set(record['verification']['paths']) |
                     {p for i in record['inputs'] for p in i['paths']} | declared_files)
            job = {'task': tid, 'commands': record['verification']['commands'],
                   'cwd': 'temporary clean execution copy', 'evidence': evidence,
                   'lane': lane['id'], 'selection_digest': _digest(selection), **record,
                   'hashes': {p: _hash(root, p) for p in sorted(paths)}}
            jobs.append(job)
            group.append(job)
        if group:
            groups.append((lane['mode'], group))
    result = {'revision': context['revision'], 'tasks': jobs, 'skipped': skipped,
              'state_committed': False, 'results': []}
    if not execute or not jobs:
        return result
    source, default_timeout = _verification_source(root)
    timeout = default_timeout if timeout is None else timeout
    before = _working_tree(root)
    for job in jobs:
        current = next(t for t in _project(root, 'context', '--json')['tasks'] if t['id'] == job['task'])
        if current['status'] in ('todo', 'review'):
            _event(root, {'action': 'start_task'}, job['task'])
    # Only workers run in processes; all record actions stay in this locked writer.
    with ProcessPoolExecutor(max_workers=maximum, mp_context=multiprocessing.get_context('fork')) as pool:
        pending = []
        for mode, group in groups:
            if mode == 'serial':
                for future in pending:
                    result['results'].extend(future.result())
                pending = []
                result['results'].extend(pool.submit(_verification_lane, group, source, timeout).result())
            else:
                pending.append(pool.submit(_verification_lane, group, source, timeout))
        for future in pending:
            result['results'].extend(future.result())
    try:
        changed = _working_tree(root) != before
    except (ValueError, OSError):
        changed = True  # A newly unsafe path is also a working-tree change.
    by_id = {j['task']: j for j in jobs}
    for report in result['results']:
        job = by_id[report['task']]
        if changed:
            report.update(passed=False, error='main working tree changed')
        if report.get('passed'):
            try:
                current_context = _project(root, 'context', '--json')
                current_raw = _read(root / '.project/state.json')
                current_selection, _, _, _ = _selection(current_context, current_raw, report['task'], input_only=True)
                if _digest(current_selection) != job['selection_digest']:
                    raise ValueError('verification task or inputs changed during execution')
            except (ValueError, OSError, RuntimeError) as exc:
                report.update(passed=False, error=str(exc))
        evidence = job['evidence']
        report.update(evidence=evidence, verification=job['verification'], inputs=job['inputs'])
        proof = {k: v for k, v in report.items() if k != 'done'}
        _write(_file(root, evidence), proof)
        if report.get('passed'):
            try:
                task = next(t for t in raw['tasks'] if t['id'] == report['task'])
                _event(root, {'action': 'submit_evidence', 'items': [
                    {'path': evidence, 'criterion': i, 'note': 'Recorded commands passed in a clean execution copy; inputs pinned by SHA-256.',
                     'reviewer': 'orvant-deterministic-verification'} for i in range(len(task['acceptance']))]}, report['task'])
                _event(root, {'action': 'complete_task'}, report['task'])
                report['done'] = True
            except (ValueError, OSError, RuntimeError) as exc:
                report['error'] = str(exc)
    result['state_committed'] = True
    return result


def _refresh_graph(project, execution, *, skipped=False, staging=None):
    if skipped or not (project / ".project/scripts/graph.py").is_file():
        return {"skipped": True, "applied": False, "changed_modules": 0, "unresolved": 0}
    raw = _read(project / ".project/state.json")
    with (contextlib.nullcontext(staging) if staging else
          tempfile.TemporaryDirectory(prefix="orvant-code-graph-")) as temporary:
        # Scan execution sources with the authoritative graph/history, without
        # writing managed records into the clean execution repository.
        snapshot = Path(temporary) / "execution"
        _git(execution, "clone", "--no-hardlinks", "--", str(execution), str(snapshot))
        managed = snapshot / ".project"
        if managed.is_symlink():
            raise ValueError("symbolic managed execution record forbidden")
        if managed.exists():
            shutil.rmtree(managed)  # Only the disposable git copy, never the execution repository.
        shutil.copytree(project / ".project/scripts", snapshot / ".project/scripts", symlinks=True)
        _write(snapshot / ".project/state.json", raw)
        path = Path(temporary) / "graph.json"
        report = _project(snapshot, "graph", "--out", path)
        event = _read(path)
        operations = event["operations"]
        old_modules = {o["id"] for o in raw["objects"] if o["type"] == "code_module"}
        changed = {op["object"]["id"] for op in operations
                   if op["op"] in ("add_object", "replace_object") and op["object"]["type"] == "code_module"}
        changed.update(op["object_id"] for op in operations
                       if op["op"] == "remove_object" and op["object_id"] in old_modules)
        target = snapshot if staging else project
        if operations:
            preview = _project(target, "preview", "--event", path, "--expected-revision", raw["revision"])
            _project(target, "apply", "--event", path, "--expected-revision", raw["revision"],
                     "--preview-digest", preview["preview_digest"])
    if staging:
        return snapshot, event, raw["revision"], {"skipped": False, "applied": bool(operations),
            "changed_modules": len(changed), "unresolved": len(report["unresolved"]), "modules": report["modules"]}
    return {"skipped": False, "applied": bool(operations), "changed_modules": len(changed),
            "unresolved": len(report["unresolved"]), "modules": report["modules"]}


def prepare(session, config_path, *, graph=True, conflict_override=None):
    if conflict_override is not None and not conflict_override.strip():
        raise ValueError("çakışmayı kabul etme gerekçesi boş olamaz")
    with tempfile.TemporaryDirectory(prefix="orvant-prepare-graph-") as staging:
        return _prepare(session, config_path, graph=graph, staging=staging, conflict_override=conflict_override)


def _prepare(session, config_path, *, graph, staging, conflict_override):
    session = _root(session)
    config = _read(config_path)
    if set(config) != {"version", "project", "execution_repo", "policy", "tasks"} or config["version"] != 1:
        raise ValueError("unsupported project execution configuration")
    project, execution = _root(config["project"]), _root(config["execution_repo"])
    roots = (session, project, execution)
    if any(a == b or a.is_relative_to(b) or b.is_relative_to(a)
           for index, a in enumerate(roots) for b in roots[index + 1:]):
        raise ValueError("session, project and execution repository must be separate siblings")
    policy = config["policy"]
    executor = policy.get("executor")
    if executor is None:
        raise ValueError("explicit executor required: codex or claude")
    if executor not in ("codex", "claude"):
        raise ValueError(f"unknown executor: {executor}; expected codex or claude")
    fields = {"model", "effort", "codex", "max_worker_runs", "token_per_attempt",
              "worker_timeout", "command_timeout", "max_context_bytes"}
    if (set(policy) - {"executor", "codex", "claude"} != fields - {"codex"}
            or (executor == "codex" and "codex" not in policy)) or not policy["model"] or policy["effort"] not in ("low", "medium", "high", "xhigh", "max"):
        raise ValueError("explicit model, effort and bounded policy required")
    for name in fields - {"model", "effort", "codex"}:
        if type(policy[name]) is not int or policy[name] <= 0:
            raise ValueError(f"positive integer policy required: {name}")
    if executor == "codex" and (not Path(policy["codex"]).is_file() or not os.access(policy["codex"], os.X_OK)):
        raise ValueError("configured Codex executable unavailable")
    if executor == "claude":
        from . import ayarlar
        binary = shutil.which(policy.get("claude") or ayarlar.claude_ikili())
        if not binary or not Path(binary).is_file() or not os.access(binary, os.X_OK):
            raise ValueError("configured Claude executable unavailable")
        policy["claude"] = str(Path(binary).resolve())
    _git(execution, "var", "GIT_AUTHOR_IDENT")
    _git(execution, "var", "GIT_COMMITTER_IDENT")
    if _git(execution, "rev-parse", "--show-toplevel").decode().strip() != str(execution):
        raise ValueError("execution root must be a git root")
    if _git(execution, "branch", "--show-current").decode().strip() != "main":
        raise ValueError("execution repository must be on main")
    if _git(execution, "--no-optional-locks", "status", "--porcelain", "--untracked-files=all").strip():
        raise ValueError("execution repository must be clean")
    if any((session / name).exists() for name in (BINDING, STATE, "plan", "karsilama")):
        raise ValueError("existing engine session cannot be overwritten; prepare a new session")
    on_kontrol = _on_kontrol(execution, config)
    refreshed = _refresh_graph(project, execution, skipped=not graph, staging=staging)
    graph_event = None
    inspection = project
    if isinstance(refreshed, tuple):
        inspection, graph_event, original_revision, graph_report = refreshed
    else:
        graph_report = refreshed
    context = _project(inspection, "context", "--json")
    raw = _read(inspection / ".project/state.json")
    if raw["revision"] != context["revision"]:
        raise ValueError("project changed while preparing; retry")
    if not config["tasks"] or any(not t.get("id") for t in config["tasks"]):
        report = suggestions(project)
        candidate = next((lane["tasks"][0] for lane in report["lanes"] if lane["tasks"]), None)
        raise ValueError(f"explicit task id required; suggested task: {candidate or 'no ready task'}; not automatically selected")
    ids = [t.get("id") for t in config["tasks"]]
    if not ids or len(set(ids)) != len(ids) or any(not isinstance(i, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", i) for i in ids):
        raise ValueError("nonempty unique safe task ids required")
    selected_outputs = set()
    for item in config["tasks"]:
        selected_outputs.update(_selection(context, raw, item["id"])[3])
    from .butce import butce_tabani, gorev_envanteri
    from .koordinasyon import cakismalari_bul, worktree_kesisimleri
    records, plan_tasks, ownership, prepare_issues = {}, [], set(), []
    gorev_koordinasyonu = {}
    tum_cakismalar = []
    tum_worktree = []
    for item in config["tasks"]:
        if set(item) != {"id", "writable", "checks", "oracle", "setup", "report"}:
            raise ValueError("task requires exact writable files, checks, oracle, setup and report")
        shape, computed, files, outputs = _selection(context, raw, item["id"])
        if computed["effective_status"] not in ("todo", "blocked"):
            raise ValueError(f"task must be unstarted: {item['id']}")
        unresolved = [d for d in computed["effective_depends_on"]
                      if next(t for t in context["tasks"] if t["id"] == d)["effective_status"] != "done"]
        if set(unresolved) - set(ids):
            raise ValueError(f"unselected unfinished producers: {unresolved}")
        writable = item["writable"]
        if not writable or len(set(writable)) != len(writable) or ownership.intersection(writable):
            raise ValueError("tasks need nonoverlapping exact writable files")
        if item["report"] not in outputs or item["report"] in writable:
            raise ValueError("controller report must be a declared output outside worker writable files")
        for path in [*writable, item["report"]]:
            _file(project, path)
        ownership.update(writable)
        checks = item["checks"]
        if ({c.get("criterion") for c in checks} != set(range(len(shape["definition"]["acceptance"])))
                or len(checks) != len(shape["definition"]["acceptance"])):
            raise ValueError("every project acceptance criterion must have one native check")
        native_checks = []
        for check in checks:
            if set(check) != {"criterion", "command", "review"} or bool(check["command"]) == bool(check["review"]):
                raise ValueError("each check needs either a command or human review rubric")
            number = check["criterion"]
            native_checks.append({"id": f"A-{number}", "sozlesme_kabul_id": None,
                                  "tur": "komut" if check["command"] else "insan_incelemesi",
                                  "komut": check["command"], "rubrik": check["review"],
                                  "beklenen": shape["definition"]["acceptance"][number]})
        oracle = item["oracle"]
        if set(oracle) != {"command", "protected_files", "description"} or not oracle["command"] or not oracle["protected_files"]:
            raise ValueError("independently authored command oracle and protected files required")
        if not isinstance(item["setup"], list) or any(not isinstance(c, str) or not c.strip() for c in item["setup"]):
            raise ValueError("setup must contain explicit command strings")
        protected = set(oracle["protected_files"])
        if protected.intersection(writable):
            raise ValueError("worker cannot edit its independent oracle")
        pins = {path: _hash(project, path) for path in files - selected_outputs}
        pins.update({path: _hash(project, path) for path in writable})
        input_files = _selection(context, raw, item["id"], input_only=True)[2]
        if input_files.intersection(writable):
            raise ValueError("mutable code cannot be its own frozen task input")
        execution_inputs = sorted(path for path in input_files
                                  if path in selected_outputs or _git(project, "ls-files", "-z", "--", path))
        for path in execution_inputs:
            if path not in selected_outputs and _hash(execution, path) != _hash(project, path):
                raise ValueError(f"execution input differs from authoritative project: {path}")
        oracle_pins = {path: _hash(project, path) for path in protected}
        if any(value is None for value in oracle_pins.values()):
            raise ValueError("oracle file missing")
        for path, sha in {**{p: pins[p] for p in writable}, **oracle_pins}.items():
            if _hash(execution, path) != sha:
                raise ValueError(f"execution clone does not match project: {path}")
        # Start preview gives the runtime's actual input closure, not guessed file dependencies.
        if computed["effective_status"] == "todo":
            with tempfile.TemporaryDirectory(prefix="orvant-prepare-") as temporary:
                ep = Path(temporary) / "start.json"
                _write(ep, {"action": "start_task", "actor": "agent", "reason": "Read-only input inspection", "task_id": item["id"]})
                preview = _project(inspection, "preview", "--event", ep, "--expected-revision", raw["revision"])
                after = next(t["after"] for t in preview["change"]["task_changes"] if t["task_id"] == item["id"])
                input_files = {f["path"] for f in after["run_snapshot"]["manifest"]["graph"]["files"]}
                if input_files.intersection(writable):
                    raise ValueError("mutable code cannot be its own frozen task input; bind a versioned contract as input and code as output/context")
        if len(json.dumps(shape, ensure_ascii=False).encode()) > policy["max_context_bytes"]:
            raise ValueError("selected ontology context exceeds approved context limit; narrow the task")
        plan_task = {"id": item["id"], "baslik": shape["definition"]["title"],
                     "amac": "\n".join(shape["definition"]["acceptance"]),
                     "bagimliliklar": unresolved, "yazilabilir": writable,
                     "kabul": native_checks, "yetki_istek_ids": [], "bekleyen_kararlar": [],
                     "butce": {"token": policy["token_per_attempt"],
                               "deneme": policy["max_worker_runs"]}, "durum": "hazir"}
        floor = butce_tabani(gorev=plan_task, envanter=gorev_envanteri(session, plan_task))
        if floor > policy["token_per_attempt"]:
            prepare_issues.append({"type": "budget_floor", "task": item["id"], "floor": floor,
                                   "suggested_token_per_attempt": floor})
        cakismalar = cakismalari_bul(project, item["id"], writable)
        worktree = worktree_kesisimleri(project, writable)
        tum_cakismalar.extend(cakismalar)
        tum_worktree.extend(worktree)
        gorev_koordinasyonu[item["id"]] = (cakismalar, worktree)
        records[item["id"]] = {"selection": shape, "pins": pins, "oracle_pins": oracle_pins,
                               "calculated_budget_floor": floor,
                               "execution_inputs": execution_inputs, "config": item, "dependencies": unresolved,
                               "cakismalar": cakismalar, "worktree_kesisimleri": worktree}
        plan_tasks.append(plan_task)
    plan = {"surum": 1, "sozlesme_revizyon": raw["revision"],
            "depo": {"yol": str(execution), "gerekce": "Approved ontology task projection; not a new S1 intake"},
            "gorevler": plan_tasks, "yetki_istekleri": [], "kapsanmayan_kabul": []}
    from .mimar.dogrulama import dogrula, durumlari_hesapla
    dogrula(plan, {"revizyon": raw["revision"], "kabul_olcutleri": []}, [])
    durumlari_hesapla(plan)
    runtime = {}
    for name in RUNTIME:
        path = project / ".project/scripts" / name
        if name in ("graph.py", "derive.py") and not path.exists():
            continue
        runtime["scripts/" + name] = hashlib.sha256(path.read_bytes()).hexdigest()
    for name in ("integration.md", "source.json", "LICENSE"):
        path = project / ".project" / name
        if path.exists():
            runtime[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    engine = {name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest() for name in ENGINE_FILES}
    contract = {"version": 1, "session": str(session), "project": str(project), "execution_repo": str(execution),
                "project_id": raw["project"]["id"], "policy": policy, "tasks": records,
                "runtime": runtime, "engine": engine, "plan": _plan_contract(plan),
                "on_kontrol": on_kontrol,
                "prepare_issues": prepare_issues,
                "base_commit": _git(execution, "rev-parse", "HEAD").decode().strip(),
                "cakismalar": tum_cakismalar, "worktree_kesisimleri": tum_worktree,
                "cakisma_gecersiz_kilma_gerekcesi": conflict_override}
    binding = {"contract": contract, "digest": _digest(contract)}
    with _lock(session):
        if any((session / name).exists() for name in (BINDING, STATE, "plan", "karsilama")):
            raise ValueError("existing engine session cannot be overwritten; prepare a new session")
        if graph_event and graph_event["operations"]:
            path = Path(staging) / "validated-graph.json"
            _write(path, graph_event)
            preview = _project(project, "preview", "--event", path, "--expected-revision", original_revision)
            _project(project, "apply", "--event", path, "--expected-revision", original_revision,
                     "--preview-digest", preview["preview_digest"])
        # Publish the fail-closed marker first. An interrupted prepare must never
        # leave an apparently ordinary, unguarded native plan behind.
        _write(session / BINDING, binding)
        (session / "plan").mkdir()
        _write(session / "plan/plan.json", plan)
        _write(session / "plan/kararlar.json", [])
        _write(session / "plan/koordinasyon.json", {"gorevler": {k: {"cakismalar": v[0], "worktree_kesisimleri": v[1]} for k, v in gorev_koordinasyonu.items()}, "cakisma_gecersiz_kilma_gerekcesi": conflict_override})
        _write(session / STATE, {"authorized": False, "runs": [], "tasks": {}, "published_files": {},
                                 "execution_head": contract["base_commit"], "graph": graph_report})
    return status(session)


def guard(session, task_id=None, *, authorized=True):
    binding = load(session)
    if binding is None:
        return None
    contract = binding["contract"]
    if _digest(contract) != binding["digest"]:
        raise ValueError("handoff contract digest changed")
    if _root(session) != Path(contract["session"]):
        raise ValueError("handoff belongs to a different session")
    state = _read(Path(session) / STATE)
    if authorized and ((Path(session) / PAUSE).exists() or not state["authorized"]
                       or state.get("approval_digest") != binding["digest"]):
        raise ValueError("project execution is paused or not digest-approved")
    plan = _read(Path(session) / "plan/plan.json")
    if _plan_contract(plan) != contract["plan"]:
        raise ValueError("native plan scope/checks/budget changed; prepare and approve again")
    root = Path(contract["project"])
    for name, digest in contract["runtime"].items():
        if hashlib.sha256((root / ".project" / name).read_bytes()).hexdigest() != digest:
            raise ValueError("managed project runtime changed")
    for name, digest in contract["engine"].items():
        if hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest() != digest:
            raise ValueError("native engine changed; prepare and approve again")
    context = _project(root, "context", "--json")
    raw = _read(root / ".project/state.json")
    if raw["revision"] != context["revision"]:
        raise ValueError("project changed while checking handoff")
    pending_publications = {item["path"]: item["after"] for progress in state["tasks"].values()
                            if not progress.get("synced") for item in progress.get("publication", [])}
    keys = [task_id] if task_id else list(contract["tasks"])
    for key in keys:
        record = contract["tasks"][key]
        shape, computed, _, _ = _selection(context, raw, key)
        if shape != record["selection"]:
            raise ValueError(f"ontology/task/decision changed: {key}")
        if computed["effective_status"] in ("needs_review", "cancelled"):
            raise ValueError(f"project inputs stale: {key}: {computed['issues']}")
        for path, digest in record["pins"].items():
            expected = state["published_files"].get(path, digest)
            observed = _hash(root, path)
            if observed != expected and (path not in pending_publications or observed != pending_publications[path]):
                raise ValueError(f"source changed since handoff: {path}")
        for path in record["execution_inputs"]:
            if path in record["pins"] or path in state["published_files"]:
                if _hash(contract["execution_repo"], path) != _hash(root, path):
                    raise ValueError(f"execution input differs from authoritative project: {path}")
        for path, digest in record["oracle_pins"].items():
            if _hash(root, path) != digest:
                raise ValueError(f"oracle source changed: {path}")
            if _hash(contract["execution_repo"], path) != digest:
                raise ValueError(f"execution oracle source changed: {path}")
    return binding


def status(session):
    binding = load(session)
    if binding is None:
        raise ValueError("not a project-bound engine session")
    issues = []
    try:
        guard(session, authorized=False)
    except (ValueError, OSError) as exc:
        issues.append(str(exc))
    state = _read(Path(session) / STATE)
    plan = _read(Path(session) / "plan/plan.json")
    active = state["authorized"] and not (Path(session) / PAUSE).exists()
    contract = binding["contract"]
    blockers = contract.get("on_kontrol", {}).get("engeller", [])
    issues.extend(blockers)
    issues.extend(contract.get("prepare_issues", []))
    return {"digest": binding["digest"], "authorized": active, "issues": issues,
            "worker_runs": len(state["runs"]), "max_worker_runs": contract["policy"]["max_worker_runs"],
            "policy": contract["policy"], "graph": state.get("graph"), "scratch_write_scope": str(Path(session) / "scratch"),
            "budget_unit": "worker CLI runs and measured executor tokens; not HTTP requests or a dollar cap",
            "scope": {key: value["config"] for key, value in contract["tasks"].items()},
            "on_kontrol": contract.get("on_kontrol"),
            "yarim_baslatma": state.get("yarim_baslatma"),
            "tasks": [{"id": t["id"], "engine": t["durum"],
                       "calculated_budget_floor": contract["tasks"][t["id"]]["calculated_budget_floor"],
                       "cakismalar": contract["tasks"][t["id"]].get("cakismalar", []),
                       "worktree_kesisimleri": contract["tasks"][t["id"]].get("worktree_kesisimleri", []),
                       "project_synced": bool(state["tasks"].get(t["id"], {}).get("synced"))}
                      for t in plan["gorevler"]],
            "ready_for_approval": not issues and not active and (not contract.get("cakismalar") or bool(contract.get("cakisma_gecersiz_kilma_gerekcesi"))),
            "cakismalar": contract.get("cakismalar", []),
            "worktree_kesisimleri": contract.get("worktree_kesisimleri", []),
            "cakisma_gecersiz_kilma_gerekcesi": contract.get("cakisma_gecersiz_kilma_gerekcesi"),
            "ready_for_approval_nedeni": ("aktif koordinasyon çakışması" if contract.get("cakismalar") and not contract.get("cakisma_gecersiz_kilma_gerekcesi") else None),
            "intake_model_calls": 0, "planning_model_calls": 0, "oracle_mode": "pinned-command"}


def approve(session, digest, reason):
    with _lock(session):
        binding = guard(session, authorized=False)
        if binding["contract"].get("on_kontrol", {}).get("engeller"):
            raise ValueError("ön kontrol engeli giderilmeden plan onaylanamaz: " +
                             "; ".join(binding["contract"]["on_kontrol"]["engeller"]))
        if binding["contract"].get("prepare_issues"):
            raise ValueError("prepare issues must be resolved; run 'proje hazirla' with an adequate policy")
        if (binding["contract"].get("cakismalar") and
                not binding["contract"].get("cakisma_gecersiz_kilma_gerekcesi")):
            raise ValueError("aktif koordinasyon çakışması giderilmeden veya gerekçeli geçersiz kılma olmadan plan onaylanamaz")
        if binding["digest"] != digest or not reason.strip():
            raise ValueError("exact handoff digest and approval reason required")
        state = _read(Path(session) / STATE)
        state.update(authorized=True, approval_digest=digest, approval_reason=reason)
        _write(Path(session) / STATE, state)
        (Path(session) / PAUSE).unlink(missing_ok=True)
    return status(session)


def pause(session, reason):
    if not reason.strip():
        raise ValueError("pause reason required")
    # Do not wait for the run lock: revocation must be observable before merge.
    _write(Path(session) / PAUSE, {"reason": reason})
    from .yurutme import Yurutme
    cancellations = []
    for task in _read(Path(session) / "plan/plan.json")["gorevler"]:
        if task["durum"] == "kosuyor":
            try:
                cancellations.append(Yurutme(session).iptal(task["id"], reason))
            except ValueError as exc:
                cancellations.append({"task": task["id"], "note": str(exc)})
    return {**status(session), "cancellations": cancellations}


def worker_settings(session, task_id):
    binding = guard(session, task_id)
    if binding:
        if not _EXECUTING.get():
            raise ValueError("paid project workers require orvant proje surdur --execute")
        check_execution_base(session)
        current = next(t for t in _project(binding["contract"]["project"], "context", "--json")["tasks"]
                       if t["id"] == task_id)
        if current["effective_status"] != "doing":
            raise ValueError("start the ontology task through orvant proje surdur before executing")
    return binding["contract"]["policy"] if binding else None

def check_execution_base(session):
    binding = load(session)
    if binding:
        state = _read(Path(session) / STATE)
        head = _git(binding["contract"]["execution_repo"], "rev-parse", "HEAD").decode().strip()
        if head != state["execution_head"]:
            raise ValueError("execution base changed before independent acceptance")


def _committed_file(execution, commit, path):
    mode = _git(execution, "ls-tree", commit, "--", path).split(b" ", 1)[0]
    if mode not in (b"100644", b"100755"):
        raise ValueError(f"accepted output is missing or not a regular file: {path}")
    return _git(execution, "show", f"{commit}:{path}"), (0o755 if mode == b"100755" else 0o644)



def preflight_call(session, task_id):
    binding = guard(session, task_id)
    if not binding:
        return None
    policy = binding["contract"]["policy"]
    from . import ayarlar
    from .butce import butce_tabani
    if ayarlar.model("isci") != policy["model"]:
        raise ValueError("resolved worker model differs from approved model")
    from .butce import gorev_envanteri
    task = next(t for t in _read(Path(session) / "plan/plan.json")["gorevler"] if t["id"] == task_id)
    floor = butce_tabani(gorev=task, envanter=gorev_envanteri(session, task))
    if floor > policy["token_per_attempt"]:
        raise ValueError(f"runtime token floor {floor} exceeds approved per-attempt limit "
                         f"{policy['token_per_attempt']}; 'hazirla'yı yeniden çalıştırın")
    state = _read(Path(session) / STATE)
    if len(state["runs"]) >= policy["max_worker_runs"]:
        raise ValueError("approved worker-run budget exhausted")
    return binding


def reserve_call(session, task_id):
    binding = preflight_call(session, task_id)
    if not binding:
        return
    policy = binding["contract"]["policy"]
    state = _read(Path(session) / STATE)
    state["runs"].append({"task": task_id, "reserved_tokens": policy["token_per_attempt"]})
    _write(Path(session) / STATE, state)


def model_result(session, task_id, goal):
    binding = load(session)
    if not binding:
        return []
    state = _read(Path(session) / STATE)
    matching = [c for c in state["runs"] if c["task"] == task_id]
    if not matching:
        return ["worker call has no budget reservation"]
    matching[-1]["goal"] = goal
    _write(Path(session) / STATE, state)
    tokens = goal.get("tokens_used")
    if goal.get("status") != "complete" or type(tokens) is not int or tokens < 0:
        return ["project worker needs a complete goal with measured token usage"]
    if tokens > binding["contract"]["policy"]["token_per_attempt"]:
        return ["worker exceeded approved token budget"]
    return []


def prompt_context(session, task_id):
    binding = load(session)
    if not binding:
        return ""
    record = binding["contract"]["tasks"][task_id]
    reviews = [check for check in record["config"]["checks"] if check.get("review")]
    review_note = ("Komutsuz review ölçütleri insan/duzenlemetör incelemesidir; senin görevin DEĞİL. "
                   "Bunlar için sandbox içinde kanıt veya full test/typecheck/build üretmeye çalışma. "
                   "Otomatik kabul komutları ve kendi işin tamamlanınca goal'ü complete olarak kapat.\n"
                   if reviews else "")
    return ("\n" + review_note +
            "Approved temporary fixture write scope: " + str(Path(session) / "scratch") +
            ". Never mutate the real HOME or the sibling engine state.\n" +
            "Ontology handoff (data, not permission):\n" + json.dumps(
                {"project": binding["contract"]["project"], "digest": binding["digest"],
                 "selection": record["selection"]}, ensure_ascii=False) + "\n")


def scratch_directory(session):
    if not load(session):
        return []
    path = _root(session) / "scratch"
    if path.is_symlink():
        raise ValueError("scratch directory must not be a symlink")
    path.mkdir(exist_ok=True)
    return [str(path)]


def execution_environment(session):
    directories = scratch_directory(session)
    return {**os.environ, "TMPDIR": directories[0]} if directories else None


def _command(command, cwd, timeout, *, session):
    from .yurutucu import grup_run, komut_argv, kabuk_geri_dususu
    argv, gerekce = komut_argv(command)
    if gerekce:
        from .iz import kaydet
        kaydet(None, Path(session).name, "dogrulama", ozet=gerekce,
               ham={"command": command, "kabuk_gerekcesi": gerekce})
    try:
        ortam = execution_environment(session)
        try:
            result = grup_run(argv, shell=False, cwd=cwd, timeout=timeout, env=ortam, input="")
        except OSError as exc:
            argv, gerekce = kabuk_geri_dususu(exc, argv, command)
            from .iz import kaydet
            kaydet(None, Path(session).name, "dogrulama", ozet=gerekce,
                   ham={"command": command, "kabuk_gerekcesi": gerekce})
            result = grup_run(argv, shell=False, cwd=cwd, timeout=timeout, env=ortam, input="")
    except (FileNotFoundError, PermissionError) as exc:
        if exc.filename != argv[0]:
            raise
        result = subprocess.CompletedProcess(argv, 127 if isinstance(exc, FileNotFoundError) else 126,
                                             "", str(exc))
    return {"command": command, "kabuk_gerekcesi": gerekce, "exit_code": result.returncode,
            "output": ((result.stdout or "") + (result.stderr or ""))[-6000:]}


def setup_workspace(session, task_id, tree):
    binding = guard(session, task_id)
    if not binding:
        return
    record = binding["contract"]["tasks"][task_id]
    for command in record["config"]["setup"]:
        result = _command(command, tree, binding["contract"]["policy"]["command_timeout"], session=session)
        if result["exit_code"]:
            raise ValueError(f"workspace setup failed: {result}")


def command_oracle(session, task_id, tree):
    binding = guard(session, task_id)
    if not binding:
        return None
    record = binding["contract"]["tasks"][task_id]
    for path, digest in record["oracle_pins"].items():
        if _hash(tree, path) != digest:
            return {"kehanet": "project-pinned-command", "gecti": False,
                    "hata": f"independent oracle changed: {path}"}
    result = _command(record["config"]["oracle"]["command"], tree,
                      binding["contract"]["policy"]["command_timeout"], session=session)
    guard(session, task_id)
    return {"kehanet": "project-pinned-command:" + binding["digest"],
            "gecti": result["exit_code"] == 0, "kontroller": [result],
            "hata": None if result["exit_code"] == 0 else "independent command oracle failed"}


def sync(session, task_id):
    binding = guard(session, task_id)
    contract = binding["contract"]
    record = contract["tasks"][task_id]
    root, execution = Path(contract["project"]), Path(contract["execution_repo"])
    state = _read(Path(session) / STATE)
    if state["tasks"].get(task_id, {}).get("synced"):
        return {"task": task_id, "synced": True, "idempotent": True}
    plan = _read(Path(session) / "plan/plan.json")
    native = next(t for t in plan["gorevler"] if t["id"] == task_id)
    if native["durum"] != "kabul":
        raise ValueError("native independent gate/review has not accepted this task")
    receipts = list((Path(session) / "yurutme/makbuzlar").glob(f"{task_id}-*.json"))
    accepted = [(p, _read(p)) for p in receipts if _read(p).get("karar") == "kabul"]
    if not accepted:
        raise ValueError("accepted native receipt missing")
    receipt_path, receipt = max(accepted, key=lambda row: row[0].stat().st_mtime_ns)
    if receipt.get("hatalar") or not (receipt.get("kehanet_sonucu") or {}).get("gecti"):
        raise ValueError("receipt lacks successful independent oracle evidence")
    head = _git(execution, "rev-parse", "HEAD").decode().strip()
    if receipt.get("execution_commit") != head:
        raise ValueError("execution head does not match the accepted receipt")
    if receipt.get("validated_tree") != _git(execution, "rev-parse", f"{head}^{{tree}}").decode().strip():
        raise ValueError("execution tree does not match independent acceptance")
    progress = state["tasks"].get(task_id, {})
    if "publication" not in progress:
        changed = _git(execution, "diff", "--no-renames", "--name-only", "-z",
                       state["execution_head"], head).decode().split("\0")
        changed = [p for p in changed if p]
        if set(changed) - set(record["config"]["writable"]):
            raise ValueError("execution commit changed files outside approved task scope")
        publication = []
        for path in changed:
            content, _ = _committed_file(execution, head, path)
            publication.append({"path": path, "before": _hash(root, path),
                                "after": hashlib.sha256(content).hexdigest()})
        progress = {"publication": publication, "head": head, "receipt": str(receipt_path)}
        state["tasks"][task_id] = progress
        _write(Path(session) / STATE, state)
    if head != progress["head"]:
        raise ValueError("execution head moved during publication")
    # Two-phase idempotent publication; every preimage is checked before any write.
    for item in progress["publication"]:
        if _hash(root, item["path"]) not in (item["before"], item["after"]):
            raise ValueError(f"concurrent product edit: {item['path']}")
        content, _ = _committed_file(execution, head, item["path"])
        if hashlib.sha256(content).hexdigest() != item["after"]:
            raise ValueError("accepted commit output changed during publication")
    for item in progress["publication"]:
        guard(session, task_id)
        if _git(execution, "rev-parse", "HEAD").decode().strip() != head:
            raise ValueError("execution head moved during publication")
        content, mode = _committed_file(execution, head, item["path"])
        target = _file(root, item["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        if _hash(root, item["path"]) != item["after"]:
            with tempfile.NamedTemporaryFile("wb", dir=target.parent, delete=False) as stream:
                stream.write(content)
                temporary = stream.name
            os.chmod(temporary, mode)
            os.replace(temporary, target)
        state["published_files"][item["path"]] = item["after"]
    state["execution_head"] = head
    guard(session, task_id)
    _write(Path(session) / STATE, state)
    report = {"kind": "native-engine-acceptance", "task": task_id, "handoff_digest": binding["digest"],
              "native_receipt": receipt, "execution_commit": head, "publication": progress["publication"],
              "worker_runs": [c for c in state["runs"] if c["task"] == task_id]}
    _write(_file(root, record["config"]["report"]), report)
    current = next(t for t in _project(root, "context", "--json")["tasks"] if t["id"] == task_id)
    if current["effective_status"] == "done":
        state["tasks"][task_id]["synced"] = True
        _write(Path(session) / STATE, state)
        return {"task": task_id, "synced": True, "idempotent": True}
    items = []
    for number in range(len(record["selection"]["definition"]["acceptance"])):
        for path in [record["config"]["report"], *(i["path"] for i in progress["publication"])]:
            items.append({"path": path, "criterion": number,
                          "note": "Native command checks, pinned independent oracle and required review accepted; exact execution outputs published with preimage checks.",
                          "reviewer": "orvant-native-gate"})
    _event(root, {"action": "submit_evidence", "items": items}, task_id)
    guard(session, task_id)
    _event(root, {"action": "complete_task"}, task_id)
    state["tasks"][task_id]["synced"] = True
    _write(Path(session) / STATE, state)
    return {"task": task_id, "synced": True, "commit": head}


@contextlib.contextmanager
def _worker_environment(session, policy):
    changes = {"ORVANT_MODEL_ISCI": policy["model"],
               "ORVANT_YURUTUCU": policy["executor"],
               "ORVANT_IZ_DIZINI": str(Path(session) / "worker-calibration")}
    if changes["ORVANT_YURUTUCU"] == "codex":
        changes["ORVANT_CODEX"] = policy["codex"]
    else:
        changes["ORVANT_CLAUDE_MODEL"] = policy["model"]
        changes["ORVANT_CLAUDE"] = policy["claude"]
    old = {k: os.environ.get(k) for k in changes}
    execution_token = _EXECUTING.set(True)
    os.environ.update(changes)
    try:
        yield
    finally:
        _EXECUTING.reset(execution_token)
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _started_task_rollback(session, root, task_id, before):
    """Undo our lone start event, or record an explicit manual recovery state."""
    current = _read(root / ".project/state.json")
    previous = json.loads(before)
    task = next((t for t in current["tasks"] if t["id"] == task_id), None)
    if (current.get("revision") == previous.get("revision", -1) + 1
            and task is not None and task.get("status") == "doing"):
        path = root / ".project/state.json"
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as stream:
            stream.write(before)
            stream.flush()
            os.fsync(stream.fileno())
            temporary = stream.name
        os.replace(temporary, path)
        return True
    state = _read(Path(session) / STATE)
    state["yarim_baslatma"] = {
        "task": task_id,
        "recovery": (f"Proje kaydı eşzamanlı değişti; {task_id} görevini güvenli biçimde todo durumuna "
                     "döndürüp 'proje hazirla'yı yeni bir oturumda yeniden çalıştırın.")}
    _write(Path(session) / STATE, state)
    return False


def run(session, *, execute=False, worker=None, goals_db=None):
    from .yurutme import Yurutme
    with _lock(session):
        binding = guard(session, authorized=execute)
        if not execute:
            return status(session)
        contract = binding["contract"]
        policy = contract["policy"]
        results = []
        with _worker_environment(session, policy):
            class ProjectExecution(Yurutme):
                def _isci_sonrasi(self, gorev, deneme, kosu, goal):
                    if policy["executor"] == "claude":
                        goal.clear()
                        goal.update(kosu.get("project_goal") or {"status": "measurement_missing", "tokens_used": None})
                        kosu["thread_id"] = kosu.get("session_id")
                    super()._isci_sonrasi(gorev, deneme, kosu, goal)

            executor = ProjectExecution(session, yurutucu=worker, goals_db=goals_db,
                               isci_zaman_asimi=policy["worker_timeout"],
                               komut_zaman_asimi=policy["command_timeout"])
            while True:
                guard(session)
                plan = executor._plan()
                pending_sync = next((t for t in plan["gorevler"] if t["durum"] == "kabul"
                                     and not _read(Path(session) / STATE)["tasks"].get(t["id"], {}).get("synced")), None)
                if pending_sync:
                    results.append(sync(session, pending_sync["id"]))
                    continue
                candidate = next((t for t in plan["gorevler"] if t["durum"] == "hazir"), None)
                if candidate is None:
                    break
                state = _read(Path(session) / STATE)
                if len(state["runs"]) >= policy["max_worker_runs"]:
                    break
                root = Path(contract["project"])
                current = next(t for t in _project(root, "context", "--json")["tasks"] if t["id"] == candidate["id"])
                if current["effective_status"] not in ("todo", "doing"):
                    raise ValueError("ontology dependencies are not ready for execution")
                # The project record must remain untouched when any paid-run
                # prerequisite rejects this attempt.
                preflight_call(session, candidate["id"])
                started = False
                before = (root / ".project/state.json").read_bytes()
                if current["status"] == "todo":
                    _event(root, {"action": "start_task"}, candidate["id"])
                    started = True
                try:
                    result = executor.yurut(gorev_id=candidate["id"], en_fazla=1)
                except Exception:
                    if started:
                        _started_task_rollback(session, root, candidate["id"], before)
                    raise
                results.extend(result)
                if not result or result[-1]["durum"] != "kabul":
                    break  # No paid replanning or self-certified review after failure.
        return {"results": results, "status": status(session)}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="orvant proje")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("hazirla", help="Project → native plan, without model calls")
    prepare_parser.add_argument("session")
    prepare_parser.add_argument("--config", required=True)
    prepare_parser.add_argument("--graf-yok", action="store_true")
    prepare_parser.add_argument("--cakismayi-kabul-et", metavar="GEREKCE")
    proposals = commands.add_parser("oneriler", help="Read-only derived work and parallel lanes")
    proposals.add_argument("root")
    proposals.add_argument("--max", type=int)
    proposals.add_argument("--json", action="store_true")
    verification = commands.add_parser("dogrula", help="Run recorded verification without models")
    verification.add_argument("root")
    verification.add_argument("--gorev", nargs="+", action="extend")
    verification.add_argument("--max", type=int)
    verification.add_argument("--calistir", action="store_true")
    verification.add_argument("--json", action="store_true")
    commands.add_parser("durum").add_argument("session")
    approval = commands.add_parser("onayla")
    approval.add_argument("session")
    approval.add_argument("--digest", required=True)
    approval.add_argument("--reason", required=True)
    runner = commands.add_parser("surdur")
    runner.add_argument("session")
    runner.add_argument("--execute", action="store_true", help="Explicitly permit approved paid worker calls")
    synchronizer = commands.add_parser("esitle")
    synchronizer.add_argument("session")
    synchronizer.add_argument("task")
    stopper = commands.add_parser("durdur")
    stopper.add_argument("session")
    stopper.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "hazirla":
            result = prepare(args.session, args.config, graph=not args.graf_yok, conflict_override=args.cakismayi_kabul_et)
        elif args.command == "oneriler":
            result = suggestions(args.root, args.max)
        elif args.command == "dogrula":
            result = verify(args.root, tasks=args.gorev, maximum=args.max, execute=args.calistir)
        elif args.command == "onayla":
            result = approve(args.session, args.digest, args.reason)
        elif args.command == "surdur":
            result = run(args.session, execute=args.execute)
        elif args.command == "esitle":
            with _lock(args.session):
                result = sync(args.session, args.task)
        elif args.command == "durdur":
            result = pause(args.session, args.reason)
        else:
            result = status(args.session)
        if args.command == "oneriler" and not args.json:
            print(f"Öneriler: {result['proposal_count']} (revision {result['revision']})")
            for category, count in result["categories"].items():
                print(f"  {category}: {count}")
            for lane in result["lanes"]:
                print(f"  {lane['id']} ({lane['mode']}): {', '.join(lane['tasks'])}")
            for lane in result["queued_lanes"]:
                print(f"  {lane['id']} bekliyor: {', '.join(lane['queued_tasks'] + lane['tasks'])}")
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, RuntimeError, KeyError, TypeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
