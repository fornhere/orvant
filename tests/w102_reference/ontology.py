"""Typed project graph validation and deterministic dependency analysis.

This is a deliberately small JSON graph contract, not a SHACL implementation.
An impact arrow points from an input to its dependent. Relation traversal and
impact are separate concepts: ``none`` links do not enter mandatory dependency
fingerprints, but may be explicit support witnesses or rule paths. Display
labels are excluded; callers may select only computation-relevant properties.

There is no task logic, persistence, permission system, or arbitrary rule
execution here. Callers own revision checks, transactions, and file access.
"""
from __future__ import annotations

from collections import deque
import copy
import hashlib
import json
import math
from pathlib import PurePosixPath, PureWindowsPath


PROPERTY_TYPES = {"string", "integer", "number", "boolean", "string_list", "file", "json"}
IMPACT_DIRECTIONS = {"forward", "reverse", "both", "none"}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _shape(value, fields, label):
    _require(type(value) is dict, f"{label}: expected object")
    _require(set(value) == set(fields), f"{label}: unexpected or missing fields")


def _text(value, label):
    _require(type(value) is str and bool(value.strip()), f"{label}: expected nonempty string")


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False)


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
            _require(type(key) is str, f"{label}: JSON object keys must be strings")
            _json_value(item, label)
        return
    raise ValueError(f"{label}: expected JSON value")


def _file_path(value, label):
    _text(value, label)
    posix, windows = PurePosixPath(value), PureWindowsPath(value)
    _require(not posix.is_absolute() and not windows.is_absolute()
             and not windows.drive and "\\" not in value and "\x00" not in value
             and posix.parts and ".." not in posix.parts
             and ".project" not in posix.parts,
             f"{label}: expected safe relative file path outside .project")


def _property_value(value, kind, label):
    if kind == "string":
        _require(type(value) is str, f"{label}: expected string")
    elif kind == "integer":
        _require(type(value) is int, f"{label}: expected integer")
    elif kind == "number":
        _require(type(value) in (int, float), f"{label}: expected number")
        if type(value) is float:
            _require(math.isfinite(value), f"{label}: expected finite number")
    elif kind == "boolean":
        _require(type(value) is bool, f"{label}: expected boolean")
    elif kind == "string_list":
        _require(type(value) is list and all(type(item) is str for item in value),
                 f"{label}: expected string_list")
    elif kind == "file":
        _file_path(value, label)
    elif kind == "json":
        _json_value(value, label)
    else:
        raise ValueError(f"{label}: unknown property type {kind!r}")


def _index(items, label):
    _require(type(items) is list, f"{label}: expected array")
    indexed = {}
    for item in items:
        _require(type(item) is dict and "id" in item, f"{label}: missing id")
        _text(item["id"], f"{label}.id")
        _require(item["id"] not in indexed, f"{label}: duplicate id {item['id']}")
        indexed[item["id"]] = item
    return indexed


