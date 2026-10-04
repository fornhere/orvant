"""Explicit alternative supports and bounded declarative acceptance rules.

These helpers never mutate state. The caller must persist returned lost flags:
once a reviewed branch is lost it cannot regain acceptance by being restored;
a new explicit evidence review is required. Rule failure is a pending acceptance
condition, not a structural graph error. No Python or expression evaluation is
performed. This module does not authenticate reviewers or prove source truth.
"""
from __future__ import annotations

import copy
import json


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _shape(value, fields, label):
    _require(type(value) is dict and set(value) == set(fields),
             f"{label}: unexpected or missing fields")


def _text(value, label):
    _require(type(value) is str and bool(value.strip()), f"{label}: expected nonempty string")


def _ids(values, label):
    _require(type(values) is list, f"{label}: expected list")
    for value in values:
        _text(value, label)
    _require(len(set(values)) == len(values), f"{label}: duplicate identifier")


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False)


def _selector_contract(selector, state):
    _shape(selector, {"roots", "path", "property"}, "selector")
    _ids(selector["roots"], "selector.roots")
    _require(type(selector["path"]) is list, "selector.path: expected list")
    declared = {item["id"] for item in state["ontology"]["relation_types"]}
    for step in selector["path"]:
        _shape(step, {"relation_type", "direction"}, "selector path step")
        _text(step["relation_type"], "selector.relation_type")
        _require(step["relation_type"] in declared, "selector: unknown relation type")
        _require(step["direction"] in ("out", "in"), "selector: expected out/in direction")
    if selector["property"] is not None:
        _text(selector["property"], "selector.property")


def _rule_contract(rule, state, seen, depth=0):
    _require(depth <= 32, "acceptance rules exceed 32 nesting levels")
    _require(type(rule) is dict, "acceptance rule: expected object")
    op = rule.get("op")
    shapes = {"equal_sets": {"left", "right"}, "disjoint": {"left", "right"},
              "count": {"selector", "min", "max"}, "all": {"rules"}, "any": {"rules"}}
    _require(type(op) is str and op in shapes, "acceptance rule: unknown operator")
    _shape(rule, {"id", "label", "op"} | shapes[op], "acceptance rule")
    _text(rule["id"], "rule.id")
    _text(rule["label"], "rule.label")
    _require(rule["id"] not in seen, f"duplicate acceptance rule id: {rule['id']}")
    seen.add(rule["id"])
    if op in ("equal_sets", "disjoint"):
        _selector_contract(rule["left"], state)
        _selector_contract(rule["right"], state)
    elif op == "count":
        _selector_contract(rule["selector"], state)
        minimum, maximum = rule["min"], rule["max"]
        _require(type(minimum) is int and minimum >= 0, "count.min: expected nonnegative integer")
        _require(maximum is None or (type(maximum) is int and maximum >= minimum),
                 "count.max: expected integer >= min or null")
    else:
        _require(type(rule["rules"]) is list and bool(rule["rules"]),
                 f"{op}.rules: expected nonempty list")
        for child in rule["rules"]:
            _rule_contract(child, state, seen, depth + 1)


def validate_contract(task, state):
    """Check optional contract shape; lost references remain valid contracts.

Support input/witness IDs and selector roots need not currently exist: their
loss must remain inspectable after mutation. Relation types in rule paths must
be declared. Missing optional fields mean empty groups/rules for legacy tasks.
"""
    groups = task.get("support_groups", [])
    _require(type(groups) is list, "support_groups: expected list")
    group_ids = set()
    for group in groups:
        _shape(group, {"id", "mode", "branches"}, "support group")
        _text(group["id"], "support group.id")
        _require("/" not in group["id"], "support group id cannot contain slash")
        _require(group["id"] not in group_ids, "duplicate support group id")
        group_ids.add(group["id"])
        _require(group["mode"] in ("all", "any"), "support group.mode: expected all/any")
        _require(type(group["branches"]) is list and bool(group["branches"]),
                 "support group.branches: expected nonempty list")
        branch_ids = set()
        for branch in group["branches"]:
            _shape(branch, {"id", "input_ids", "relation_ids"}, "support branch")
            _text(branch["id"], "support branch.id")
            _require("/" not in branch["id"], "support branch id cannot contain slash")
            _require(branch["id"] not in branch_ids, "duplicate support branch id")
            branch_ids.add(branch["id"])
            _ids(branch["input_ids"], "support branch.input_ids")
            _ids(branch["relation_ids"], "support branch.relation_ids")
            _require(branch["input_ids"] or branch["relation_ids"], "empty support branch")
    rules = task.get("acceptance_rules", [])
    _require(type(rules) is list, "acceptance_rules: expected list")
    seen = set()
    for rule in rules:
        _rule_contract(rule, state, seen)


