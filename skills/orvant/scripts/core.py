"""Project model checks and typed state transitions; Python 3.10+, stdlib only.

This module validates local records, not the truth of reviewer/actor statements.
Single-writer orchestration and atomic persistence belong to the CLI.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import stat
from pathlib import Path, PurePosixPath, PureWindowsPath


TASK_STATUSES = {"todo", "doing", "review", "done", "cancelled"}
DECISION_STATUSES = {"proposed", "accepted", "rejected", "superseded"}
ACTIONS = {
    "start_task": {"task_id"},
    "submit_evidence": {"task_id", "items"},
    "complete_task": {"task_id"},
    "reopen_task": {"task_id"},
    "propose_decision": {"decision"},
    "accept_decision": {"decision_id"},
    "reject_decision": {"decision_id"},
    "reconcile_task": {"task_id", "decision_ids"},
}
LEGACY_ACTIONS = frozenset(ACTIONS)
ACTIONS.update({"extend_model": {"objects", "relations", "tasks"},
                "revise_task": {"task_id", "definition"},
                "migrate_ontology": {"ontology", "objects", "relations", "bindings"},
                "mutate_graph": {"operations"}})
DEFINITION_FIELDS = {"title", "object_ids", "depends_on", "decision_ids", "acceptance"}
DOMAIN_TASK_FIELDS = {"input_ids", "output_ids", "input_snapshot", "generation", "review_reasons"}
OPTIONAL_DOMAIN_TASK_FIELDS = {"input_fields", "run_snapshot", "output_snapshot", "support_groups",
                               "support_snapshot", "acceptance_rules"}


def _ontology():
    # Lazy import preserves legacy runtimes and keeps the graph engine independent.
    import ontology
    return ontology


def _acceptance():
    import acceptance
    return acceptance


def _definition_fields(state):
    return DEFINITION_FIELDS | ({"input_ids", "output_ids"} if state["schema_version"] == 3 else set())


def _task_dependencies(state):
    """Explicit workflow prerequisites plus declared input producers."""
    result = {t["id"]: list(t["depends_on"]) for t in state["tasks"]}
    reasons = {t["id"]: [] for t in state["tasks"]}
    if state["schema_version"] != 3:
        return result, reasons
    producers = {}
    for task in state["tasks"]:
        for obj in task["output_ids"]:
            _require(obj not in producers, f"object {obj}: multiple producing tasks")
            producers[obj] = task["id"]
    for task in state["tasks"]:
        closure = _ontology().upstream(state, task["input_ids"])
        for obj in sorted(closure):
            producer = producers.get(obj)
            if producer:
                _require(producer != task["id"], f"task {task['id']}: consumes own output {obj}")
                if producer not in result[task["id"]]:
                    result[task["id"]].append(producer)
                reasons[task["id"]].append({"task_id": producer, "object_id": obj})
    _acyclic(result, "effective task dependencies")
    return result, reasons


def _input_snapshot(state, task, root):
    graph = _ontology().snapshot(state, task["input_ids"], root, _hash_file, task.get("input_fields"))
    dependencies, _ = _task_dependencies(state)
    tasks = {t["id"]: t for t in state["tasks"]}
    generations = {key: tasks[key]["generation"] for key in dependencies[task["id"]]}
    manifest = {"graph": graph, "producer_generations": generations,
                "contract": {"input_fields": task.get("input_fields", {}),
                             "acceptance_rules": task.get("acceptance_rules", []),
                             "support_groups": task.get("support_groups", [])}}
    return {"sha256": _manifest_hash(manifest), "manifest": manifest}


def _manifest_hash(manifest):
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _snapshot_changes(previous, current):
    old, new = previous["manifest"], current["manifest"]
    reasons = []
    for collection in ("objects", "relations", "object_types", "relation_types"):
        before = {item["id"]: item for item in old["graph"].get(collection, [])}
        after = {item["id"]: item for item in new["graph"].get(collection, [])}
        for key in sorted(before.keys() | after.keys()):
            if _manifest_hash(before.get(key)) != _manifest_hash(after.get(key)):
                reasons.append(f"domain {collection} changed: {key}")
    if old["graph"].get("files") != new["graph"].get("files"):
        old_files = {(f["object_id"], f["property"]): f for f in old["graph"].get("files", [])}
        new_files = {(f["object_id"], f["property"]): f for f in new["graph"].get("files", [])}
        for key in sorted(old_files.keys() | new_files.keys()):
            if old_files.get(key) != new_files.get(key):
                reasons.append(f"domain file changed: {key[0]}.{key[1]}")
    if old.get("producer_generations") != new.get("producer_generations"):
        reasons.append("input producer completion changed")
    return reasons or ["domain input bindings changed"]


def _output_snapshot(state, task, root):
    manifest = _ontology().snapshot(state, task["output_ids"], root, _hash_file, direct=True)
    # Alternative support witnesses have their own any/all acceptance contract.
    # Their loss must be evaluated there, not made unconditional output damage.
    witnesses = {rid for group in task.get("support_groups", []) for branch in group["branches"]
                 for rid in branch["relation_ids"]}
    manifest["relations"] = [r for r in manifest["relations"] if r["id"] not in witnesses]
    if task.get("acceptance_rules"):
        manifest["rule_observations"] = _acceptance().observe_rules(state, task["acceptance_rules"])
    return {"sha256": _manifest_hash(manifest), "manifest": manifest}


def _support_graph(state, ids, root, hash_file):
    graph = _ontology().snapshot(state, ids, root, hash_file)
    closure = _ontology().upstream(state, ids)
    graph["producer_generations"] = {t["id"]: t["generation"] for t in state["tasks"]
                                     if set(t["output_ids"]) & closure}
    return graph


def _producer_statuses(state, report):
    statuses = {t["id"]: t["effective_status"] for t in report["tasks"]}
    return {obj: "current" if statuses[t["id"]] == "done" else statuses[t["id"]]
            for t in state["tasks"] for obj in t.get("output_ids", [])}


def _refresh_supports(state, root):
    """Latch observed loss of an approved branch; never auto-substitute one."""
    if state["schema_version"] != 3:
        return []
    report = inspect_state(state, root)
    views = {t["id"]: t for t in report["tasks"]}
    effects = []
    seeds = set()
    for task in state["tasks"]:
        status = views[task["id"]].get("support_status")
        if status is None:
            continue
        task["support_snapshot"] = {"groups": copy.deepcopy(status["groups"])}
        if status["lost"]:
            effects.append({"task_id": task["id"], "lost": status["lost"], "current": status["current"]})
        if not status["current"] and task["status"] in {"doing", "review", "done"}:
            seeds.add(task["id"])
            _clear_acceptance(task, "approved support requirements lost; explicit new review required")
    dependencies, _ = _task_dependencies(state)
    pending = list(seeds)
    while pending:
        parent = pending.pop()
        for task in state["tasks"]:
            key = task["id"]
            if key not in seeds and parent in dependencies[key]:
                seeds.add(key)
                pending.append(key)
                if task["status"] in {"doing", "review", "done"}:
                    _clear_acceptance(task, f"support of input producer {parent} lost")
    return effects


def _require(ok, message):
    if not ok:
        raise ValueError(message)


def _shape(value, keys, label):
    _require(type(value) is dict, f"{label}: expected object")
    _require(set(value) == set(keys), f"{label}: unexpected or missing fields")


def _text(value, label, allow_empty=False):
    _require(type(value) is str and (allow_empty or bool(value.strip())),
             f"{label}: expected nonempty string")


def _integer(value, label, minimum=0):
    _require(type(value) is int and value >= minimum, f"{label}: invalid integer")


def _list(value, label):
    _require(type(value) is list, f"{label}: expected array")


def _strings(value, label, unique=False):
    _list(value, label)
    for item in value:
        _text(item, label)
    if unique:
        _require(len(value) == len(set(value)), f"{label}: duplicate reference")


def _json_value(value, label):
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        _require(math.isfinite(value), f"{label}: non-finite number")
        return
    if type(value) is list:
        for item in value:
            _json_value(item, label)
        return
    if type(value) is dict:
        for key, item in value.items():
            _text(key, label)
            _json_value(item, label)
        return
    raise ValueError(f"{label}: expected JSON value")


def _index(items, label):
    result = {}
    _list(items, label)
    for item in items:
        _require(type(item) is dict and "id" in item, f"{label}: missing id")
        _text(item["id"], f"{label}.id")
        _require(item["id"] not in result, f"{label}: duplicate id {item['id']}")
        result[item["id"]] = item
    return result


def _acyclic(graph, label):
    active, finished = set(), set()

    def visit(node):
        _require(node not in active, f"{label}: cycle at {node}")
        if node in finished:
            return
        active.add(node)
        for dependency in graph[node]:
            visit(dependency)
        active.remove(node)
        finished.add(node)

    try:
        for node in graph:
            visit(node)
    except RecursionError as exc:
        raise ValueError(f"{label}: graph exceeds supported nesting") from exc


def _relative_path(value):
    _text(value, "evidence.path")
    posix, windows = PurePosixPath(value), PureWindowsPath(value)
    _require(not posix.is_absolute() and not windows.is_absolute()
             and not windows.drive and "\\" not in value,
             "evidence.path: use a relative POSIX path")
    _require(".." not in posix.parts and ".project" not in {p.casefold() for p in posix.parts}
             and posix.parts and "\x00" not in value,
             "evidence.path: traversal or managed path forbidden")
    # Avoid Windows device names, alternate data streams and normalized aliases.
    reserved = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {
        prefix + digit for prefix in ("COM", "LPT") for digit in "123456789¹²³"}
    _require(all(not any(c in '<>:"|?*' or ord(c) < 32 for c in part)
                 and not part.endswith((".", " "))
                 and part.split(".")[0].upper() not in reserved for part in posix.parts),
             "evidence.path: use a portable filename (no Windows devices or aliases)")
    return posix


def _validate_data(state):
    _require(type(state) is dict, "state: expected object")
    _shape(state, {"schema_version", "revision", "project", "objects", "relations",
                   "tasks", "decisions", "history"} | ({"ontology"} if state.get("schema_version") == 3 else set()), "state")
    _require(type(state["schema_version"]) is int and state["schema_version"] in {1, 2, 3},
             "unsupported schema_version")
    _integer(state["revision"], "revision")
    project = state["project"]
    _shape(project, {"id", "name", "goal", "audience", "scope", "out_of_scope",
                     "constraints", "open_questions"}, "project")
    for key in ("id", "name", "goal", "audience"):
        _text(project[key], f"project.{key}")
    for key in ("scope", "out_of_scope", "constraints", "open_questions"):
        _strings(project[key], f"project.{key}")

    objects = _index(state["objects"], "objects")
    tasks = _index(state["tasks"], "tasks")
    decisions = _index(state["decisions"], "decisions")
    for obj in objects.values():
        _shape(obj, {"id", "type", "label", "properties"}, "object")
        for key in ("type", "label"):
            _text(obj[key], f"object.{key}")
        _require(type(obj["properties"]) is dict, "object.properties: expected object")
        _json_value(obj["properties"], "object.properties")
    _list(state["relations"], "relations")
    seen_relations = set()
    for relation in state["relations"]:
        _shape(relation, {"from", "type", "to"} | ({"id"} if state["schema_version"] == 3 else set()), "relation")
        for key in relation:
            _text(relation[key], f"relation.{key}")
        _require(relation["from"] in objects and relation["to"] in objects,
                 "relation: unknown object")
        triple = (relation["from"], relation["type"], relation["to"])
        _require(triple not in seen_relations, "duplicate relation")
        seen_relations.add(triple)
    if state["schema_version"] == 3:
        _ontology().validate_graph(state)

    accepted_topics = set()
    for decision in decisions.values():
        _shape(decision, {"id", "topic", "statement", "status", "rationale", "source",
                          "supersedes", "accepted_by"}, "decision")
        for key in ("topic", "statement", "rationale"):
            _text(decision[key], f"decision.{key}")
        _text(decision["source"], "decision.source", allow_empty=True)
        _text(decision["status"], "decision.status")
        _require(decision["status"] in DECISION_STATUSES, "invalid decision status")
        if decision["status"] in {"accepted", "superseded"}:
            _text(decision["accepted_by"], "decision.accepted_by")
            _text(decision["source"], "accepted decision.source")
        else:
            _require(decision["accepted_by"] is None,
                     "unaccepted decision cannot have accepted_by")
        if decision["status"] == "accepted":
            _require(decision["topic"] not in accepted_topics,
                     "multiple accepted decisions for same topic")
            accepted_topics.add(decision["topic"])
        previous = decision["supersedes"]
        if previous is not None:
            _text(previous, "decision.supersedes")
            _require(previous in decisions and previous != decision["id"],
                     "invalid supersedes reference")
            predecessor = decisions[previous]
            _require(predecessor.get("topic") == decision["topic"],
                     "supersedes must use same topic")
            _require(predecessor.get("status") in {"accepted", "superseded"},
                     "supersedes must refer to previously accepted decision")
            if decision["status"] in {"accepted", "superseded"}:
                _require(predecessor.get("status") == "superseded",
                         "accepted replacement requires superseded predecessor")
    _acyclic({key: ([d["supersedes"]] if d["supersedes"] else [])
              for key, d in decisions.items()}, "decision supersedes")
    for key, decision in decisions.items():
        if decision["status"] == "superseded":
            successors = [d for d in decisions.values()
                          if d["supersedes"] == key and d["status"] in {"accepted", "superseded"}]
            _require(len(successors) == 1, "superseded decision needs one accepted successor")

    for task in tasks.values():
        _shape(task, {"id", "title", "status", "object_ids", "depends_on", "decision_ids",
                      "acceptance", "evidence"} | (DOMAIN_TASK_FIELDS | (set(task) & OPTIONAL_DOMAIN_TASK_FIELDS)
                                                 if state["schema_version"] == 3 else set()), "task")
        _text(task["title"], "task.title")
        _text(task["status"], "task.status")
        _require(task["status"] in TASK_STATUSES, "invalid task status")
        for field, lookup in (("object_ids", objects), ("depends_on", tasks),
                              ("decision_ids", decisions)):
            _strings(task[field], f"task.{field}", unique=True)
            _require(all(key in lookup for key in task[field]), f"task.{field}: unknown reference")
        _require(task["id"] not in task["depends_on"], "self dependency")
        _require(all(decisions[key]["status"] in {"accepted", "superseded"}
                     for key in task["decision_ids"]), "task must link accepted decision history")
        if state["schema_version"] == 3:
            for field in ("input_ids", "output_ids"):
                _strings(task[field], f"task.{field}", unique=True)
                _require(set(task[field]) <= set(task["object_ids"]), f"task.{field}: must be included in object_ids")
            _require(not set(task["input_ids"]) & set(task["output_ids"]), "task inputs and outputs overlap")
            _integer(task["generation"], "task.generation")
            _strings(task["review_reasons"], "task.review_reasons")
            snap = task["input_snapshot"]
            if snap is not None:
                _shape(snap, {"sha256", "manifest"}, "input snapshot")
                _shape(snap["manifest"], {"graph", "producer_generations", "contract"}, "snapshot manifest")
                _require(type(snap["manifest"]["graph"]) is dict and type(snap["manifest"]["producer_generations"]) is dict,
                         "invalid snapshot graph or generations")
                _json_value(snap["manifest"], "snapshot manifest")
                _require(snap["sha256"] == _manifest_hash(snap["manifest"]), "invalid input snapshot hash")
            for field in ("run_snapshot", "output_snapshot"):
                stored = task.get(field)
                if stored is not None:
                    _shape(stored, {"sha256", "manifest"}, f"task.{field}")
                    _json_value(stored["manifest"], f"task.{field}.manifest")
                    _require(stored["sha256"] == _manifest_hash(stored["manifest"]), f"invalid {field} hash")
            if "input_fields" in task:
                _require(type(task["input_fields"]) is dict, "input_fields must be object")
                closure = _ontology().upstream(state, task["input_ids"])
                for obj, fields in task["input_fields"].items():
                    _require(obj in closure, "projection object not in input closure")
                    _strings(fields, "input projection fields", unique=True)
                    definition = next(d for d in state["ontology"]["object_types"] if d["id"] == objects[obj]["type"])
                    _require(set(fields) <= set(definition["properties"]), "unknown projected property")
            if task.get("support_groups") or task.get("acceptance_rules"):
                _acceptance().validate_contract(task, state)
            if task["status"] == "done":
                _require(snap is not None and task["generation"] > 0, "done task requires input snapshot and generation")
        _strings(task["acceptance"], "task.acceptance")
        _require(bool(task["acceptance"]), "task requires acceptance criteria")
        _list(task["evidence"], "task.evidence")
        seen_evidence = set()
        for evidence in task["evidence"]:
            _shape(evidence, {"path", "sha256", "criterion", "note", "reviewer"}, "evidence")
            _relative_path(evidence["path"])
            _text(evidence["sha256"], "evidence.sha256")
            _require(len(evidence["sha256"]) == 64
                     and all(c in "0123456789abcdef" for c in evidence["sha256"]),
                     "evidence.sha256: expected lowercase SHA-256")
            _integer(evidence["criterion"], "evidence.criterion")
            _require(evidence["criterion"] < len(task["acceptance"]),
                     "evidence criterion out of range")
            _text(evidence["note"], "evidence.note")
            _text(evidence["reviewer"], "evidence.reviewer")
            pair = (evidence["path"], evidence["criterion"])
            _require(pair not in seen_evidence, "duplicate evidence for criterion/path")
            seen_evidence.add(pair)
        if task["status"] == "done":
            _require({e["criterion"] for e in task["evidence"]}
                     == set(range(len(task["acceptance"]))), "done task requires every criterion")
    _acyclic({key: task["depends_on"] for key, task in tasks.items()}, "task dependencies")
    _task_dependencies(state)
    if state["schema_version"] == 3:
        # Optional supports are not unconditional prerequisites, but may not
        # indirectly justify their own producer acceptance.
        graph, _ = _task_dependencies(state)
        owners = {obj: t["id"] for t in tasks.values() for obj in t["output_ids"]}
        for task in tasks.values():
            for group in task.get("support_groups", []):
                for branch in group["branches"]:
                    ids = [key for key in branch["input_ids"] if key in objects]
                    for obj in _ontology().upstream(state, ids):
                        producer = owners.get(obj)
                        if producer:
                            _require(producer != task["id"], "task cannot support itself")
                            if producer not in graph[task["id"]]:
                                graph[task["id"]].append(producer)
        _acyclic(graph, "support producer dependencies")

    _list(state["history"], "history")
    _require(len(state["history"]) == state["revision"], "history length must equal revision")
    for revision, entry in enumerate(state["history"], 1):
        fields = {"revision", "action", "actor", "reason"}
        if state["schema_version"] >= 2 and type(entry) is dict and "change" in entry:
            fields.add("change")
            _json_value(entry["change"], "history.change")
        _shape(entry, fields, "history entry")
        _integer(entry["revision"], "history.revision", 1)
        _require(entry["revision"] == revision, "history revisions must be contiguous")
        for field in ("action", "actor", "reason"):
            _text(entry[field], f"history.{field}")
        _require(entry["action"] in (ACTIONS if state["schema_version"] >= 2 else LEGACY_ACTIONS),
                 "unknown history action")
    if state["revision"] == 0:
        _require(all(t["status"] == "todo" and not t["evidence"] for t in tasks.values()),
                 "initial tasks must be todo without evidence")
        _require(all(d["status"] in {"proposed", "accepted"} for d in decisions.values()),
                 "initial decisions must be proposed or accepted")
        if state["schema_version"] == 3:
            _require(all(t["input_snapshot"] is None and t["generation"] == 0 and not t["review_reasons"]
                         and all(t.get(k) is None for k in ("run_snapshot", "output_snapshot", "support_snapshot"))
                         for t in tasks.values()), "initial ontology tasks require empty snapshots/generations/review reasons")


def validate(state):
    """Check complete structural/semantic contract without filesystem access."""
    try:
        _validate_data(state)
    except (TypeError, KeyError, AttributeError, RecursionError, OverflowError) as exc:
        # Cross-record checks can encounter malformed records before their own
        # shape check. Public callers always get the documented error type.
        raise ValueError(f"malformed project model: {exc}") from exc


def is_link(path):
    """Reject symlinks and Windows junction/reparse aliases (also Python 3.10)."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _hash_file(root, relative):
    parts = _relative_path(relative).parts
    base = Path(root).resolve()
    candidate = base
    try:
        for part in parts:
            candidate = candidate / part
            _require(not is_link(candidate), "evidence symlink forbidden")
        _require(candidate.resolve().is_relative_to(base), "evidence escapes project")
        _require(candidate.is_file(), f"evidence file missing or not regular: {relative}")
        digest = hashlib.sha256()
        with candidate.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"cannot read evidence {relative}: {exc}") from exc