def _validate_graph(state):
    _require(type(state) is dict, "graph state: expected object")
    ontology = state["ontology"]
    _shape(ontology, {"object_types", "relation_types"}, "ontology")
    object_types = _index(ontology["object_types"], "ontology.object_types")
    relation_types = _index(ontology["relation_types"], "ontology.relation_types")
    objects = _index(state["objects"], "objects")
    relations = _index(state["relations"], "relations")

    for type_id, definition in object_types.items():
        label = f"object type {type_id}"
        _shape(definition, {"id", "label", "properties"} | ({"immutable"} if "immutable" in definition else set()), label)
        if "immutable" in definition:
            _require(type(definition["immutable"]) is bool, f"{label}.immutable: expected boolean")
        _text(definition["label"], f"{label}.label")
        _require(type(definition["properties"]) is dict, f"{label}.properties: expected object")
        for name, prop in definition["properties"].items():
            _text(name, f"{label}: property name")
            prop_label = f"{label}.properties.{name}"
            _shape(prop, {"type", "required", "enum"}, prop_label)
            _text(prop["type"], f"{prop_label}.type")
            _require(prop["type"] in PROPERTY_TYPES, f"{prop_label}: unknown property type")
            _require(type(prop["required"]) is bool, f"{prop_label}.required: expected boolean")
            choices = prop["enum"]
            if choices is not None:
                _require(type(choices) is list, f"{prop_label}.enum: expected array or null")
                seen = set()
                for value in choices:
                    _property_value(value, prop["type"], f"{prop_label}.enum")
                    serialized = _canonical(value)
                    _require(serialized not in seen, f"{prop_label}.enum: duplicate value")
                    seen.add(serialized)

    for type_id, definition in relation_types.items():
        label = f"relation type {type_id}"
        _shape(definition, {"id", "label", "from_type", "to_type", "from_min", "from_max",
                            "to_min", "to_max", "impact"} | ({"immutable"} if "immutable" in definition else set()), label)
        if "immutable" in definition:
            _require(type(definition["immutable"]) is bool, f"{label}.immutable: expected boolean")
        _text(definition["label"], f"{label}.label")
        for side in ("from", "to"):
            endpoint = definition[f"{side}_type"]
            _text(endpoint, f"{label}.{side}_type")
            _require(endpoint in object_types, f"{label}.{side}_type: unknown object type {endpoint}")
            minimum, maximum = definition[f"{side}_min"], definition[f"{side}_max"]
            _require(type(minimum) is int and minimum >= 0,
                     f"{label}.{side}_min: expected nonnegative integer")
            _require(maximum is None or (type(maximum) is int and maximum >= minimum),
                     f"{label}.{side}_max: expected integer >= minimum or null")
        _text(definition["impact"], f"{label}.impact")
        _require(definition["impact"] in IMPACT_DIRECTIONS, f"{label}: unknown impact direction")

    for object_id, obj in objects.items():
        label = f"object {object_id}"
        _shape(obj, {"id", "type", "label", "properties"}, label)
        _text(obj["type"], f"{label}.type")
        _text(obj["label"], f"{label}.label")
        _require(obj["type"] in object_types, f"{label}: undeclared type {obj['type']}")
        _require(type(obj["properties"]) is dict, f"{label}.properties: expected object")
        props = object_types[obj["type"]]["properties"]
        _require(set(obj["properties"]) <= set(props), f"{label}: undeclared properties")
        for name, definition in props.items():
            present = name in obj["properties"]
            _require(present or not definition["required"], f"{label}.{name}: required property missing")
            if not present:
                continue
            value = obj["properties"][name]
            _property_value(value, definition["type"], f"{label}.{name}")
            if definition["enum"] is not None:
                allowed = {_canonical(choice) for choice in definition["enum"]}
                _require(_canonical(value) in allowed, f"{label}.{name}: value outside enum")

    counts = {type_id: {"from": {}, "to": {}} for type_id in relation_types}
    triples = set()
    for relation_id, relation in relations.items():
        label = f"relation {relation_id}"
        _shape(relation, {"id", "from", "type", "to"}, label)
        for field in ("from", "type", "to"):
            _text(relation[field], f"{label}.{field}")
        _require(relation["type"] in relation_types, f"{label}: undeclared relation type {relation['type']}")
        definition = relation_types[relation["type"]]
        for side in ("from", "to"):
            endpoint = relation[side]
            _require(endpoint in objects, f"{label}.{side}: unknown object {endpoint}")
            _require(objects[endpoint]["type"] == definition[f"{side}_type"],
                     f"{label}.{side}: expected {definition[f'{side}_type']}, "
                     f"got {objects[endpoint]['type']}")
            side_counts = counts[relation["type"]][side]
            side_counts[endpoint] = side_counts.get(endpoint, 0) + 1
        triple = (relation["from"], relation["type"], relation["to"])
        _require(triple not in triples, f"{label}: duplicate relation triple")
        triples.add(triple)

    # Zero-edge instances must be checked too; counting only existing edges
    # would silently bypass required relations.
    for type_id, definition in relation_types.items():
        for side in ("from", "to"):
            for obj in objects.values():
                if obj["type"] != definition[f"{side}_type"]:
                    continue
                count = counts[type_id][side].get(obj["id"], 0)
                minimum, maximum = definition[f"{side}_min"], definition[f"{side}_max"]
                _require(count >= minimum and (maximum is None or count <= maximum),
                         f"object {obj['id']}: relation {type_id}.{side} cardinality {count}; "
                         f"expected {minimum}..{maximum if maximum is not None else '*'}")