def _branch_manifest(state, branch, root, hash_file, graph_snapshot_callable, producer_statuses):
    objects = {obj["id"]: obj for obj in state["objects"]}
    relations = {rel["id"]: rel for rel in state["relations"]}
    relation_types = {rel["id"]: rel for rel in state["ontology"]["relation_types"]}
    for object_id in branch["input_ids"]:
        _require(object_id in objects, f"support input missing: {object_id}")
    witnesses = []
    producer_inputs = set(branch["input_ids"])
    for relation_id in sorted(branch["relation_ids"]):
        _require(relation_id in relations, f"support witness missing: {relation_id}")
        relation = relations[relation_id]
        _require(relation["from"] in objects and relation["to"] in objects,
                 f"support witness endpoint missing: {relation_id}")
        _require(relation["type"] in relation_types, f"support witness type missing: {relation_id}")
        witnesses.append({"relation": copy.deepcopy(relation),
                          "relation_type": {key: copy.deepcopy(value) for key, value in
                                            relation_types[relation["type"]].items() if key != "label"}})
    graph = graph_snapshot_callable(state, branch["input_ids"], root, hash_file)
    _require(type(graph) is dict and type(graph.get("objects")) is list,
             "support graph callback must return manifest with objects list")
    producer_inputs.update(obj["id"] for obj in graph["objects"])
    for object_id in sorted(producer_inputs):
        if object_id in producer_statuses:
            _require(producer_statuses[object_id] == "current",
                     f"support producer not current: {object_id} ({producer_statuses[object_id]})")
    manifest = {"graph": copy.deepcopy(graph), "witnesses": witnesses}
    _canonical(manifest)  # Fail before accepting non-JSON/non-finite callback output.
    return manifest


def support_snapshot(state, task, root, hash_file, reviewed_supports,
                     graph_snapshot_callable, producer_statuses):
    """Capture only explicitly reviewed branches; never enroll an alternative.

Callback signature: (state, input_ids, root, hash_file) -> domain manifest with
objects[], optionally enriched with producer_generations. producer_statuses is
object_id -> current/pending/needs_review for produced objects; externals absent.
"""
    validate_contract(task, state)
    _ids(reviewed_supports, "reviewed_supports")
    selected = set(reviewed_supports)
    declared = {f"{group['id']}/{branch['id']}" for group in task.get("support_groups", [])
                for branch in group["branches"]}
    _require(selected <= declared, "reviewed_supports contains unknown branch")
    groups = []
    for group in task.get("support_groups", []):
        branches = []
        for branch in group["branches"]:
            identifier = f"{group['id']}/{branch['id']}"
            if identifier not in selected:
                continue
            manifest = _branch_manifest(state, branch, root, hash_file,
                                        graph_snapshot_callable, producer_statuses)
            branches.append({**copy.deepcopy(branch), "manifest": manifest, "lost": False})
        sufficient = len(branches) == len(group["branches"]) if group["mode"] == "all" else bool(branches)
        _require(sufficient, f"support group {group['id']}: insufficient explicitly reviewed branches")
        groups.append({"id": group["id"], "mode": group["mode"], "branches": branches})
    return {"groups": groups}