def inspect_state(state, root):
    """Return computed readiness and stale evidence without modifying state."""
    validate(state)
    tasks = {task["id"]: task for task in state["tasks"]}
    decisions = {d["id"]: d for d in state["decisions"]}
    dependencies, producer_reasons = _task_dependencies(state)
    computed = {}

    def inspect(key):
        if key in computed:
            return computed[key]
        task = tasks[key]
        issues = []
        for reference in task["decision_ids"]:
            if decisions[reference]["status"] != "accepted":
                issues.append(f"decision {reference} superseded; reconcile required")
        for evidence in task["evidence"]:
            try:
                if _hash_file(root, evidence["path"]) != evidence["sha256"]:
                    issues.append(f"stale evidence: {evidence['path']}")
            except ValueError as exc:
                issues.append(str(exc))
        if state["schema_version"] == 3 and task["input_snapshot"] is not None:
            try:
                current = _input_snapshot(state, task, root)
                if current != task["input_snapshot"]:
                    issues.extend(_snapshot_changes(task["input_snapshot"], current))
            except ValueError as exc:
                issues.append(f"domain input unavailable: {exc}")
        if state["schema_version"] == 3:
            if task.get("run_snapshot") is not None and task["status"] == "doing":
                try:
                    if _input_snapshot(state, task, root) != task["run_snapshot"]:
                        issues.append("run inputs changed since start")
                except ValueError as exc:
                    issues.append(f"run input unavailable: {exc}")
            if task.get("output_snapshot") is not None:
                try:
                    if _output_snapshot(state, task, root) != task["output_snapshot"]:
                        issues.append("accepted output integrity changed")
                except ValueError as exc:
                    issues.append(f"accepted output unavailable: {exc}")
        support_report = None
        rule_report = []
        if state["schema_version"] == 3 and task.get("support_snapshot"):
            owners = {obj: t["id"] for t in state["tasks"] for obj in t["output_ids"]}
            statuses = {}
            for group in task.get("support_groups", []):
                for branch in group["branches"]:
                    available = [obj for obj in branch["input_ids"] if obj in {o["id"] for o in state["objects"]}]
                    for obj in _ontology().upstream(state, available):
                        owner = owners.get(obj)
                        if owner:
                            statuses[obj] = "current" if inspect(owner)["effective_status"] == "done" else "needs_review"
            support_report = _acceptance().inspect_supports(state, task, task["support_snapshot"], root,
                                                            _hash_file, _support_graph, statuses)
            if not support_report["current"]:
                issues.append("accepted support requirements no longer satisfied")
        if state["schema_version"] == 3 and task.get("acceptance_rules"):
            rule_report = _acceptance().evaluate_rules(state, task["acceptance_rules"])
            if task["status"] in {"done", "review"} and task["input_snapshot"] is not None:
                issues.extend(f"acceptance rule {r['id']}: {r['detail']}" for r in rule_report if not r["passed"])
        dependency_issues = []
        for dependency in dependencies[key]:
            result = inspect(dependency)
            if result["effective_status"] != "done":
                dependency_issues.append(f"dependency {dependency}: {result['effective_status']}")
        effective = task["status"]
        if effective != "cancelled":
            if issues:
                effective = "needs_review"
            elif dependency_issues:
                effective = "needs_review" if effective == "done" else "blocked"
        result = {"id": key, "title": task["title"], "status": task["status"],
                  "effective_status": effective, "issues": issues + dependency_issues,
                  "depends_on": list(task["depends_on"]),
                  "object_ids": list(task["object_ids"]),
                  "acceptance": list(task["acceptance"]),
                  "decision_ids": list(task["decision_ids"])}
        if state["schema_version"] == 3:
            result.update(input_ids=list(task["input_ids"]), output_ids=list(task["output_ids"]),
                          effective_depends_on=dependencies[key], producer_dependencies=producer_reasons[key],
                          review_reasons=list(task["review_reasons"]), generation=task["generation"])
            result.update(support_status=support_report, acceptance_rule_results=rule_report,
                          freshness="current" if effective == "done" else "stale" if task["generation"] else "unverified",
                          readiness="blocked" if dependency_issues else "ready")
        computed[key] = result
        return result

    try:
        ordered = [inspect(task["id"]) for task in state["tasks"]]
    except RecursionError as exc:
        raise ValueError("task graph exceeds supported nesting") from exc
    ready = [t["id"] for t in ordered if t["effective_status"] in {"todo", "doing", "review"}]
    warnings = [f"{t['id']}: {issue}" for t in ordered for issue in t["issues"]
                if not issue.startswith("dependency ") or t["status"] == "done"]
    repair_actions = []
    current_by_topic = {d["topic"]: d["id"] for d in decisions.values() if d["status"] == "accepted"}
    for result in ordered:
        task = tasks[result["id"]]
        if task["status"] == "done" and result["effective_status"] == "needs_review":
            repair_actions.append({"action": "reopen_task", "task_id": task["id"],
                                   "reason": "Recorded completion needs review: " + "; ".join(result["issues"])})
        elif task["status"] in {"todo", "doing", "review"} and any(
                decisions[key]["status"] == "superseded" for key in task["decision_ids"]):
            topics = list(dict.fromkeys(decisions[key]["topic"] for key in task["decision_ids"]))
            if all(topic in current_by_topic for topic in topics):
                repair_actions.append({"action": "reconcile_task", "task_id": task["id"],
                                       "decision_ids": [current_by_topic[topic] for topic in topics],
                                       "reason": "Review task under current accepted decisions for the same topics."})
    report = {"project": copy.deepcopy(state["project"]), "revision": state["revision"],
            "schema_version": state["schema_version"],
            "objects": copy.deepcopy(state["objects"]),
            "relations": copy.deepcopy(state["relations"]),
            "decisions": copy.deepcopy(state["decisions"]), "tasks": ordered,
            "ready": ready, "warnings": warnings, "repair_actions": repair_actions}
    if state["schema_version"] == 3:
        report["ontology"] = copy.deepcopy(state["ontology"])
        owners = {obj: t["id"] for t in state["tasks"] for obj in t["output_ids"]}
        report["object_status"] = []
        for obj in state["objects"]:
            producer = owners.get(obj["id"])
            status = "input"
            if producer:
                effective = computed[producer]["effective_status"]
                status = "current" if effective == "done" else "needs_review" if effective in {"review", "needs_review"} else "pending"
            report["object_status"].append({"object_id": obj["id"], "producer": producer, "status": status})
    return report


