"""w106: real CLI bytes and preview/apply guards across hash seeds."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from large_fixtures import large_record, runtime, SCRIPTS
from ontology_fixtures import event, relation_type


class HashSeedTests(unittest.TestCase):
    def test_dense_cli_outputs_and_cross_seed_apply_are_identical(self):
        self.check_cli_outputs(False)

    def test_support_loss_cli_outputs_and_cross_seed_apply_are_identical(self):
        self.check_cli_outputs(True)

    def check_cli_outputs(self, support_loss):
        with tempfile.TemporaryDirectory() as temp, runtime() as m:
            root = Path(temp)
            state = large_record(root, m, count=120, task_count=24)
            # Twelve independent cyclic/diamond graph components feed twelve
            # converging producer tasks. Direct tasks each have multiple reasons.
            for i, task in enumerate(state['tasks']):
                task['input_ids'] = ([f'M{i * 5}', f'M{i * 5 + 2}'] if i < 12 else
                                     [f'A{j}' for j in range(12 if i < 20 else 20)])
                task['object_ids'] = task['input_ids'] + task['output_ids']
            if support_loss:
                state['ontology']['relation_types'].append(
                    relation_type('supports', 'Module', 'Artifact', 'none'))
                for i, task in enumerate(state['tasks'][:12]):
                    rid = f'support-{i}'
                    state['relations'].append({'id': rid, 'type': 'supports',
                                               'from': 'M119', 'to': f'A{i}'})
                    task['support_groups'] = [{'id': 'support', 'mode': 'all', 'branches': [
                        {'id': 'approved', 'input_ids': ['M119'], 'relation_ids': [rid]}]}]
                    task['support_snapshot'] = m['acceptance'].support_snapshot(
                        state, task, root, m['core']._hash_file, ['support/approved'],
                        m['core']._support_graph, {})
            for task in state['tasks']:
                task['output_snapshot'] = m['core']._output_snapshot(state, task, root)
                task['input_snapshot'] = m['core']._input_snapshot(state, task, root)
            m['core'].validate(state)
            self.assertEqual(m['core'].inspect_state(state, root)['ready'], [])
            operations = []
            for i in ([119] if support_loss else [j * 5 for j in range(12)]):
                obj = copy.deepcopy(state['objects'][i])
                obj['properties']['version'] = 1
                operations.append({'op': 'replace_object', 'object': obj})
            payload = event('mutate_graph', operations=operations)
            folder = root / '.project'
            shutil.copytree(SCRIPTS, folder / 'scripts')
            (root / 'event.json').write_text(json.dumps(payload), encoding='utf-8')
            initial = m['project'].json_text(state).encode('utf-8')
            command = [sys.executable, str(folder / 'scripts/project.py')]
            def run(seed, *args):
                result = subprocess.run(command + list(args), capture_output=True,
                                        env={**os.environ, 'PYTHONHASHSEED': seed,
                                             'PYTHONDONTWRITEBYTECODE': '1'})
                self.assertEqual(result.returncode, 0, (result.stdout + result.stderr).decode())
                return result.stdout
            baseline = None
            digest = None
            for seed in ('0', '1', '12345', 'random', 'random'):
                (folder / 'state.json').write_bytes(initial)
                (folder / 'CONTEXT.md').write_text('synthetic view\n')
                preview = run(seed, 'preview', str(root), '--event', str(root / 'event.json'),
                              '--expected-revision', '1')
                if digest is None:
                    report = json.loads(preview)
                    digest = report['preview_digest']
                    if not support_loss:
                        impact = report['change']['impact']
                        self.assertEqual(len(impact['affected_tasks']), 24)
                        self.assertTrue(any(len(t['reasons']) > 1 for t in impact['affected_tasks']))
                outputs = {'preview': preview}
                for name in ('context', 'derive', 'lanes'):
                    outputs['before_' + name] = run(seed, name, str(root))
                # Digest always comes from seed 0; apply runs in a new process.
                outputs['apply'] = run(seed, 'apply', str(root), '--event', str(root / 'event.json'),
                                       '--expected-revision', '1', '--preview-digest', digest)
                outputs['state'] = (folder / 'state.json').read_bytes()
                applied = json.loads(outputs['state'])
                self.assertTrue(all(t['status'] == 'review' and t['review_reasons']
                                    for t in applied['tasks']))
                for name in ('context', 'derive', 'lanes'):
                    outputs['after_' + name] = run(seed, name, str(root))
                if baseline is None:
                    baseline = outputs
                for name, output in outputs.items():
                    with self.subTest(seed=seed, output=name):
                        self.assertEqual(output, baseline[name])