def inspect_supports(state, task, stored, root, hash_file, graph_snapshot_callable, producer_statuses):
    """Inspect accepted branches without mutating the stored acceptance.

Returned groups retain accepted branch manifests and updated lost flags, so the
caller can persist {groups: result['groups']} on its next authorized transaction.
The 'lost' list reports newly lost group/branch IDs only. An already lost branch
stays excluded even after restoration. Newly added alternatives are unreviewed.
"""
    validate_contract(task, state)
    _require(type(stored) is dict and type(stored.get("groups")) is list,
             "stored supports: expected groups list")
    previous = {group["id"]: group for group in stored["groups"]}
    groups, newly_lost = [], []
    current_group_ids = {group["id"] for group in task.get("support_groups", [])}
    for removed_id in sorted(previous.keys() - current_group_ids):
        removed = copy.deepcopy(previous[removed_id])
        for branch in removed["branches"]:
            if not branch.get("lost", False):
                newly_lost.append(f"{removed_id}/{branch['id']}")
            branch.update(lost=True, current=False, detail="support group removed; renewed acceptance required")
        removed.update(current=False, unreviewed=[])
        groups.append(removed)
    for group in task.get("support_groups", []):
        accepted = previous.get(group["id"], {})
        accepted_branches = {b["id"]: b for b in accepted.get("branches", [])}
        current_definitions = {b["id"]: b for b in group["branches"]}
        branches = []
        for branch_id, old in accepted_branches.items():
            branch = copy.deepcopy(old)
            identifier = f"{group['id']}/{branch_id}"
            definition = current_definitions.get(branch_id)
            detail = "reviewed support current"
            was_lost = old.get("lost", False)
            valid = not was_lost
            if was_lost:
                detail = "previously lost; explicit review required"
            elif accepted.get("mode") != group["mode"]:
                valid, detail = False, "support group mode changed"
            elif definition is None or any(old.get(key) != definition[key] for key in ("input_ids", "relation_ids")):
                valid, detail = False, "support branch bindings changed or removed"
            else:
                try:
                    current = _branch_manifest(state, definition, root, hash_file,
                                               graph_snapshot_callable, producer_statuses)
                    valid = _canonical(current) == _canonical(old.get("manifest"))
                    if not valid:
                        detail = "reviewed support manifest changed"
                except (ValueError, OSError) as exc:
                    valid, detail = False, str(exc)
            branch.update(lost=not valid, current=valid, detail=detail)
            if not valid and not was_lost:
                newly_lost.append(identifier)
            branches.append(branch)
        valid_ids = {branch["id"] for branch in branches if branch["current"]}
        if group["mode"] == "all":
            current = valid_ids == set(current_definitions)
        else:
            current = bool(valid_ids & set(current_definitions))
        groups.append({"id": group["id"], "mode": group["mode"], "branches": branches,
                       "current": current, "unreviewed": sorted(set(current_definitions) - set(accepted_branches))})
    return {"current": all(group["current"] for group in groups), "groups": groups,
            "lost": sorted(newly_lost)}


def _select(state, selector):
    objects = {obj["id"]: obj for obj in state["objects"]}
    selected = set(selector["roots"])
    missing = selected - objects.keys()
    _require(not missing, f"selector roots missing: {', '.join(sorted(missing))}")
    for step in selector["path"]:
        source, target = ("from", "to") if step["direction"] == "out" else ("to", "from")
        selected = {rel[target] for rel in state["relations"]
                    if rel["type"] == step["relation_type"] and rel[source] in selected}
        _require(selected <= objects.keys(), "selector encountered missing relation endpoint")
    if selector["property"] is None:
        return sorted(selected)
    values = []
    for identifier in sorted(selected):
        properties = objects[identifier]["properties"]
        _require(selector["property"] in properties,
                 f"selector property missing: {identifier}.{selector['property']}")
        values.append(properties[selector["property"]])
    return values