def render_context(state, root):
    report = inspect_state(state, root)
    project = report["project"]
    lines = [f"# {project['name']} — proje bağlamı", "",
             f"Revision: {report['revision']} · Yetkili kaynak: .project/state.json", "",
             "Bu görünüm türetilmiştir. Güncel kanıt kontrolü için context komutunu çalıştır.",
             "", f"Amaç: {project['goal']}", f"Hedef kitle: {project['audience']}"]
    for field, title in (("scope", "Kapsam"), ("out_of_scope", "Kapsam dışı"),
                          ("constraints", "Kısıtlar"), ("open_questions", "Açık sorular")):
        lines += ["", f"## {title}", ""]
        lines += [f"- {value}" for value in project[field]] or ["- Yok."]
    lines += ["", "## Nesneler ve ilişkiler", ""]
    lines += [f"- {obj['id']} ({obj['type']}): {obj['label']}" for obj in report["objects"]]
    relation_labels = {r["id"]: r["label"] for r in state.get("ontology", {}).get("relation_types", [])}
    lines += [f"- {rel['from']} → {relation_labels.get(rel['type'], rel['type'])} → {rel['to']}" for rel in report["relations"]]
    if state["schema_version"] == 3:
        lines += ["", "### Somut nesne değerleri", ""]
        for obj in state["objects"]:
            lines.append(f"- {obj['id']}: {json.dumps(obj['properties'], ensure_ascii=False, sort_keys=True)}")
        lines += ["", "Türler ve bağlantı kuralları: `ontology` komutu / `ONTOLOJİ.md`."]
    lines += ["", "## Kararlar", ""]
    for decision in report["decisions"]:
        lines += [f"- {decision['id']} [{decision['status']}]: {decision['statement']}",
                  f"  Gerekçe: {decision['rationale']}; kaynak: {decision['source'] or 'belirtilmedi'}; "
                  f"kabul eden: {decision['accepted_by'] or 'henüz kabul edilmedi'}"]
    lines += ["", "## Görevler", ""]
    for task in report["tasks"]:
        lines += [f"- {task['id']} [{task['effective_status']}] {task['title']} "
                  f"(kayıt: {task['status']})"]
        lines += [f"  - Ölçüt: {criterion}" for criterion in task["acceptance"]]
        lines += [f"  - İlgili nesneler: {', '.join(task['object_ids']) or 'yok'}"]
        if state["schema_version"] == 3:
            lines += [f"  - Girdiler: {', '.join(task['input_ids']) or 'yok'}",
                      f"  - Ürettiği nesneler: {', '.join(task['output_ids']) or 'yok'}",
                      f"  - Etkin önkoşullar: {', '.join(task['effective_depends_on']) or 'yok'}"]
            lines += [f"  - Üretici bağı: {r['object_id']} ← {r['task_id']}" for r in task["producer_dependencies"]]
            freshness = {"current": "güncel", "stale": "yeniden inceleme gerekli", "unverified": "henüz doğrulanmadı"}
            lines += [f"  - Kabul güncelliği: {freshness[task['freshness']]} · tamamlanma sayısı: {task['generation']}"]
            lines += [f"  - Alan kuralı: {r['label']} — {'geçti' if r['passed'] else 'karşılanmadı'}"
                      for r in task["acceptance_rule_results"]]
            if task.get("support_status"):
                lines += [f"  - İncelenmiş destekler: {'yeterli' if task['support_status']['current'] else 'yetersiz'}"]
            lines += [f"  - Yeniden inceleme: {reason}" for reason in task["review_reasons"]]
        lines += [f"  - Kontrol: {issue}" for issue in task["issues"]]
    lines += ["", "## Çalışılabilir görevler", "",
              ", ".join(report["ready"]) or "Şu anda çalışılabilir görev yok.", "",
              "## Uyarılar", ""]
    lines += [f"- {warning}" for warning in report["warnings"]] or ["- Yok."]
    lines += ["", "## Onarım işlemleri", "",
              "Bunlar öneridir; gerekçeyi değerlendir, actor ekle ve güncel revision ile uygula."]
    lines += [f"- {item['action']} → {item['task_id']}: {item['reason']}"
              + (f" Kararlar: {', '.join(item['decision_ids'])}" if "decision_ids" in item else "")
              for item in report["repair_actions"]] or ["- Yok."]
    lines += ["", "Kanıt hash'i dosya sürümünü denetler; kalite veya insan kabulünü ispatlamaz.", ""]
    return "\n".join(lines)


