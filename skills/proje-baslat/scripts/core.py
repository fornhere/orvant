"""Project model checks and typed state transitions; Python 3.10+, stdlib only.

This module validates local records, not the truth of reviewer/actor statements.
Single-writer orchestration and atomic persistence belong to the CLI.
"""
from __future__ import annotations

import copy
import hashlib
import math
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
                "revise_task": {"task_id", "definition"}})
DEFINITION_FIELDS = {"title", "object_ids", "depends_on", "decision_ids", "acceptance"}


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
    _require(".." not in posix.parts and ".project" not in posix.parts
             and posix.parts and "\x00" not in value,
             "evidence.path: traversal or managed path forbidden")
    return posix


def _validate_data(state):
    _shape(state, {"schema_version", "revision", "project", "objects", "relations",
                   "tasks", "decisions", "history"}, "state")
    _require(type(state["schema_version"]) is int and state["schema_version"] in {1, 2},
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
        _shape(relation, {"from", "type", "to"}, "relation")
        for key in relation:
            _text(relation[key], f"relation.{key}")
        _require(relation["from"] in objects and relation["to"] in objects,
                 "relation: unknown object")
        triple = (relation["from"], relation["type"], relation["to"])
        _require(triple not in seen_relations, "duplicate relation")
        seen_relations.add(triple)

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
                      "acceptance", "evidence"}, "task")
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

    _list(state["history"], "history")
    _require(len(state["history"]) == state["revision"], "history length must equal revision")
    for revision, entry in enumerate(state["history"], 1):
        fields = {"revision", "action", "actor", "reason"}
        if state["schema_version"] == 2 and type(entry) is dict and "change" in entry:
            fields.add("change")
            _json_value(entry["change"], "history.change")
        _shape(entry, fields, "history entry")
        _integer(entry["revision"], "history.revision", 1)
        _require(entry["revision"] == revision, "history revisions must be contiguous")
        for field in ("action", "actor", "reason"):
            _text(entry[field], f"history.{field}")
        _require(entry["action"] in (ACTIONS if state["schema_version"] == 2 else LEGACY_ACTIONS),
                 "unknown history action")
    if state["revision"] == 0:
        _require(all(t["status"] == "todo" and not t["evidence"] for t in tasks.values()),
                 "initial tasks must be todo without evidence")
        _require(all(d["status"] in {"proposed", "accepted"} for d in decisions.values()),
                 "initial decisions must be proposed or accepted")


def validate(state):
    """Check complete structural/semantic contract without filesystem access."""
    try:
        _validate_data(state)
    except (TypeError, KeyError, AttributeError, RecursionError, OverflowError) as exc:
        # Cross-record checks can encounter malformed records before their own
        # shape check. Public callers always get the documented error type.
        raise ValueError(f"malformed project model: {exc}") from exc


def _hash_file(root, relative):
    parts = _relative_path(relative).parts
    base = Path(root).resolve()
    candidate = base
    try:
        for part in parts:
            candidate = candidate / part
            _require(not candidate.is_symlink(), "evidence symlink forbidden")
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
        dependency_issues = []
        for dependency in task["depends_on"]:
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
                  "acceptance": list(task["acceptance"]),
                  "decision_ids": list(task["decision_ids"])}
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
    return {"project": copy.deepcopy(state["project"]), "revision": state["revision"],
            "objects": copy.deepcopy(state["objects"]),
            "relations": copy.deepcopy(state["relations"]),
            "decisions": copy.deepcopy(state["decisions"]), "tasks": ordered,
            "ready": ready, "warnings": warnings, "repair_actions": repair_actions}


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
    lines += [f"- {rel['from']} → {rel['type']} → {rel['to']}" for rel in report["relations"]]
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