def _evaluate(state, rule):
    op = rule["op"]
    try:
        if op in ("all", "any"):
            results = [_evaluate(state, child) for child in rule["rules"]]
            passed = (all if op == "all" else any)(result["passed"] for result in results)
            detail = "; ".join(f"{r['id']}: {'passed' if r['passed'] else 'failed'} ({r['detail']})"
                               for r in results)
        elif op == "count":
            values = _select(state, rule["selector"])
            count = len(values)
            passed = count >= rule["min"] and (rule["max"] is None or count <= rule["max"])
            detail = f"selected count {count}; expected {rule['min']}..{rule['max'] if rule['max'] is not None else '*'}"
        else:
            left_values, right_values = _select(state, rule["left"]), _select(state, rule["right"])
            left, right = {_canonical(v) for v in left_values}, {_canonical(v) for v in right_values}
            passed = left == right if op == "equal_sets" else left.isdisjoint(right)
            detail = f"left={_canonical(left_values)}; right={_canonical(right_values)}"
    except (ValueError, KeyError, TypeError) as exc:
        passed, detail = False, str(exc)
    return {"id": rule["id"], "label": rule["label"], "passed": passed, "detail": detail}


def evaluate_rules(state, rules):
    """Evaluate safe rule trees. Count counts selected nodes/property values.

equal_sets/disjoint compare canonical JSON values as sets; count preserves one
property value per selected object, even if two objects have equal values.
Empty set equality/disjointness follow ordinary set semantics; use a count rule
when nonemptiness is required. Missing roots/properties fail with an explanation.
"""
    validate_contract({"acceptance_rules": rules}, state)
    return [_evaluate(state, rule) for rule in rules]


def _observe_selector(state, selector):
    """Record exact finite traversal, including absence, without resolving truth."""
    objects = {obj["id"]: obj for obj in state["objects"]}
    definitions = {rel["id"]: rel for rel in state["ontology"]["relation_types"]}
    selected = set(selector["roots"])
    missing_roots = sorted(selected - objects.keys())
    selected &= objects.keys()
    steps = []
    for step in selector["path"]:
        source, target = ("from", "to") if step["direction"] == "out" else ("to", "from")
        traversed = sorted([rel for rel in state["relations"]
                            if rel["type"] == step["relation_type"] and rel[source] in selected],
                           key=lambda rel: rel["id"])
        reached = {rel[target] for rel in traversed}
        steps.append({"step": copy.deepcopy(step), "from_ids": sorted(selected),
                      "to_ids": sorted(reached), "relations": copy.deepcopy(traversed),
                      "relation_type": {key: copy.deepcopy(value) for key, value in
                                        definitions[step["relation_type"]].items() if key != "label"}})
        selected = reached
    values, missing_properties = [], []
    for identifier in sorted(selected):
        if selector["property"] is None:
            values.append({"object_id": identifier, "value": identifier})
        elif identifier not in objects or selector["property"] not in objects[identifier]["properties"]:
            missing_properties.append({"object_id": identifier, "property": selector["property"]})
        else:
            obj = objects[identifier]
            kind = next(t for t in state["ontology"]["object_types"] if t["id"] == obj["type"])
            values.append({"object_id": identifier,
                           "value": copy.deepcopy(obj["properties"][selector["property"]]),
                           "property_definition": copy.deepcopy(kind["properties"].get(selector["property"]))})
    normalized = copy.deepcopy(selector)
    normalized["roots"] = sorted(normalized["roots"])
    return {"selector": normalized, "steps": steps, "selected_ids": sorted(selected),
            "values": values, "missing_roots": missing_roots, "missing_properties": missing_properties}


def observe_rules(state, rules):
    """Return detached JSON provenance for rules, separate from task run inputs.

Includes selector definitions, every traversed witness, selected object IDs,
selected property values/definitions, and empty/missing selections. Thus a rule
may remain true yet its old acceptance becomes stale when its witnesses change.
Rule/object/type display labels are excluded; identity and semantics remain.
"""
    validate_contract({"acceptance_rules": rules}, state)

    def observe(rule):
        result = {"id": rule["id"], "op": rule["op"]}
        if rule["op"] in ("all", "any"):
            result["rules"] = [observe(child) for child in rule["rules"]]
        elif rule["op"] == "count":
            result.update(min=rule["min"], max=rule["max"],
                          selector=_observe_selector(state, rule["selector"]))
        else:
            result.update(left=_observe_selector(state, rule["left"]),
                          right=_observe_selector(state, rule["right"]))
        return result

    return {"rules": [observe(rule) for rule in rules]}