def render_ontology(state, root):
    """Readable ontology and a diagram derived from the authoritative state."""
    report = inspect_state(state, root)
    lines = [f"# {state['project']['name']} — Ontoloji", "",
             f"Revizyon: {state['revision']}. Canlı görünüm için `ontology` komutunu çalıştır.", ""]
    if state["schema_version"] != 3:
        return "\n".join(lines + ["Bu eski kayıtta tip sözleşmesi yok. Açık ontoloji geçişi gerekiyor.", ""])
    lines += ["## Türler ve özellikler", ""]
    for kind in state["ontology"]["object_types"]:
        lines += [f"### {kind['label']} (`{kind['id']}`)", ""]
        if kind.get("immutable"):
            lines += ["Geçmiş kaydı korunur; farklı içerik için yeni kimlik/sürüm gerekir.", ""]
        for key, spec in kind["properties"].items():
            lines.append(f"- {key}: {spec['type']}; {'zorunlu' if spec['required'] else 'isteğe bağlı'}; seçenekler: {spec['enum']}")
    lines += ["", "## İlişki kuralları", ""]
    for rel in state["ontology"]["relation_types"]:
        lines.append(f"- {rel['label']} (`{rel['id']}`): {rel['from_type']} → {rel['to_type']}; "
                     f"kaynak başına {rel['from_min']}..{rel['from_max'] if rel['from_max'] is not None else 'çok'}, "
                     f"hedef başına {rel['to_min']}..{rel['to_max'] if rel['to_max'] is not None else 'çok'}; etki: {rel['impact']}")
    lines += ["", "## Somut nesneler", ""]
    status = {s["object_id"]: s for s in report["object_status"]}
    for obj in state["objects"]:
        lines.append(f"- **{obj['label']}** (`{obj['id']}`, {obj['type']}): "
                     f"{json.dumps(obj['properties'], ensure_ascii=False, sort_keys=True)}; "
                     f"durum: {status[obj['id']]['status']}; üretici: {status[obj['id']]['producer'] or 'dış girdi'}")
    # Mermaid identifiers never use untrusted user IDs; labels are entity escaped.
    def escaped(value):
        return str(value).replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", " ").replace("`", "&#96;")
    ids = {obj["id"]: f"n{i}" for i, obj in enumerate(state["objects"])}
    relation_types = {r["id"]: r for r in state["ontology"]["relation_types"]}
    lines += ["", "## Nesne haritası", "", "```mermaid", "flowchart LR"]
    for obj in state["objects"]:
        lines.append(f'  {ids[obj["id"]]}["{escaped(obj["label"])}"]')
    for rel in state["relations"]:
        lines.append(f'  {ids[rel["from"]]} -->|"{escaped(relation_types[rel["type"]]["label"])}"| {ids[rel["to"]]}')
    lines += ["```", "", "Oklar kayıtlı ilişki yönüdür; değişiklik etkisinin yönü üstte ayrıca tanımlıdır.",
              "", "## Görevlerin veri bağları", ""]
    for task in report["tasks"]:
        lines.append(f"- **{task['id']} — {task['title']}**: girdiler [{', '.join(task['input_ids'])}], "
                     f"çıktılar [{', '.join(task['output_ids'])}], durum {task['effective_status']}.")
        lines += [f"  - Kabul kuralı: {rule['label']} — {'geçti' if rule['passed'] else 'karşılanmadı'}"
                  for rule in task["acceptance_rule_results"]]
    lines += ["", "Etki yeniden inceleme ihtiyacıdır; nesnenin yanlış olduğu hükmü değildir.", ""]
    return "\n".join(lines)