def validate_graph(state):
    """Validate graph schema and instances, without filesystem access.

``from_min/max`` count outgoing instances of that relation for EACH source
object; ``to_min/max`` count incoming instances for EACH target object. Object
graph cycles and self-relations are permitted when endpoint types allow them.
"""
    try:
        _validate_graph(state)
    except (TypeError, KeyError, AttributeError, RecursionError, OverflowError) as exc:
        raise ValueError(f"malformed ontology graph: {exc}") from exc


def _arcs(state):
    """Yield (input, dependent, relation_id) in stable order."""
    types = {item["id"]: item for item in state["ontology"]["relation_types"]}
    for relation in sorted(state["relations"], key=lambda item: item["id"]):
        direction = types[relation["type"]]["impact"]
        if direction in {"forward", "both"}:
            yield relation["from"], relation["to"], relation["id"]
        if direction in {"reverse", "both"}:
            yield relation["to"], relation["from"], relation["id"]


def _input_ids(state, input_ids):
    _require(isinstance(input_ids, (list, tuple, set, frozenset)), "input_ids: expected collection")
    available = {obj["id"] for obj in state["objects"]}
    result = set()
    for object_id in input_ids:
        _text(object_id, "input_ids")
        _require(object_id in available, f"input_ids: unknown object {object_id}")
        result.add(object_id)
    return result


def _upstream(state, inputs):
    incoming = {}
    for source, dependent, _ in _arcs(state):
        incoming.setdefault(dependent, set()).add(source)
    visited = set(inputs)
    queue = deque(sorted(inputs))
    while queue:
        node = queue.popleft()
        for source in sorted(incoming.get(node, ())):
            if source not in visited:
                visited.add(source)
                queue.append(source)
    return visited


def upstream(state, input_ids):
    """Return bound inputs and all declared upstream dependencies, cycle-safe."""
    validate_graph(state)
    return _upstream(state, _input_ids(state, input_ids))


def _by_id(items):
    return {item["id"]: item for item in items}


def _different_ids(before, after):
    # Canonical comparison distinguishes bool from int and detects changes
    # in arbitrary JSON values without depending on dictionary order.
    return sorted(key for key in before.keys() | after.keys()
                  if key not in before or key not in after
                  or _canonical(before[key]) != _canonical(after[key]))


def impact(before, after):
    """Explain conservative change impact over the union of old and new arcs.

Link changes seed their dependent endpoint only. ``none`` links have no
semantic effect. Changes to an impactful relation TYPE affect instances of
both endpoint types, including instances without links, since their schema
contract changed. A change of label counts conservatively as a change.

``paths`` maps each affected object id to a list beginning with a seed reason,
followed by impact hops. One deterministic shortest discovered path is enough
to explain why an object needs review; it is not an exhaustive proof graph.
"""
    validate_graph(before)
    validate_graph(after)
    old_objects, new_objects = _by_id(before["objects"]), _by_id(after["objects"])
    old_relations, new_relations = _by_id(before["relations"]), _by_id(after["relations"])
    changed_objects = _different_ids(old_objects, new_objects)
    changed_relations = _different_ids(old_relations, new_relations)
    seeds = {key: [f"object changed: {key}"] for key in changed_objects}

    for collection in ("object_types", "relation_types"):
        old_types = _by_id(before["ontology"][collection])
        new_types = _by_id(after["ontology"][collection])
        for type_id in _different_ids(old_types, new_types):
            for state, definitions in ((before, old_types), (after, new_types)):
                definition = definitions.get(type_id)
                if definition is None:
                    continue
                if collection == "object_types":
                    relevant_types = {type_id}
                elif definition["impact"] != "none":
                    relevant_types = {definition["from_type"], definition["to_type"]}
                else:
                    continue
                for obj in sorted(state["objects"], key=lambda item: item["id"]):
                    if obj["type"] in relevant_types:
                        seeds.setdefault(obj["id"], [f"schema changed: {collection}.{type_id}"])

    arcs = sorted(set(_arcs(before)) | set(_arcs(after)))
    changed_relation_ids = set(changed_relations)
    for source, dependent, relation_id in arcs:
        if relation_id in changed_relation_ids:
            seeds.setdefault(dependent,
                             [f"relation changed: {relation_id} ({source} -> {dependent})"])

    outgoing = {}
    for source, dependent, relation_id in arcs:
        outgoing.setdefault(source, []).append((dependent, relation_id))
    paths = dict(sorted(seeds.items()))
    queue = deque(paths)
    while queue:
        source = queue.popleft()
        for dependent, relation_id in outgoing.get(source, ()):
            if dependent not in paths:
                paths[dependent] = paths[source] + [f"{source} --{relation_id}--> {dependent}"]
                queue.append(dependent)
    return {"changed_objects": changed_objects, "changed_relations": changed_relations,
            "affected_objects": sorted(paths), "paths": dict(sorted(paths.items()))}