def apply_event(state, event, root):
    """Apply one supported event to a deep copy; raise ValueError on rejection."""
    validate(state)
    _require(type(event) is dict, "event must be object")
    _text(event.get("action"), "event.action")
    action = event["action"]
    _require(action in ACTIONS, "unsupported action")
    _shape(event, {"action", "actor", "reason"} | ACTIONS[action], "event")
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
        _require(all(effective[key] == "done" for key in task["depends_on"]),
                 "task dependencies are not effectively done")
        _require(all(decisions[key]["status"] == "accepted" for key in task["decision_ids"]),
                 "task decision changed; reconcile required")

    def invalidate_dependents():
        # Preserve invalidation after the reopened prerequisite becomes done
        # again: old downstream evidence must never silently regain acceptance.
        pending, seen = [task["id"]], set()
        while pending:
            parent = pending.pop()
            for candidate in tasks.values():
                if parent not in candidate["depends_on"] or candidate["id"] in seen:
                    continue
                seen.add(candidate["id"])
                pending.append(candidate["id"])
                if candidate["status"] in {"done", "review", "doing"}:
                    candidate["status"], candidate["evidence"] = "review", []

    change = None
    if action == "extend_model":
        for collection in ("objects", "relations", "tasks"):
            _list(event[collection], f"event.{collection}")
        _require(any(event[key] for key in ("objects", "relations", "tasks")), "empty model extension")
        for added in event["tasks"]:
            _shape(added, {"id", "status", "evidence"} | DEFINITION_FIELDS, "new task")
            _require(added["status"] == "todo" and added["evidence"] == [],
                     "new tasks must be todo without evidence")
            _strings(added["decision_ids"], "new task.decision_ids", unique=True)
            _require(all(key in decisions and decisions[key]["status"] == "accepted"
                         for key in added["decision_ids"]), "new tasks require current accepted decisions")
        change = {key: copy.deepcopy(event[key]) for key in ("objects", "relations", "tasks")}
        for collection in change:
            new[collection].extend(copy.deepcopy(change[collection]))
        new["schema_version"] = 2
    elif action == "revise_task":
        definition = event["definition"]
        _shape(definition, DEFINITION_FIELDS, "task definition")
        _strings(definition["depends_on"], "definition.depends_on", unique=True)
        _require(task["status"] != "cancelled", "cannot revise cancelled task")
        _require(any(task[key] != definition[key] for key in DEFINITION_FIELDS), "unchanged task definition")
        _strings(definition["decision_ids"], "definition.decision_ids", unique=True)
        _require(all(key in decisions and decisions[key]["status"] == "accepted"
                     for key in definition["decision_ids"]), "revision requires current accepted decisions")
        old_topics = {decisions[key]["topic"] for key in task["decision_ids"]}
        new_topics = {decisions[key]["topic"] for key in definition["decision_ids"]}
        _require(old_topics <= new_topics, "revision cannot silently remove existing decision topics")
        before = copy.deepcopy(task)
        task.update(copy.deepcopy(definition))
        task["status"], task["evidence"] = "todo", []
        invalidate_dependents()
        change = {"before": before, "after": copy.deepcopy(task)}
        new["schema_version"] = 2
    elif action == "start_task":
        _require(task["status"] in {"todo", "review"}, "start_task requires todo/review")
        dependencies_ready()
        task["status"] = "doing"
    elif action == "submit_evidence":
        _require(task["status"] in {"doing", "review"}, "submit_evidence requires doing/review")
        dependencies_ready()
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
    elif action == "complete_task":
        _require(task["status"] == "review", "complete_task requires review")
        dependencies_ready()
        _require({e["criterion"] for e in task["evidence"]}
                 == set(range(len(task["acceptance"]))), "missing acceptance evidence")
        for item in task["evidence"]:
            _require(_hash_file(root, item["path"]) == item["sha256"], "stale evidence")
        task["status"] = "done"
    elif action == "reopen_task":
        _require(task["status"] in {"done", "review", "doing"}, "cannot reopen task from this status")
        task["status"], task["evidence"] = "todo", []
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
        task["status"], task["evidence"] = "todo", []
        invalidate_dependents()
    new["revision"] += 1
    new["history"].append({"revision": new["revision"], "action": action,
                           "actor": event["actor"], "reason": event["reason"]})
    if change is not None:
        new["history"][-1]["change"] = change
    validate(new)
    return new