def _clear_acceptance(task, reason, status="review"):
    task["status"], task["evidence"] = status, []
    if "input_snapshot" in task:
        task["input_snapshot"] = None
        task["output_snapshot"] = None
        task["support_snapshot"] = None
        if reason not in task["review_reasons"]:
            task["review_reasons"].append(reason)


def _invalidate_domain(before, after, impact, root):
    affected = set(impact["affected_objects"])
    direct = set()
    before_tasks = {t["id"]: t for t in before["tasks"]}
    for task in after["tasks"]:
        old = before_tasks.get(task["id"])
        if old is None:
            continue
        try:
            changed_input = _input_snapshot(before, old, root) != _input_snapshot(after, task, root)
        except ValueError:
            changed_input = bool(set(task["input_ids"]) & affected)
        changed_output = False
        if old.get("output_snapshot") is not None:
            try:
                changed_output = _output_snapshot(after, task, root) != old["output_snapshot"]
            except ValueError:
                changed_output = True
        if changed_input or changed_output:
            direct.add(task["id"])
    old_deps, _ = _task_dependencies(before)
    new_deps, _ = _task_dependencies(after)
    tasks = {t["id"]: t for t in after["tasks"]}
    reasons = {}
    for key in direct:
        hits = sorted((set(tasks[key]["input_ids"]) | set(tasks[key]["output_ids"])) & affected)
        reasons[key] = [" → ".join(impact["paths"].get(obj, [obj])) for obj in hits] or ["input contract/producer or accepted output changed"]
    pending = list(direct)
    while pending:
        parent = pending.pop()
        for key in tasks:
            if parent in set(old_deps.get(key, [])) | set(new_deps.get(key, [])) and key not in reasons:
                reasons[key] = [f"producer/dependency task {parent} requires renewed review"]
                pending.append(key)
    for key, why in reasons.items():
        if tasks[key]["status"] in {"done", "doing", "review"}:
            _clear_acceptance(tasks[key], "; ".join(why))
    impact["affected_tasks"] = [{"task_id": key, "reasons": reasons[key]} for key in sorted(reasons)]
    return impact


