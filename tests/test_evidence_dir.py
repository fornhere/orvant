"""w110: project evidence scopes for recorded, source-free verification."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ontology_fixtures import build_notebook, event, prop, task, write_notebook_files, link, relation_type

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/orvant/scripts'
sys.path.insert(0, str(SCRIPTS))
import core
import derive


class EvidenceDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        write_notebook_files(self.root)
        self.state = build_notebook()
        self.state['tasks'] = []
        self.state['ontology']['object_types'].append({'id': 'scenario', 'label': 'Scenario',
            'properties': {'acceptance': prop('string'), 'commands': prop('json')}})
        for suffix in 'abc':
            self.state['objects'].append({'id': 'scenario-' + suffix, 'type': 'scenario',
                'label': 'Synthetic scenario', 'properties': {'acceptance': 'Criterion ' + suffix,
                    'commands': ['python -m unittest tests.test_' + suffix]}})

    def proposals(self):
        return [p for p in derive.derive(self.state, self.root)['proposals']
                if p['because'].get('object_id', '').startswith('scenario-')]

    def test_three_verifications_have_three_parallel_scopes(self):
        self.state['project']['evidence_dir'] = 'proof/runs'
        proposals = self.proposals()
        self.assertEqual(len(proposals), 3)
        for proposal in proposals:
            self.assertEqual(proposal['write_scope'], ['proof/runs/' + proposal['task_id'] + '/'])
            self.assertEqual(proposal['event']['tasks'][0]['output_ids'], [])
            self.state = core.apply_event(self.state, proposal['event'], self.root)
        lanes = derive.lanes(self.state, self.root)['lanes']
        self.assertEqual(len(lanes), 3)
        self.assertTrue(all(l['mode'] == 'parallel' and len(l['tasks']) == 1 for l in lanes))
        self.assertEqual({p for l in lanes for p in l['write_scope']},
                         {p for proposal in proposals for p in proposal['write_scope']})

    def test_missing_field_preserves_serial_and_old_validation(self):
        before = copy.deepcopy(self.state)
        core.validate(self.state)
        for proposal in self.proposals():
            self.assertIsNone(proposal['write_scope'])
            self.state = core.apply_event(self.state, proposal['event'], self.root)
        self.assertEqual([l['mode'] for l in derive.lanes(self.state, self.root)['lanes']],
                         ['serial'] * 3)
        self.assertNotIn('evidence_dir', before['project'])

    def test_unsafe_paths_rejected_by_state_and_event(self):
        for path in ('/proof', '../proof', 'proof/../out', '.git', 'proof/.GiT/x',
                     '.project/proof', 'C:/proof', '.', '', 'proof\\out'):
            with self.subTest(path=path):
                state = copy.deepcopy(self.state)
                state['project']['evidence_dir'] = path
                with self.assertRaises(ValueError):
                    core.validate(state)
                with self.assertRaises(ValueError):
                    core.preview_event(self.state, event('set_evidence_dir', evidence_dir=path), self.root)

    def test_preview_apply_records_project_change(self):
        before = copy.deepcopy(self.state)
        payload = event('set_evidence_dir', evidence_dir='proof')
        preview = core.preview_event(self.state, payload, self.root)
        self.assertEqual(self.state, before)
        self.assertEqual(preview['next_context']['project']['evidence_dir'], 'proof')
        self.assertEqual(preview['change']['project']['after']['evidence_dir'], 'proof')
        after = core.apply_event(self.state, payload, self.root)
        self.assertEqual(after['revision'], 1)
        self.assertEqual(after['history'][-1]['action'], 'set_evidence_dir')
        core.validate(after)

    def test_recorded_test_paths_without_commands_qualify(self):
        self.state['project']['evidence_dir'] = 'proof'
        self.state['ontology']['object_types'] += [
            {'id': 'source', 'label': 'Source', 'properties': {}},
            {'id': 'code_module', 'label': 'Code', 'properties': {'path': prop('file')}}]
        self.state['ontology']['relation_types'] += [
            relation_type('exercised_by', 'scenario', 'source', 'none'),
            relation_type('grounds:source', 'code_module', 'source', 'forward'),
            relation_type('tests', 'code_module', 'code_module', 'reverse')]
        for obj in self.state['objects']:
            if obj['type'] == 'scenario':
                obj['properties']['commands'] = []
        self.state['objects'] += [
            {'id': 'source-a', 'type': 'source', 'label': 'Source', 'properties': {}},
            {'id': 'module-a', 'type': 'code_module', 'label': 'Module', 'properties': {'path': 'src/a.py'}},
            {'id': 'test-a', 'type': 'code_module', 'label': 'Test', 'properties': {'path': 'tests/test_a.py'}}]
        self.state['relations'] += [link('exercise-a', 'scenario-a', 'exercised_by', 'source-a'),
            link('ground-a', 'module-a', 'grounds:source', 'source-a'),
            link('test-link-a', 'test-a', 'tests', 'module-a')]
        proposal = next(p for p in self.proposals() if p['because']['object_id'] == 'scenario-a')
        self.assertEqual(proposal['verification'], {'paths': ['tests/test_a.py']})
        self.assertEqual(proposal['write_scope'], ['proof/' + proposal['task_id'] + '/'])
        self.state = core.apply_event(self.state, proposal['event'], self.root)
        self.assertEqual(derive.lanes(self.state, self.root)['lanes'][0]['mode'], 'parallel')

    def test_revalidation_without_outputs_uses_project_directory(self):
        self.state['project']['evidence_dir'] = 'proof'
        work = task('verify-a', ['scenario-a'], [])
        work['acceptance'] = ['Criterion a']
        self.state['tasks'] = [work]
        proof = self.root / 'proof.txt'
        proof.write_text('Synthetic proof')
        for action in ('start_task', 'submit_evidence', 'complete_task'):
            fields = {'task_id': 'verify-a'}
            if action == 'submit_evidence':
                fields['items'] = [{'path': 'proof.txt', 'criterion': 0,
                    'note': 'Synthetic review', 'reviewer': 'synthetic-reviewer'}]
            self.state = core.apply_event(self.state, event(action, **fields), self.root)
        proof.write_text('Changed proof')
        proposal = next(p for p in derive.derive(self.state, self.root)['proposals']
                        if p['task_id'] == 'verify-a')
        self.assertEqual(proposal['write_scope'], ['proof/verify-a/'])
        self.state = core.apply_event(self.state, proposal['event'], self.root)
        lane = next(l for l in derive.lanes(self.state, self.root)['lanes']
                    if 'verify-a' in l['tasks'])
        self.assertEqual(lane['write_scope'], ['proof/verify-a/'])
        self.assertEqual(lane['mode'], 'parallel')

    def test_init_option_and_cli_event(self):
        spec = self.root / 'spec.json'
        spec.write_text(json.dumps(self.state))
        def cli(*args):
            result = subprocess.run([sys.executable, str(SCRIPTS / 'project.py'), *map(str, args)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return json.loads(result.stdout)
        cli('init', self.root, '--spec', spec, '--evidence-dir', 'proof')
        state_path = self.root / '.project/state.json'
        before = state_path.read_bytes()
        payload = self.root / 'event.json'
        payload.write_text(json.dumps(event('set_evidence_dir', evidence_dir='proof/new')))
        preview = cli('preview', self.root, '--event', payload, '--expected-revision', 0)
        self.assertEqual(state_path.read_bytes(), before)
        cli('apply', self.root, '--event', payload, '--expected-revision', 0,
            '--preview-digest', preview['preview_digest'])
        self.assertEqual(json.loads(state_path.read_text())['project']['evidence_dir'], 'proof/new')

    def test_implementation_scope_and_unrecorded_work_unchanged(self):
        self.state['project']['evidence_dir'] = 'proof'
        self.state['tasks'] = [task('implementation', ['scenario-a'], [])]
        self.assertEqual(derive.lanes(self.state, self.root)['lanes'][0]['mode'], 'serial')
        self.state['tasks'] = [task('implementation', ['experiment-a'], ['artifact-a'])]
        before = derive.lanes(self.state, self.root)
        del self.state['project']['evidence_dir']
        self.assertEqual(derive.lanes(self.state, self.root), before)
        self.state['project']['evidence_dir'] = 'proof'
        for obj in self.state['objects']:
            if obj['type'] == 'scenario':
                obj['properties']['commands'] = []
        self.assertTrue(all(p['write_scope'] is None for p in self.proposals()))


if __name__ == '__main__':
    unittest.main()