def snapshot(state, input_ids, root, hash_file, projections=None, *, direct=False):
    """Hash the bound inputs, upstream graph, applicable schema and file content.

Only impact-bearing relations pointing INTO the upstream closure are included;
an outgoing relation to a downstream object cannot invalidate its source task.
Definitions of impact-bearing relation types incident to an included OBJECT
TYPE are also included, because changing their contract can affect that type
even when no relation instance exists. Display-only relation types are omitted.

``hash_file(root, relative_path)`` is supplied by the caller and must enforce
project containment, symlink policy and file readability. Missing files must
raise rather than silently return a fingerprint for incomplete evidence.
"""
    validate_graph(state)
    inputs = _input_ids(state, input_ids)
    included = set(inputs) if direct else _upstream(state, inputs)
    object_types = _by_id(state["ontology"]["object_types"])
    objects = [copy.deepcopy(obj) for obj in sorted(state["objects"], key=lambda item: item["id"])
               if obj["id"] in included]
    projections = projections or {}
    selected_by_type = {}
    for obj in objects:
        obj.pop("label", None)  # Display labels are not computation inputs.
        if obj["id"] in projections:
            obj["properties"] = {key: value for key, value in obj["properties"].items()
                                 if key in projections[obj["id"]]}
        selected_by_type.setdefault(obj["type"], set()).update(obj["properties"])
    included_types = {obj["type"] for obj in objects}
    included_relations = {relation_id for _, dependent, relation_id in _arcs(state)
                          if dependent in included}
    files = []
    for obj in objects:
        definitions = object_types[obj["type"]]["properties"]
        for name, value in sorted(obj["properties"].items()):
            if definitions[name]["type"] == "file":
                digest = hash_file(root, value)
                _text(digest, f"file hash for {obj['id']}.{name}")
                files.append({"object_id": obj["id"], "property": name,
                              "path": value, "sha256": digest})
    payload = {
        "inputs": sorted(inputs),
        "objects": objects,
        "object_types": [{"id": key, "properties": {field: definition for field, definition in
                           object_types[key]["properties"].items() if field in selected_by_type[key]}}
                         for key in sorted(included_types)],
        "relation_types": [{key: value for key, value in definition.items() if key != "label"} for definition in
                           sorted(state["ontology"]["relation_types"], key=lambda item: item["id"])
                           if definition["impact"] != "none"
                           and (definition["from_type"] in included_types
                                or definition["to_type"] in included_types)],
        "relations": [relation for relation in
                      sorted(state["relations"], key=lambda item: item["id"])
                      if relation["id"] in included_relations],
        "files": files,
    }
    return copy.deepcopy(payload)


def fingerprint(state, input_ids, root, hash_file):
    """Compact digest of the inspectable dependency manifest."""
    return hashlib.sha256(_canonical(snapshot(state, input_ids, root, hash_file)).encode("utf-8")).hexdigest()