def _graph_operations(state, operations):
    _list(operations, "operations")
    _require(bool(operations), "empty graph transaction")
    # Tombstones are derived from retained transaction history. A deleted ID
    # must not silently become a different entity later.
    used_objects = {o["id"] for o in state["objects"]}
    used_relations = {r["id"] for r in state["relations"]}
    for entry in state["history"]:
        change = entry.get("change", {})
        for obj in change.get("before_graph", {}).get("objects", []):
            used_objects.add(obj["id"])
        for rel in change.get("before_graph", {}).get("relations", []):
            used_relations.add(rel["id"])
        for operation in change.get("operations", []):
            if operation.get("op") == "add_object":
                used_objects.add(operation["object"]["id"])
            if operation.get("op") == "add_relation":
                used_relations.add(operation["relation"]["id"])
    protected_objects = {o["id"]: copy.deepcopy(o) for o in state["objects"] if
                         next(t for t in state["ontology"]["object_types"] if t["id"] == o["type"]).get("immutable", False)}
    protected_relations = {r["id"]: copy.deepcopy(r) for r in state["relations"] if
                           next(t for t in state["ontology"]["relation_types"] if t["id"] == r["type"]).get("immutable", False)}
    for operation in operations:
        _require(type(operation) is dict, "graph operation must be object")
        op = operation.get("op")
        if op in {"add_object", "replace_object"}:
            _shape(operation, {"op", "object"}, "object operation")
            obj = copy.deepcopy(operation["object"])
            _require(type(obj) is dict and "id" in obj, "object missing id")
            old = next((o for o in state["objects"] if o["id"] == obj["id"]), None)
            if op == "add_object":
                _require(old is None and obj["id"] not in used_objects, "object id exists or was retired")
                state["objects"].append(obj)
                used_objects.add(obj["id"])
            else:
                _require(old is not None, "cannot replace unknown object")
                state["objects"][state["objects"].index(old)] = obj
        elif op == "remove_object":
            _shape(operation, {"op", "object_id"}, "remove object")
            _text(operation["object_id"], "object_id")
            old = next((o for o in state["objects"] if o["id"] == operation["object_id"]), None)
            _require(old is not None, "cannot remove unknown object")
            state["objects"].remove(old)
        elif op == "add_relation":
            _shape(operation, {"op", "relation"}, "add relation")
            _require(type(operation["relation"]) is dict and "id" in operation["relation"], "relation missing id")
            _require(operation["relation"]["id"] not in used_relations, "relation id exists or was retired")
            state["relations"].append(copy.deepcopy(operation["relation"]))
            used_relations.add(operation["relation"]["id"])
        elif op == "remove_relation":
            _shape(operation, {"op", "relation_id"}, "remove relation")
            _text(operation["relation_id"], "relation_id")
            old = next((r for r in state["relations"] if r["id"] == operation["relation_id"]), None)
            _require(old is not None, "cannot remove unknown relation")
            state["relations"].remove(old)
        elif op == "replace_ontology":
            _shape(operation, {"op", "ontology"}, "replace ontology")
            state["ontology"] = copy.deepcopy(operation["ontology"])
        else:
            raise ValueError(f"unsupported graph operation: {op}")
    _ontology().validate_graph(state)
    current_objects = {o["id"]: o for o in state["objects"]}
    current_relations = {r["id"]: r for r in state["relations"]}
    object_types = {t["id"]: t for t in state["ontology"]["object_types"]}
    relation_types = {t["id"]: t for t in state["ontology"]["relation_types"]}
    for key, old in protected_objects.items():
        current = current_objects.get(key)
        _require(current is not None and current["type"] == old["type"] and
                 object_types[old["type"]].get("immutable", False) and
                 _manifest_hash(current["properties"]) == _manifest_hash(old["properties"]),
                 f"immutable historical object {key}: create a new version ID")
    for key, old in protected_relations.items():
        _require(current_relations.get(key) == old and relation_types[old["type"]].get("immutable", False),
                 f"immutable historical relation {key}: create new records")


