"""Deterministic synthetic schema-3 records; no real project data."""
import copy
import importlib.util
from pathlib import Path
import sys
from contextlib import contextmanager

from ontology_fixtures import build_notebook, prop, relation_type, task, event

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/orvant/scripts'
REFERENCE = Path(__file__).resolve().parent / 'w102_reference'


@contextmanager
def runtime(reference=False):
    """Load isolated old/new modules, including their lazy imports."""
    names = ('ontology', 'acceptance', 'core', 'derive', 'project')
    previous = {name: sys.modules.get(name) for name in names}
    folder = REFERENCE if reference else SCRIPTS
    modules = {}
    try:
        for name in names:
            spec = importlib.util.spec_from_file_location(name, folder / (name + '.py'))
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            modules[name] = module
        yield modules
    finally:
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def large_record(root, modules, count=2000, task_count=50):
    """Two edges/module within bounded components, plus accepted artifact tasks."""
    core, ontology = modules['core'], modules['ontology']
    state = build_notebook()
    state.update(objects=[], relations=[], tasks=[], decisions=[], history=[])
    state['ontology'] = {
        'object_types': [
            {'id': 'Module', 'label': 'Synthetic module', 'properties': {
                'path': prop('file'), 'version': prop('integer')}},
            {'id': 'Artifact', 'label': 'Synthetic artifact', 'properties': {'path': prop('file')}}],
        'relation_types': [relation_type('imports', 'Module', 'Module', 'forward')]}
    width = count // task_count
    for i in range(count):
        path = f'module-{i}.py'
        (root / path).write_text(f'# synthetic module {i}\n', encoding='utf-8')
        state['objects'].append({'id': f'M{i}', 'type': 'Module', 'label': f'Module {i}',
                                'properties': {'path': path, 'version': 0}})
        start = i // width * width
        for step in (1, 2):
            target = start + (i - start + step) % width
            state['relations'].append({'id': f'R{i}-{step}', 'from': f'M{i}',
                                       'type': 'imports', 'to': f'M{target}'})
    for i in range(task_count):
        path = f'artifact-{i}.txt'
        (root / path).write_text(f'synthetic artifact {i}\n', encoding='utf-8')
        state['objects'].append({'id': f'A{i}', 'type': 'Artifact', 'label': f'Artifact {i}',
                                'properties': {'path': path}})
        state['tasks'].append(task(f'T{i}', [f'M{i * width}'], [f'A{i}']))
    # The fixture constructs reviewed records without replaying 150 transactions.
    for t in state['tasks']:
        graph = ontology.snapshot(state, t['input_ids'], root, core._hash_file)
        manifest = {'graph': graph, 'producer_generations': {},
                    'contract': {'input_fields': {}, 'acceptance_rules': [], 'support_groups': []}}
        snap = {'sha256': core._manifest_hash(manifest), 'manifest': manifest}
        t.update(status='done', generation=1, input_snapshot=snap,
                 output_snapshot=core._output_snapshot(state, t, root),
                 evidence=[{'path': 'artifact-' + t['id'][1:] + '.txt', 'criterion': 0,
                            'note': 'Synthetic review', 'reviewer': 'fixture-reviewer',
                            'sha256': core._hash_file(root, 'artifact-' + t['id'][1:] + '.txt')}])
    state['revision'] = 1
    state['history'] = [{'revision': 1, 'action': 'complete_task', 'actor': 'fixture',
                         'reason': 'Synthetic reviewed fixture'}]
    return state


def replacement(state, index=0, value=1):
    obj = copy.deepcopy(state['objects'][index])
    obj['properties']['version'] = value
    return event('mutate_graph', operations=[{'op': 'replace_object', 'object': obj}])
