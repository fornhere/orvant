"""w109: graph-backed inputs, bounded traversal and recorded verification."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest

from ontology_fixtures import build_notebook, prop, relation_type, link, task, write_notebook_files, event

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/orvant/scripts'))
import core
import derive


class GraphProposalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        write_notebook_files(self.root)
        self.state = build_notebook()
        self.state['tasks'] = []
        self.state['ontology']['object_types'] += [
            {'id': 'scenario', 'label': 'Scenario', 'properties': {
                'acceptance': prop('string'), 'verification': prop('json', False), 'commands': prop('json', False)}},
            {'id': 'source', 'label': 'Source', 'properties': {}},
            {'id': 'code_module', 'label': 'Module', 'properties': {'path': prop('file')}}]
        self.state['ontology']['relation_types'] += [
            relation_type('exercised_by', 'scenario', 'source', 'none'),
            relation_type('grounds:source', 'code_module', 'source', 'forward'),
            relation_type('tests', 'code_module', 'code_module', 'reverse')]
        for suffix in ('a', 'b'):
            self.add_object('scenario-' + suffix, 'scenario', {
                'acceptance': 'Recorded criterion ' + suffix,
                'verification': {'command': 'python -m unittest tests.test_' + suffix,
                                 'evidence_dir': 'evidence/' + suffix}})
            self.add_object('source-' + suffix, 'source', {})
            self.add_module('module-' + suffix, 'src/' + suffix + '.py')
            self.add_module('test-' + suffix, 'tests/test_' + suffix + '.py')
            self.state['relations'] += [
                link('exercise-' + suffix, 'scenario-' + suffix, 'exercised_by', 'source-' + suffix),
                link('ground-' + suffix, 'module-' + suffix, 'grounds:source', 'source-' + suffix),
                link('test-link-' + suffix, 'test-' + suffix, 'tests', 'module-' + suffix)]

    def add_object(self, key, kind, properties):
        self.state['objects'].append({'id': key, 'type': kind, 'label': key, 'properties': properties})

    def add_module(self, key, path):
        self.add_object(key, 'code_module', {'path': path})
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('# synthetic\n')

    def proposal(self, suffix='a'):
        return next(p for p in derive.derive(self.state, self.root)['proposals']
                    if p['because'].get('object_id') == 'scenario-' + suffix)

    def test_graph_paths_commands_and_application_preview_apply_serial_lanes(self):
        before = copy.deepcopy(self.state)
        proposals = [self.proposal(s) for s in ('a', 'b')]
        self.assertEqual(self.state, before)
        for p, suffix in zip(proposals, ('a', 'b')):
            self.assertIn({'object_id': 'module-' + suffix, 'paths': ['src/' + suffix + '.py']}, p['inputs'])
            self.assertEqual(p['verification'], {'paths': ['tests/test_' + suffix + '.py'],
                'commands': ['python -m unittest tests.test_' + suffix]})
            self.assertIsNone(p['write_scope'])
            self.assertTrue(core.preview_event(self.state, p['event'], self.root)['ok'])
            self.state = core.apply_event(self.state, p['event'], self.root)
        lanes = derive.lanes(self.state, self.root)['lanes']
        self.assertEqual(len(lanes), 2)
        self.assertEqual([l['mode'] for l in lanes], ['serial', 'serial'])
        self.assertEqual(lanes[0]['write_scope'], [])

    def test_application_sources_missing_verification_scope_and_command(self):
        scenario = next(o for o in self.state['objects'] if o['id'] == 'scenario-a')
        del scenario['properties']['verification']
        p = self.proposal()
        self.assertIsNone(p['write_scope'])
        self.assertEqual(p['verification'], {'paths': ['tests/test_a.py']})
        scenario['properties']['verification'] = {}
        p = self.proposal()
        self.assertIsNone(p['write_scope'])
        self.state = core.apply_event(self.state, p['event'], self.root)
        self.assertEqual(derive.lanes(self.state, self.root)['lanes'][0]['mode'], 'serial')

    def test_sorted_bounded_modules_and_tests(self):
        for number in range(55):
            self.add_module('extra-' + str(number), 'src/extra-%02d.py' % number)
            self.state['relations'].append(link('extra-ground-' + str(number),
                'extra-' + str(number), 'grounds:source', 'source-a'))
            self.add_module('extra-test-' + str(number), 'tests/extra-%02d.py' % number)
            self.state['relations'].append(link('extra-tests-' + str(number),
                'extra-test-' + str(number), 'tests', 'module-a'))
        p = self.proposal()
        self.assertTrue(p['truncated'])
        self.assertLessEqual(len(p['verification']['paths']), 50)
        self.assertLessEqual(sum(i['object_id'].startswith(('module-', 'extra-')) for i in p['inputs']), 50)
        self.assertIsNone(p['write_scope'])
        self.state['objects'].reverse()
        self.state['relations'].reverse()
        self.assertEqual(p, self.proposal())

    def test_depth_cycles_and_invalid_evidence_path_stay_safe(self):
        self.state['ontology']['relation_types'].append(relation_type('next', 'source', 'source', 'none'))
        self.state['relations'] = [r for r in self.state['relations'] if r['id'] != 'ground-a']
        previous = 'source-a'
        for n in range(8):
            key = 'deep-' + str(n)
            self.add_object(key, 'source', {})
            self.state['relations'].append(link('deep-link-' + str(n), previous, 'next', key))
            previous = key
        self.state['relations'] += [link('cycle', previous, 'next', 'source-a'),
            link('deep-ground', 'module-a', 'grounds:source', previous)]
        p = self.proposal()
        self.assertNotIn('truncated', p)
        self.assertEqual(p['verification']['paths'], [])
        self.assertIsNone(p['write_scope'])
        obj = next(o for o in self.state['objects'] if o['id'] == 'scenario-b')
        obj['properties']['verification']['evidence_dir'] = '../escape'
        self.assertIsNone(self.proposal('b')['write_scope'])

    def test_shared_evidence_directory_conflicts_even_with_disjoint_tests(self):
        for obj in self.state['objects']:
            if obj['type'] == 'scenario':
                obj['properties']['verification']['evidence_dir'] = 'evidence/shared'
        for suffix in ('a', 'b'):
            p = derive._new_proposal(self.state, 'inspect', ['scenario-' + suffix],
                ['Inspect recorded change'], {'object_id': 'scenario-' + suffix})
            self.state = core.apply_event(self.state, p['event'], self.root)
        lanes = derive.lanes(self.state, self.root)['lanes']
        self.assertEqual(len(lanes), 1)
        self.assertEqual(len(lanes[0]['tasks']), 2)
        self.assertEqual(lanes[0]['write_scope'], ['evidence/shared'])
        self.assertIn('write_scope_overlap', lanes[0]['reasons'][0]['reasons'])

    def test_test_fanout_is_exactly_bounded_and_sorted(self):
        for number in range(55):
            self.add_module('fan-test-' + str(number), 'tests/fan-%02d.py' % number)
            self.state['relations'].append(link('fan-link-' + str(number),
                'fan-test-' + str(number), 'tests', 'module-a'))
        proposal = self.proposal()
        self.assertEqual(len(proposal['verification']['paths']), 50)
        self.assertEqual(proposal['verification']['paths'], sorted(proposal['verification']['paths']))
        self.assertTrue(proposal['truncated'])
        self.assertIsNone(proposal['write_scope'])

    def test_revalidation_uses_recorded_directory_and_no_invented_command(self):
        obj = next(o for o in self.state['objects'] if o['id'] == 'scenario-a')
        obj['properties']['verification'] = {'output_dir': 'evidence/a'}
        work = task('verify-a', ['scenario-a'], [])
        work['acceptance'] = ['Recorded criterion a']
        self.state['tasks'] = [work]
        for action in ('start_task', 'submit_evidence', 'complete_task'):
            fields = {'task_id': 'verify-a'}
            if action == 'submit_evidence':
                (self.root / 'proof.txt').write_text('synthetic proof')
                fields['items'] = [{'path': 'proof.txt', 'criterion': 0,
                    'note': 'synthetic review', 'reviewer': 'synthetic-reviewer'}]
            self.state = core.apply_event(self.state, event(action, **fields), self.root)
        (self.root / 'proof.txt').write_text('changed proof')
        proposal = next(p for p in derive.derive(self.state, self.root)['proposals']
                        if p['task_id'] == 'verify-a')
        self.assertEqual(proposal['kind'], 'revalidate')
        self.assertEqual(proposal['verification'], {'paths': ['tests/test_a.py']})
        self.assertEqual(proposal['write_scope'], ['evidence/a'])
        self.state = core.apply_event(self.state, proposal['event'], self.root)
        self.assertEqual(derive.lanes(self.state, self.root)['lanes'][0]['write_scope'], ['evidence/a'])
        del obj['properties']['verification']
        # apply_event returns a copy: remove the live record's declaration too.
        del next(o for o in self.state['objects'] if o['id'] == 'scenario-a')['properties']['verification']
        self.assertEqual(derive.lanes(self.state, self.root)['lanes'][0]['mode'], 'serial')

    def test_grounded_task_with_unknown_output_remains_serial(self):
        obj = next(o for o in self.state['objects'] if o['id'] == 'scenario-a')
        del obj['properties']['verification']
        self.state['tasks'] = [task('implementation', ['scenario-a'], ['source-a'])]
        lane = derive.lanes(self.state, self.root)['lanes'][0]
        self.assertEqual(lane['mode'], 'serial')

    def test_output_scope_survives_enrichment_and_truncation(self):
        self.add_module('output', 'result/output.py')
        work = task('implementation', ['scenario-a'], ['output'])
        self.state['tasks'] = [work]
        before = derive.lanes(self.state, self.root)['lanes']
        self.assertEqual(before[0]['write_scope'], ['result/output.py'])
        for n in range(60):
            self.add_module('overflow-' + str(n), 'tests/overflow-%02d.py' % n)
            self.state['relations'].append(link('overflow-link-' + str(n),
                'overflow-' + str(n), 'tests', 'module-a'))
        self.assertTrue(derive._CodeContext(self.state).resolve(['scenario-a'])['truncated'])
        self.assertEqual(derive.lanes(self.state, self.root)['lanes'], before)

    def test_application_verification_record_does_not_authorize_evidence_only(self):
        self.state['tasks'] = [task('implementation', ['scenario-a'], [])]
        self.assertEqual(derive.lanes(self.state, self.root)['lanes'][0]['mode'], 'serial')

    def test_exact_chain_ignores_domain_fanout_and_copies_both_commands(self):
        self.state['ontology']['relation_types'].append(relation_type('next', 'source', 'source', 'none'))
        for n in range(70):
            self.add_object('noise-' + str(n), 'source', {})
            self.state['relations'].append(link('noise-link-' + str(n), 'source-a', 'next', 'noise-' + str(n)))
        for n in (1, 2):
            self.add_module('s3-source-' + str(n), 'src/s3-' + str(n) + '.py')
            self.add_module('s3-test-' + str(n), 'tests/s3-' + str(n) + '.py')
            self.state['relations'] += [
                link('s3-ground-' + str(n), 's3-source-' + str(n), 'grounds:source', 'source-a'),
                link('s3-tests-' + str(n), 's3-test-' + str(n), 'tests', 's3-source-' + str(n))]
        obj = next(o for o in self.state['objects'] if o['id'] == 'scenario-a')
        obj['properties']['commands'] = ['python tests/check_one.py', 'python tests/check_two.py']
        p = self.proposal()
        self.assertEqual(p['verification']['paths'], ['tests/s3-1.py', 'tests/s3-2.py', 'tests/test_a.py'])
        self.assertEqual(p['verification']['commands'], ['python tests/check_one.py',
            'python tests/check_two.py', 'python -m unittest tests.test_a'])
        self.assertNotIn('truncated', p)

    def test_truncation_preserves_explicit_inspection_evidence_scope(self):
        p = derive._new_proposal(self.state, 'inspect', ['scenario-a'],
            ['Inspect recorded change'], {'object_id': 'scenario-a'})
        self.state = core.apply_event(self.state, p['event'], self.root)
        before = derive.lanes(self.state, self.root)['lanes']
        self.assertEqual(before[0]['write_scope'], ['evidence/a'])
        for n in range(55):
            self.add_module('inspect-test-' + str(n), 'tests/inspect-%02d.py' % n)
            self.state['relations'].append(link('inspect-link-' + str(n),
                'inspect-test-' + str(n), 'tests', 'module-a'))
        self.assertTrue(derive._CodeContext(self.state).resolve(['scenario-a'], True)['truncated'])
        self.assertEqual(derive.lanes(self.state, self.root)['lanes'], before)

    def test_grounded_test_module_is_itself_recorded_verification(self):
        self.state['relations'] = [r for r in self.state['relations'] if r['id'] != 'ground-a']
        self.state['relations'].append(link('ground-test-a', 'test-a', 'grounds:source', 'source-a'))
        p = self.proposal()
        self.assertIn({'object_id': 'test-a', 'paths': ['tests/test_a.py']}, p['inputs'])
        self.assertEqual(p['verification']['paths'], ['tests/test_a.py'])
        self.assertNotIn({'object_id': 'module-a', 'paths': ['src/a.py']}, p['inputs'])
        self.assertIsNone(p['write_scope'])