def apply_event(state, event, root):
    """Apply one supported event to a deep copy; raise ValueError on rejection."""
    validate(state)
    _require(type(event) is dict, "event must be object")
    _text(event.get("action"), "event.action")
    action = event["action"]
    _require(action in ACTIONS, "unsupported action")
    event_fields = {"action", "actor", "reason"} | ACTIONS[action]
    if state["schema_version"] == 3 and action == "submit_evidence" and "reviewed_supports" in event:
        event_fields.add("reviewed_supports")
    _shape(event, event_fields, "event")
    _text(event["actor"], "event.actor")
    _text(event["reason"], "event.reason")
    new = copy.deepcopy(state)
    tasks = {t["id"]: t for t in new["tasks"]}
    decisions = {d["id"]: d for d in new["decisions"]}
    task = None
    if "task_id" in event:
        _text(event["task_id"], "event.task_id")
        _require(event["task_id"] in tasks, "unknown task_id")
        task = tasks[event["task_id"]]
    decision = None
    if "decision_id" in event:
        _text(event["decision_id"], "event.decision_id")
        _require(event["decision_id"] in decisions, "unknown decision_id")
        decision = decisions[event["decision_id"]]

    def dependencies_ready():
        report = inspect_state(state, root)
        effective = {t["id"]: t["effective_status"] for t in report["tasks"]}
        dependencies, _ = _task_dependencies(state)
        _require(all(effective[key] == "done" for key in dependencies[task["id"]]),
                 "task dependencies are not effectively done")
        _require(all(decisions[key]["status"] == "accepted" for key in task["decision_ids"]),
                 "task decision changed; reconcile required")

    def invalidate_dependents():
        # Preserve invalidation after the reopened prerequisite becomes done
        # again: old downstream evidence must never silently regain acceptance.
        old_deps, _ = _task_dependencies(state)
        new_deps, _ = _task_dependencies(new)
        pending, seen = [task["id"]], set()
        while pending:
            parent = pending.pop()
            for candidate in tasks.values():
                if parent not in set(old_deps.get(candidate["id"], [])) | set(new_deps.get(candidate["id"], [])) or candidate["id"] in seen:
                    continue
                seen.add(candidate["id"])
                pending.append(candidate["id"])
                if candidate["status"] in {"done", "review", "doing"}:
                    _clear_acceptance(candidate, f"dependency task {parent} changed")

    change = None
    if action == "migrate_ontology":
        _require(state["schema_version"] in {1, 2}, "ontology migration requires legacy schema")
        _list(event["bindings"], "bindings")
        bindings = {}
        for binding in event["bindings"]:
            _shape(binding, {"task_id", "object_ids", "input_ids", "output_ids"}, "migration binding")
            _text(binding["task_id"], "binding.task_id")
            _require(binding["task_id"] not in bindings, "duplicate task binding")
            bindings[binding["task_id"]] = binding
        _require(set(bindings) == set(tasks), "migration must explicitly bind every task")
        change = {"previous_schema": state["schema_version"], "previous_objects": copy.deepcopy(state["objects"]),
                  "previous_relations": copy.deepcopy(state["relations"]), "previous_tasks": copy.deepcopy(state["tasks"])}
        new["schema_version"] = 3
        for field in ("ontology", "objects", "relations"):
            new[field] = copy.deepcopy(event[field])
        for key, current in tasks.items():
            binding = bindings[key]
            current.update({k: copy.deepcopy(binding[k]) for k in ("object_ids", "input_ids", "output_ids")})
            current.update(input_snapshot=None, generation=0, review_reasons=[])
            if current["status"] in {"doing", "review", "done"}:
                _clear_acceptance(current, "ontology migration: legacy evidence was not reviewed against new domain bindings")
    elif action == "mutate_graph":
        _require(state["schema_version"] == 3, "migrate ontology before graph mutation")
        _graph_operations(new, event["operations"])
        _require(any(new[key] != state[key] for key in ("ontology", "objects", "relations")), "unchanged graph transaction")
        domain_impact = _ontology().impact(state, new)
        _invalidate_domain(state, new, domain_impact, root)
        change = {"operations": copy.deepcopy(event["operations"]), "impact": domain_impact,
                  "before_graph": {k: copy.deepcopy(state[k]) for k in ("ontology", "objects", "relations")}}
    elif action == "extend_model":
        for collection in ("objects", "relations", "tasks"):
            _list(event[collection], f"event.{collection}")
        _require(any(event[key] for key in ("objects", "relations", "tasks")), "empty model extension")
        for added in event["tasks"]:
            _shape(added, {"id", "status", "evidence"} | DEFINITION_FIELDS |
                   (DOMAIN_TASK_FIELDS | (set(added) & OPTIONAL_DOMAIN_TASK_FIELDS) if state["schema_version"] == 3 else set()), "new task")
            _require(added["status"] == "todo" and added["evidence"] == [],
                     "new tasks must be todo without evidence")
            _strings(added["decision_ids"], "new task.decision_ids", unique=True)
            _require(all(key in decisions and decisions[key]["status"] == "accepted"
                         for key in added["decision_ids"]), "new tasks require current accepted decisions")
            if state["schema_version"] == 3:
                _require(added["input_snapshot"] is None and added["generation"] == 0 and not added["review_reasons"]
                         and all(added.get(k) is None for k in ("run_snapshot", "output_snapshot", "support_snapshot")),
                         "new tasks require empty domain acceptance")
        change = {key: copy.deepcopy(event[key]) for key in ("objects", "relations", "tasks")}
        if state["schema_version"] == 3:
            operations = ([{"op": "add_object", "object": o} for o in event["objects"]] +
                          [{"op": "add_relation", "relation": r} for r in event["relations"]])
            if operations:
                _graph_operations(new, operations)
            new["tasks"].extend(copy.deepcopy(event["tasks"]))
        else:
            for collection in change:
                new[collection].extend(copy.deepcopy(change[collection]))
        if state["schema_version"] == 3:
            _ontology().validate_graph(new)
            change["impact"] = _invalidate_domain(state, new, _ontology().impact(state, new), root)
        else:
            new["schema_version"] = 2
    elif action == "revise_task":
        definition = event["definition"]
        fields = _definition_fields(state) | (set(definition) & {"input_fields", "support_groups", "acceptance_rules"}
                                               if state["schema_version"] == 3 else set())
        _shape(definition, fields, "task definition")
        _strings(definition["depends_on"], "definition.depends_on", unique=True)
        _require(task["status"] != "cancelled", "cannot revise cancelled task")
        _require(any(task.get(key) != definition[key] for key in fields), "unchanged task definition")
        _strings(definition["decision_ids"], "definition.decision_ids", unique=True)
        _require(all(key in decisions and decisions[key]["status"] == "accepted"
                     for key in definition["decision_ids"]), "revision requires current accepted decisions")
        old_topics = {decisions[key]["topic"] for key in task["decision_ids"]}
        new_topics = {decisions[key]["topic"] for key in definition["decision_ids"]}
        _require(old_topics <= new_topics, "revision cannot silently remove existing decision topics")
        before = copy.deepcopy(task)
        task.update(copy.deepcopy(definition))
        _clear_acceptance(task, "task definition revised", status="todo")
        invalidate_dependents()
        change = {"before": before, "after": copy.deepcopy(task)}
        if state["schema_version"] != 3:
            new["schema_version"] = 2
    elif action == "start_task":
        _require(task["status"] in {"todo", "review"}, "start_task requires todo/review")
        dependencies_ready()
        task["status"] = "doing"
        if state["schema_version"] == 3:
            task["run_snapshot"] = _input_snapshot(new, task, root)
    elif action == "submit_evidence":
        _require(task["status"] in {"doing", "review"}, "submit_evidence requires doing/review")
        dependencies_ready()
        if state["schema_version"] == 3 and task.get("run_snapshot") is not None:
            _require(_input_snapshot(new, task, root) == task["run_snapshot"],
                     "run inputs changed: restart task before submitting")
        _list(event["items"], "event.items")
        _require(bool(event["items"]), "submit_evidence requires items")
        evidence = []
        for item in event["items"]:
            _shape(item, {"path", "criterion", "note", "reviewer"}, "evidence item")
            _integer(item["criterion"], "item.criterion")
            _require(item["criterion"] < len(task["acceptance"]), "criterion out of range")
            _text(item["note"], "item.note")
            _text(item["reviewer"], "item.reviewer")
            evidence.append({**item, "sha256": _hash_file(root, item["path"])})
        task["evidence"] = evidence
        task["status"] = "review"
        if state["schema_version"] == 3:
            task["input_snapshot"] = _input_snapshot(new, task, root)
            task["output_snapshot"] = _output_snapshot(new, task, root)
            if task.get("support_groups"):
                _require("reviewed_supports" in event, "explicit reviewed_supports required")
                report = inspect_state(new, root)
                task["support_snapshot"] = _acceptance().support_snapshot(
                    new, task, root, _hash_file, event["reviewed_supports"], _support_graph,
                    _producer_statuses(new, report))
            task["review_reasons"] = []
    elif action == "complete_task":
        _require(task["status"] == "review", "complete_task requires review")
        dependencies_ready()
        _require({e["criterion"] for e in task["evidence"]}
                 == set(range(len(task["acceptance"]))), "missing acceptance evidence")
        for item in task["evidence"]:
            _require(_hash_file(root, item["path"]) == item["sha256"], "stale evidence")
        if state["schema_version"] == 3:
            _require(task["input_snapshot"] is not None and _input_snapshot(new, task, root) == task["input_snapshot"],
                     "domain inputs changed or missing snapshot: resubmit evidence")
            _require(task.get("output_snapshot") == _output_snapshot(new, task, root),
                     "output changed after review: resubmit evidence")
            if task.get("acceptance_rules"):
                failed = [r for r in _acceptance().evaluate_rules(new, task["acceptance_rules"]) if not r["passed"]]
                _require(not failed, f"acceptance rules failed: {failed}")
            if task.get("support_groups"):
                _require(task.get("support_snapshot") is not None, "missing approved supports")
                report = inspect_state(new, root)
                supports = _acceptance().inspect_supports(new, task, task["support_snapshot"], root,
                    _hash_file, _support_graph, _producer_statuses(new, report))
                _require(supports["current"], "approved support requirements lost")
            task["generation"] += 1
        task["status"] = "done"
    elif action == "reopen_task":
        _require(task["status"] in {"done", "review", "doing"}, "cannot reopen task from this status")
        _clear_acceptance(task, "task reopened", status="todo")
        invalidate_dependents()
    elif action == "propose_decision":
        proposal = event["decision"]
        _shape(proposal, {"id", "topic", "statement", "rationale", "source", "supersedes"}, "proposal")
        _text(proposal["id"], "proposal.id")
        _require(proposal["id"] not in decisions, "decision id already exists")
        new["decisions"].append({**copy.deepcopy(proposal), "status": "proposed", "accepted_by": None})
    elif action == "accept_decision":
        _require(decision["status"] == "proposed", "accept_decision requires proposed")
        _text(decision["source"], "accepted decision source")
        previous = next((d for d in decisions.values()
                         if d["topic"] == decision["topic"] and d["status"] == "accepted"), None)
        _require(decision["supersedes"] == (previous["id"] if previous else None),
                 "supersedes must identify current accepted decision")
        if previous:
            previous["status"] = "superseded"
        decision["status"], decision["accepted_by"] = "accepted", event["actor"]
    elif action == "reject_decision":
        _require(decision["status"] == "proposed", "reject_decision requires proposed")
        decision["status"] = "rejected"
    elif action == "reconcile_task":
        _require(task["status"] in {"todo", "doing", "review"}, "reopen done task before reconciliation")
        _strings(event["decision_ids"], "event.decision_ids", unique=True)
        _require(all(key in decisions and decisions[key]["status"] == "accepted"
                     for key in event["decision_ids"]), "reconcile requires current accepted decisions")
        old_topics = {decisions[key]["topic"] for key in task["decision_ids"]}
        new_topics = {decisions[key]["topic"] for key in event["decision_ids"]}
        _require(old_topics <= new_topics, "reconcile cannot silently remove existing decision topics")
        task["decision_ids"] = list(event["decision_ids"])
        _clear_acceptance(task, "decision bindings reconciled", status="todo")
        invalidate_dependents()
    if new["schema_version"] == 3:
        # Validate a candidate with its next history slot before inspecting
        # supports; inspect_state itself enforces the complete schema contract.
        new["revision"] += 1
        new["history"].append({"revision": new["revision"], "action": action,
                               "actor": event["actor"], "reason": event["reason"]})
        effects = _refresh_supports(new, root)
        if effects:
            if change is None:
                change = {}
            change["support_effects"] = effects
    else:
        new["revision"] += 1
        new["history"].append({"revision": new["revision"], "action": action,
                               "actor": event["actor"], "reason": event["reason"]})
    if change is not None:
        new["history"][-1]["change"] = change
    if new["schema_version"] == 3:
        record = new["history"][-1].setdefault("change", {})
        old_tasks = {t["id"]: t for t in state["tasks"]}
        record["task_changes"] = [{"task_id": t["id"], "before": copy.deepcopy(old_tasks.get(t["id"])),
                                   "after": copy.deepcopy(t)} for t in new["tasks"] if t != old_tasks.get(t["id"])]
    validate(new)
    return new


