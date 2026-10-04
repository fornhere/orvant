"""Deterministic read-only work proposals and single-writer lane planning.

Ontology type names have no built-in workflow semantics. An object can expose
properties.acceptance (string/list of strings), or satisfied=false with a
properties.definition string. These are explicit criteria, never inferred from
labels. Source paths come from typed file properties or properties.source.path.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import PurePosixPath

import core

SINGLE_WRITER = ('Tek yazıcı: kayıt durumunu yalnız koordinatör preview → apply '
                 '--expected-revision ile yazar; şeritler kendi önerilerini ve kanıtlarını '
                 'döndürür. Şerit içi işler önkoşul sırasıyla yürür; serial şeritler bütün '
                 'diğer şeritler durduğunda çalışır.')


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _paths(state, obj):
    definitions = next((t['properties'] for t in state.get('ontology', {}).get('object_types', [])
                        if t['id'] == obj['type']), {})
    paths = [value for key, value in obj['properties'].items()
             if definitions.get(key, {}).get('type') == 'file']
    source = obj['properties'].get('source')
    if isinstance(source, dict) and isinstance(source.get('path'), str):
        # JSON source properties aren't otherwise validated as filesystem paths.
        core._relative_path(source['path'])
        paths.append(source['path'])
    return sorted(set(paths))


def _inputs(state, ids):
    objects = {o['id']: o for o in state['objects']}
    return [{'object_id': key, 'paths': _paths(state, objects[key])}
            for key in sorted(set(ids)) if key in objects]


def _file_changes(state, task, root):
    changes = []
    seen = set()
    stored = list(task['evidence'])
    for field in ('input_snapshot', 'output_snapshot', 'run_snapshot'):
        snapshot = task.get(field)
        if snapshot:
            manifest = snapshot['manifest']
            stored.extend(manifest.get('graph', manifest).get('files', []))
    for item in stored:
        identity = (item['path'], item['sha256'])
        if identity in seen:
            continue
        seen.add(identity)
        try:
            current = core._hash_file(root, item['path'])
            error = None
        except ValueError as exc:
            current, error = None, str(exc)
        if current != item['sha256']:
            changes.append({'object_id': item.get('object_id'), 'path': item['path'],
                            'before_sha256': item['sha256'], 'after_sha256': current,
                            'error': error})
    return sorted(changes, key=lambda c: (c['path'], c['before_sha256']))


def _domain_changes(state, task, root):
    # Open work already covers renewed review. Only the current acceptance is
    # a baseline; historical snapshots belong to earlier acceptance cycles.
    if task['status'] != 'done':
        return []
    changes = []
    for field, callback in (('input_snapshot', core._input_snapshot),
                            ('output_snapshot', core._output_snapshot)):
        before = task.get(field)
        if before is None:
            continue
        try:
            after = callback(state, task, root)
        except ValueError as exc:
            after = None
            error = str(exc)
        else:
            error = None
        if after != before:
            changes.append({'snapshot': field, 'before_sha256': before['sha256'],
                            'after_sha256': after['sha256'] if after else None, 'error': error})
            old = before['manifest'].get('graph', before['manifest'])
            new = after['manifest'].get('graph', after['manifest']) if after else {}
            for collection in ('objects', 'relations'):
                old_items = {o['id']: o for o in old.get(collection, [])}
                new_items = {o['id']: o for o in new.get(collection, [])}
                for key in sorted(old_items.keys() | new_items.keys()):
                    if old_items.get(key) != new_items.get(key):
                        changes.append({'collection': collection, 'object_id': key,
                                        'before_sha256': _hash(old_items.get(key)),
                                        'after_sha256': _hash(new_items.get(key))})
    return changes


def _criteria(obj):
    value = obj['properties'].get('acceptance')
    if isinstance(value, str):
        value = [value]
    if isinstance(value, list) and value and all(isinstance(v, str) and v.strip() for v in value):
        return list(value)
    if obj['properties'].get('satisfied') is False:
        definition = obj['properties'].get('definition')
        if isinstance(definition, str) and definition.strip():
            return [definition]
    return []


def _new_proposal(state, kind, ids, criteria, because, rules=None):
    identifier = 'derived-' + _hash({'kind': kind, 'ids': sorted(ids), 'criteria': criteria,
                                    'rules': rules or []})[:20]
    tasks = {t['id']: t for t in state['tasks']}
    base, number = identifier, 1
    while identifier in tasks:
        if tasks[identifier]['status'] != 'cancelled':
            return None
        number += 1
        identifier = base + '-' + str(number)
    title = ('İncele: ' if kind == 'inspect' else 'Doğrula: ') + ', '.join(sorted(ids))
    task = {'id': identifier, 'title': title, 'status': 'todo', 'object_ids': sorted(ids),
            'depends_on': [], 'decision_ids': [], 'acceptance': criteria, 'evidence': []}
    if state['schema_version'] == 3:
        task.update(input_ids=sorted(ids), output_ids=[], input_snapshot=None,
                    generation=0, review_reasons=[])
        if rules:
            task['acceptance_rules'] = copy.deepcopy(rules)
    return {'kind': kind, 'task_id': identifier, 'inputs': _inputs(state, ids),
            'expected_outputs': ['Kayıtlı kabul ölçütlerine bağlı inceleme kanıtı.'],
            'acceptance': list(criteria), 'because': because,
            'event': {'action': 'extend_model', 'actor': 'derive',
                      'reason': title, 'objects': [], 'relations': [], 'tasks': [task]}}


def derive(state, root):
    """Compute from the same live report as context, without applying events."""
    report = core.inspect_state(state, root)
    views = {t['id']: t for t in report['tasks']}
    proposals, skipped = [], []
    open_tasks = [t for t in state['tasks'] if t['status'] not in ('done', 'cancelled')
                  or views[t['id']]['effective_status'] == 'needs_review']
    covered = {key for t in open_tasks for key in t['object_ids']}
    for task in sorted(state['tasks'], key=lambda t: t['id']):
        view = views[task['id']]
        if task['status'] == 'cancelled':
            continue
        changes = _file_changes(state, task, root) + _domain_changes(state, task, root)
        reasons = view['issues'] + task.get('review_reasons', [])
        stale = bool(changes or task.get('review_reasons') or
                     (task.get('generation', 0) and view['effective_status'] == 'needs_review') or
                     (task.get('input_snapshot') and view['issues']))
        if task['status'] == 'done' and stale:
            indices = sorted({e['criterion'] for e in task['evidence']
                              if any(c.get('path') == e['path'] for c in changes)})
            # Domain/snapshot drift invalidates the task acceptance as a whole.
            if not indices or any(c.get('object_id') or c.get('snapshot') for c in changes) or task.get('review_reasons'):
                indices = list(range(len(task['acceptance'])))
            proposals.append({'kind': 'revalidate', 'task_id': task['id'],
                'inputs': _inputs(state, task['object_ids']),
                'expected_outputs': ['Mevcut görev için yenilenmiş inceleme kanıtı.'],
                'acceptance': list(task['acceptance']),
                'because': {'task_id': task['id'], 'changes': changes, 'issues': reasons,
                            'criterion_indices': indices},
                'event': {'action': 'reopen_task',
                          'actor': 'derive', 'reason': 'Kayıtlı kabulü yeniden doğrula: ' + task['id'],
                          'task_id': task['id']}})
        # Failed task rules are already covered by an open/revalidation task.
        # Only a completed, current task can need a new rule-specific proposal.
        failed = [r for r in view.get('acceptance_rule_results', []) if not r['passed']]
        if failed and task not in open_tasks:
            rules = [r for r in task.get('acceptance_rules', []) if r['id'] in {f['id'] for f in failed}]
            criteria = [r['label'] for r in rules]
            if criteria:
                p = _new_proposal(state, 'acceptance', task['object_ids'], criteria,
                    {'task_id': task['id'], 'rule_results': failed, 'rules': copy.deepcopy(rules)}, rules)
                if p:
                    proposals.append(p)
    for obj in sorted(state['objects'], key=lambda o: o['id']):
        criteria = _criteria(obj)
        if not criteria or obj['properties'].get('satisfied') is True:
            continue
        # Object identity alone is not acceptance coverage. Unrelated work on
        # the same scenario must not hide a contract without matching evidence.
        if any(obj['id'] in t['object_ids'] and set(criteria) <= set(t['acceptance'])
               and (t in open_tasks or views[t['id']]['effective_status'] == 'done')
               for t in state['tasks']):
            continue
        p = _new_proposal(state, 'acceptance', [obj['id']], criteria,
                          {'object_id': obj['id'], 'object_sha256': _hash(obj),
                           'acceptance': list(criteria), 'reason': 'Kayıtlı ölçüt için güncel kanıt yok.'})
        if p:
            proposals.append(p)
    # History preserves the original impact, so no fabricated previous snapshot.
    entry = next((e for e in reversed(state['history']) if 'impact' in e.get('change', {}) and
                  (e['action'] == 'mutate_graph' or e['change'].get('objects') or
                   e['change'].get('relations'))), None)
    if entry is None:
        skipped.append({'category': 'impact', 'reason': 'Kayıtta önceki değişime ait etki/anlık görüntü yok.'})
    else:
        impact = entry['change']['impact']
        proposed_objects = {p['because'].get('object_id') for p in proposals}
        existing = {o['id'] for o in state['objects']}
        for key in sorted(set(impact['affected_objects']) & existing - covered - proposed_objects):
            path = impact.get('paths', {}).get(key, [])
            if not path:
                continue
            p = _new_proposal(state, 'inspect', [key], ['Kayıtlı değişim etkisini incele: ' + '; '.join(path)],
                              {'object_id': key, 'revision': entry['revision'], 'impact_path': path})
            if p:
                proposals.append(p)
    # Guarantee that emitted events are accepted by the existing preview engine.
    valid = []
    for proposal in proposals:
        try:
            core.preview_event(state, proposal['event'], root)
        except ValueError as exc:
            skipped.append({'category': proposal['kind'], 'task_id': proposal['task_id'],
                            'reason': str(exc)})
        else:
            valid.append(proposal)
    return {'revision': state['revision'], 'proposals': valid, 'skipped': skipped,
            'state_committed': False, 'single_writer': SINGLE_WRITER}


def _scope(state, task):
    inputs = _inputs(state, task['object_ids'])
    paths = sorted({path for item in inputs for path in item['paths']})
    outputs = set(task.get('output_ids', []))
    unknown = not paths or any(not item['paths'] for item in inputs if item['object_id'] in outputs)
    return paths, unknown


def _overlap(left, right):
    return any(a == b or PurePosixPath(a) in PurePosixPath(b).parents or
               PurePosixPath(b) in PurePosixPath(a).parents for a in left for b in right)


def lanes(state, root, maximum=None):
    """Connected conflict components; blocked descendants queue within a lane."""
    if maximum is not None and maximum < 1:
        raise ValueError('--max must be positive')
    report = core.inspect_state(state, root)
    views = {t['id']: t for t in report['tasks']}
    active = {t['id']: t for t in state['tasks'] if t['status'] not in ('done', 'cancelled')}
    scopes = {key: _scope(state, task) for key, task in active.items()}
    adjacency = {key: set() for key in active}
    edges = []
    # Traverse through completed prerequisites too: pending tasks on one chain
    # must not be scheduled concurrently merely because an intermediate is done.
    def ancestors(key):
        seen, pending = set(), list(views[key].get('effective_depends_on', views[key]['depends_on']))
        while pending:
            item = pending.pop()
            if item not in seen:
                seen.add(item)
                pending.extend(views[item].get('effective_depends_on', views[item]['depends_on']))
        return seen
    predecessors = {key: ancestors(key) for key in active}
    for i, left in enumerate(sorted(active)):
        for right in sorted(active)[i + 1:]:
            reasons = []
            if _overlap(scopes[left][0], scopes[right][0]):
                reasons.append('write_scope_overlap')
            if left in predecessors[right] or right in predecessors[left]:
                reasons.append('prerequisite_chain')
            # Schema-3 task links only accepted/superseded decisions. A proposed
            # replacement of the same topic makes an accepted binding open.
            # Rejected/superseded history is closed; unknown statuses stay open.
            topics = {d['topic'] for d in state['decisions'] if d['status'] == 'proposed'}
            open_ids = {d['id'] for d in state['decisions']
                        if d['status'] not in ('accepted', 'rejected', 'superseded')
                        or (d['status'] == 'accepted' and d['topic'] in topics)}
            shared = set(active[left]['decision_ids']) & set(active[right]['decision_ids']) & open_ids
            if shared:
                reasons.append('shared_open_decision: ' + ', '.join(sorted(shared)))
            if reasons:
                adjacency[left].add(right)
                adjacency[right].add(left)
                edges.append({'tasks': [left, right], 'reasons': reasons})
    result, deferred, seen = [], [], set()
    ready = set(report['ready'])
    for key in sorted(active):
        if key in seen:
            continue
        component, pending = set(), [key]
        while pending:
            item = pending.pop()
            if item not in component:
                component.add(item)
                pending.extend(adjacency[item] - component)
        seen.update(component)
        unknown = sorted(t for t in component if scopes[t][1])
        lane = {'id': 'lane-' + _hash(sorted(component))[:12],
                'mode': 'serial' if unknown else 'parallel',
                'tasks': sorted(component & ready), 'queued_tasks': sorted(component - ready),
                'write_scope': sorted({p for t in component for p in scopes[t][0]}),
                'reasons': [e for e in edges if set(e['tasks']) <= component],
                'separation_reason': 'Diğer bileşenlerle bilinen yazma/önkoşul/açık karar kesişimi yok.'}
        if unknown:
            lane['reasons'].append({'tasks': unknown, 'reasons': ['write_scope_unknown; exclusive serial execution']})
        if lane['tasks'] and (maximum is None or len(result) < maximum):
            result.append(lane)
        else:
            lane['queue_reason'] = 'max_lanes' if lane['tasks'] else 'not_ready'
            deferred.append(lane)
    return {'revision': state['revision'], 'lanes': result, 'queued_lanes': deferred,
            'state_committed': False, 'single_writer': SINGLE_WRITER}
