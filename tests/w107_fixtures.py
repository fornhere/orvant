"""w102 generator with dense unproven acceptance and main's frozen runtime."""
import importlib.util
from contextlib import contextmanager
from pathlib import Path
import sys
from large_fixtures import large_record, runtime
from ontology_fixtures import prop


@contextmanager
def modules(reference=False):
    with runtime() as loaded:
        if reference:
            for name in ('core', 'derive'):
                path = Path(__file__).parent / 'w107_reference' / (name + '.py')
                spec = importlib.util.spec_from_file_location(name, path)
                module = importlib.util.module_from_spec(spec)
                sys.modules[name] = module
                spec.loader.exec_module(module)
                loaded[name] = module
        yield loaded


def record(root, loaded, count=2000, proposals=40):
    with loaded["ontology"].analysis_scope():
        return _record(root, loaded, count, proposals)


def _record(root, loaded, count, proposals):
    state = large_record(root, loaded, count=count, task_count=40)
    state['ontology']['object_types'][0]['properties']['acceptance'] = prop('string', required=False)
    for obj in state['objects'][1:proposals + 1]:
        obj['properties']['acceptance'] = 'Synthetic independent review: ' + obj['id']
    loaded['ontology'].invalidate(state)
    for task in state['tasks']:
        task['input_snapshot'] = loaded['core']._input_snapshot(state, task, root)
    # Large retained, valid JSON history models a long-lived record.
    state['history'][0]['change'] = {'synthetic_retained_data': 'x' * (count * 3000)}
    return state


def add_code_graph(state, root):
    """Enrich the large fixture with exact chains and unrelated domain fanout."""
    from ontology_fixtures import relation_type, link
    state['ontology']['object_types'] += [
        {'id': 'code_module', 'label': 'Code', 'properties': {'path': prop('file')}},
        {'id': 'Domain', 'label': 'Domain', 'properties': {}}]
    state['ontology']['relation_types'] += [
        relation_type('exercised_by', 'Module', 'Domain', 'none'),
        relation_type('grounds:domain', 'code_module', 'Domain', 'none'),
        relation_type('tests', 'code_module', 'code_module', 'none'),
        relation_type('noise', 'Domain', 'Domain', 'none')]
    for i in range(35):
        domain = 'domain-' + str(i)
        state['objects'].append({'id': domain, 'type': 'Domain', 'label': domain, 'properties': {}})
        state['relations'].append(link('exercise-' + str(i), 'M' + str(i + 1), 'exercised_by', domain))
        for kind, folder in (('source', 'src'), ('test', 'tests')):
            key, path = kind + '-' + str(i), folder + '/graph-' + str(i) + '.py'
            (root / folder).mkdir(exist_ok=True)
            (root / path).write_text('# synthetic graph\n')
            state['objects'].append({'id': key, 'type': 'code_module', 'label': key, 'properties': {'path': path}})
        state['relations'] += [link('ground-' + str(i), 'source-' + str(i), 'grounds:domain', domain),
            link('test-edge-' + str(i), 'test-' + str(i), 'tests', 'source-' + str(i))]
    for i in range(34):
        state['relations'].append(link('noise-' + str(i), 'domain-' + str(i), 'noise', 'domain-' + str(i + 1)))