def preview_digest(state, event, root, next_state=None):
    """Bind a preview to state, action and observed referenced file contents.

    The writer lock serializes cooperating CLI processes, not external editors.
    This detects observed drift; it is not a filesystem transaction/snapshot.
    """
    if next_state is None:
        next_state = apply_event(state, event, root)
    paths = set()
    for model in (state, next_state):
        for task in model["tasks"]:
            paths.update(item["path"] for item in task["evidence"])
        if model["schema_version"] == 3:
            types = {t["id"]: t for t in model["ontology"]["object_types"]}
            for obj in model["objects"]:
                paths.update(value for key, value in obj["properties"].items()
                             if types[obj["type"]]["properties"][key]["type"] == "file")
    files = {}
    for path in sorted(paths):
        try:
            files[path] = {"sha256": _hash_file(root, path)}
        except (ValueError, OSError) as error:
            files[path] = {"unavailable": str(error)}
    return _manifest_hash({"semantics_version": 1, "state": state, "event": event,
                           "next_state": next_state, "files": files})


def preview_event(state, event, root):
    """Run the same transition without writing; expose a reviewable change set."""
    next_state = apply_event(state, event, root)
    before_tasks = {t["id"]: t for t in state["tasks"]}
    changed_tasks = [{"task_id": t["id"], "before": before_tasks.get(t["id"], {}).get("status"),
                      "after": t["status"], "reasons": t.get("review_reasons", [])}
                     for t in next_state["tasks"] if t != before_tasks.get(t["id"])]
    change = next_state["history"][-1].get("change", {})
    return {"ok": True, "result": "preview", "state_committed": False,
            "revision": state["revision"], "next_revision": next_state["revision"],
            "action": event["action"], "changed_tasks": changed_tasks,
            "preview_digest": preview_digest(state, event, root, next_state),
            "impact": change.get("impact", {}), "change": change,
            "next_context": inspect_state(next_state, root)}
