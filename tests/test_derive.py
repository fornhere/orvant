"""Work derivation and conflict lanes over synthetic, temporary projects."""
import copy
import json
import locale
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ontology_fixtures import build_notebook, evidence, event, prop, task, write_notebook_files

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/orvant/scripts'
sys.path.insert(0, str(SCRIPTS))
import core
import derive


class DeriveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = build_notebook()
        write_notebook_files(self.root)

    def cli(self, command, *args):
        result = subprocess.run([sys.executable, str(SCRIPTS / 'project.py'), command,
                                 str(self.root), *map(str, args)], capture_output=True, text=True,
                                encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def install(self):
        spec = self.root / 'spec.json'
        spec.write_text(json.dumps(self.state), encoding='utf-8')
        self.cli('init', '--spec', spec)

    def save(self):
        (self.root / '.project/state.json').write_text(json.dumps(self.state), encoding='utf-8')

    def scenario(self):
        self.state['ontology']['object_types'].append({'id': 'Scenario', 'label': 'Senaryo',
            'properties': {'acceptance': prop('string_list'), 'source': prop('json')}})
        self.state['objects'].append({'id': 'scenario-x', 'type': 'Scenario', 'label': 'Kurmaca senaryo',
            'properties': {'acceptance': ['Tam olarak kayıtlı ölçüt.'], 'source': {'path': 'scenario.txt'}}})
        (self.root / 'scenario.txt').write_text('Kurmaca senaryo.', encoding='utf-8')

    def apply_cli(self, payload):
        path = self.root / 'event.json'
        path.write_text(json.dumps(payload), encoding='utf-8')
        revision = self.state['revision']
        self.cli('preview', '--event', path, '--expected-revision', revision)
        self.cli('apply', '--event', path, '--expected-revision', revision)
        self.state = json.loads((self.root / '.project/state.json').read_text(encoding='utf-8'))

    def complete_cli(self, identifier):
        for action in ('start_task', 'submit_evidence', 'complete_task'):
            fields = {'task_id': identifier}
            if action == 'submit_evidence':
                fields['items'] = evidence(identifier)
            self.apply_cli(event(action, **fields))

    def reopen_changed_pair(self):
        self.install()
        for identifier in ('run-a', 'evaluate-a'):
            self.complete_cli(identifier)
        (self.root / 'çıktı-a.txt').write_text('Değişmiş çıktı.', encoding='utf-8')
        proposals = [p for p in self.cli('derive')['proposals'] if p['kind'] == 'revalidate']
        self.assertEqual({p['task_id'] for p in proposals}, {'run-a', 'evaluate-a'})
        for p in proposals:
            self.assertEqual(p['event']['action'], 'reopen_task')
            self.apply_cli(p['event'])

    def test_revalidation_applied_once_leaves_open_tasks_without_proposals(self):
        self.reopen_changed_pair()
        self.assertFalse(any(p['task_id'] in ('run-a', 'evaluate-a')
                             for p in self.cli('derive')['proposals']))

    def test_cli_json_is_decoded_as_utf8_when_locale_is_legacy(self):
        self.scenario()
        self.install()
        with patch.object(locale, 'getencoding', return_value='cp1252'):
            proposal = next(p for p in self.cli('derive')['proposals']
                            if p['because'].get('object_id') == 'scenario-x')
        self.assertEqual(proposal['acceptance'], ['Tam olarak kayıtlı ölçüt.'])

    def test_doing_task_is_not_reopened_by_derive(self):
        self.reopen_changed_pair()
        self.apply_cli(event('start_task', task_id='run-a'))
        self.assertFalse(any(p['task_id'] == 'run-a' for p in self.cli('derive')['proposals']))
        self.assertEqual(next(t for t in self.state['tasks'] if t['id'] == 'run-a')['status'], 'doing')

    def test_old_domain_snapshot_does_not_survive_new_acceptance(self):
        self.install()
        self.complete_cli('run-a')
        obj = copy.deepcopy(next(o for o in self.state['objects'] if o['id'] == 'experiment-a'))
        obj['properties']['prompt'] = 'Yeni kurmaca girdi'
        self.apply_cli(event('mutate_graph', operations=[{'op': 'replace_object', 'object': obj}]))
        current = next(t for t in self.state['tasks'] if t['id'] == 'run-a')
        self.assertEqual(current['status'], 'review')
        self.assertEqual(derive._domain_changes(self.state, current, self.root), [])
        self.complete_cli('run-a')
        self.assertFalse(any(p['task_id'] == 'run-a' for p in self.cli('derive')['proposals']))
        accepted = next(t for t in self.state['tasks'] if t['id'] == 'run-a')
        baseline = accepted['output_snapshot']['sha256']
        (self.root / 'çıktı-a.txt').write_text('Son kabulden sonraki yeni çıktı.', encoding='utf-8')
        proposal = next(p for p in self.cli('derive')['proposals'] if p['task_id'] == 'run-a')
        self.assertEqual(proposal['kind'], 'revalidate')
        self.assertTrue(any(c.get('snapshot') == 'output_snapshot' and
                            c['before_sha256'] == baseline and c['after_sha256'] != baseline
                            for c in proposal['because']['changes']))
        self.apply_cli(proposal['event'])
        current = next(t for t in self.state['tasks'] if t['id'] == 'run-a')
        self.assertEqual(derive._domain_changes(self.state, current, self.root), [])
        self.assertFalse(any(p['task_id'] == 'run-a' for p in self.cli('derive')['proposals']))

    def test_stale_source_one_revalidation_with_hashes_and_criteria(self):
        self.install()
        for action in ('start_task', 'submit_evidence', 'complete_task'):
            fields = {'task_id': 'run-a'}
            if action == 'submit_evidence':
                fields['items'] = evidence('run-a')
            self.state = core.apply_event(self.state, event(action, **fields), self.root)
        self.save()
        before = (self.root / '.project/state.json').read_bytes()
        (self.root / 'çıktı-a.txt').write_text('Değişmiş çıktı.', encoding='utf-8')
        proposals = self.cli('derive')['proposals']
        relevant = [p for p in proposals if p['kind'] == 'revalidate' and p['task_id'] == 'run-a']
        self.assertEqual(len(relevant), 1)
        p = relevant[0]
        self.assertEqual(p['acceptance'], self.state['tasks'][0]['acceptance'])
        self.assertTrue(any(c.get('path') == 'çıktı-a.txt' and c['before_sha256'] != c['after_sha256']
                            for c in p['because']['changes']))
        self.assertEqual(p['because']['criterion_indices'], [0])
        self.assertEqual(p['event']['action'], 'reopen_task')
        self.assertEqual((self.root / '.project/state.json').read_bytes(), before)

    def test_unproven_scenario_event_preview_apply_and_dedup(self):
        self.scenario()
        self.install()
        report = self.cli('derive')
        p = next(p for p in report['proposals'] if p['because'].get('object_id') == 'scenario-x')
        self.assertEqual(p['acceptance'], ['Tam olarak kayıtlı ölçüt.'])
        self.assertEqual(p['inputs'], [{'object_id': 'scenario-x', 'paths': ['scenario.txt']}])
        payload = self.root / 'event.json'
        payload.write_text(json.dumps(p['event']), encoding='utf-8')
        preview = self.cli('preview', '--event', payload, '--expected-revision', 0)
        self.assertFalse(preview['state_committed'])
        result = self.cli('apply', '--event', payload, '--expected-revision', 0)
        self.assertEqual(result['revision'], 1)
        self.assertFalse(any(x['because'].get('object_id') == 'scenario-x'
                             for x in self.cli('derive')['proposals']))
        installed = self.root / '.project/scripts/project.py'
        result = subprocess.run([sys.executable, str(installed), 'derive', str(self.root)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_deterministic_out_and_no_managed_write(self):
        self.scenario()
        self.install()
        before = {str(p.relative_to(self.root)): p.read_bytes()
                  for p in (self.root / '.project').rglob('*') if p.is_file()}
        out = self.root / 'proposals.json'
        self.cli('derive', '--out', out)
        first = out.read_bytes()
        self.cli('derive', '--out', out)
        self.assertEqual(first, out.read_bytes())
        self.assertEqual(before, {str(p.relative_to(self.root)): p.read_bytes()
                                  for p in (self.root / '.project').rglob('*') if p.is_file()})
        result = subprocess.run([sys.executable, str(SCRIPTS / 'project.py'), 'derive', str(self.root),
                                 '--out', str(self.root / '.project/state.json')], capture_output=True)
        self.assertNotEqual(result.returncode, 0)

    def test_lanes_overlap_disjoint_dependency_and_limit(self):
        # Independent tasks may write the same file through different objects.
        self.state['tasks'] = [t for t in self.state['tasks'] if t['id'].startswith('run-')]
        self.state['tasks'].append(task('chain', [], []))
        self.state['tasks'][-1]['depends_on'] = ['run-a']
        next(o for o in self.state['objects'] if o['id'] == 'artifact-b')['properties']['path'] = 'çıktı-a.txt'
        self.install()
        report = self.cli('lanes')
        a = next(l for l in report['lanes'] if 'run-a' in l['tasks'])
        self.assertIn('run-b', a['tasks'])
        self.assertIn('chain', a['queued_tasks'])
        self.assertFalse(any('chain' in l['tasks'] + l['queued_tasks'] for l in report['lanes'] if l != a))
        c = next(l for l in report['lanes'] if 'run-c' in l['tasks'])
        self.assertNotEqual(a['id'], c['id'])
        self.assertIn('çıktı-a.txt', a['write_scope'])
        limited = self.cli('lanes', '--max', 1)
        self.assertEqual(len(limited['lanes']), 1)
        self.assertTrue(limited['queued_lanes'])
        self.assertIn('expected-revision', limited['single_writer'])

    def test_unknown_scope_serial_and_missing_impact_explained(self):
        self.state['tasks'] = [task('unknown', ['experiment-a'], [])]
        self.install()
        lane = self.cli('lanes')['lanes'][0]
        self.assertEqual(lane['mode'], 'serial')
        self.assertTrue(lane['reasons'])
        self.assertIn('impact', self.cli('derive')['skipped'][0]['category'])

    def test_impact_uses_recorded_history_and_open_task_suppresses(self):
        self.state['tasks'] = []
        self.install()
        obj = copy.deepcopy(self.state['objects'][0])
        obj['properties']['prompt'] = 'Değişen kurmaca girdi'
        self.state = core.apply_event(self.state, event('mutate_graph', operations=[
            {'op': 'replace_object', 'object': obj}]), self.root)
        self.save()
        report = self.cli('derive')
        p = next(p for p in report['proposals'] if p['because'].get('object_id') == 'experiment-a')
        self.assertEqual(p['kind'], 'inspect')
        self.assertEqual(p['because']['revision'], 1)
        self.assertTrue(p['because']['impact_path'])
        self.state = core.apply_event(self.state, p['event'], self.root)
        self.save()
        self.assertFalse(any(x['because'].get('object_id') == 'experiment-a'
                             for x in self.cli('derive')['proposals']))

    def test_explicit_unmet_criterion_and_existing_open_coverage(self):
        self.state['ontology']['object_types'].append({'id': 'Requirement', 'label': 'Ölçüt',
            'properties': {'definition': prop('string'), 'satisfied': prop('boolean')}})
        self.state['objects'].append({'id': 'unmet', 'type': 'Requirement', 'label': 'Kurmaca ölçüt',
            'properties': {'definition': 'Çıktı iki kayıt içerir.', 'satisfied': False}})
        self.install()
        p = next(p for p in self.cli('derive')['proposals'] if p['because'].get('object_id') == 'unmet')
        self.assertEqual(p['acceptance'], ['Çıktı iki kayıt içerir.'])
        self.state = core.apply_event(self.state, p['event'], self.root)
        self.save()
        self.assertFalse(any(p['because'].get('object_id') == 'unmet' for p in self.cli('derive')['proposals']))

    def test_decision_status_controls_shared_lanes(self):
        self.state['tasks'] = [t for t in self.state['tasks'] if t['id'] in ('run-a', 'run-b')]
        # Isolate lane conflicts from readiness validation: schema-3 rejects
        # direct proposed/rejected/unknown bindings before lanes can inspect them.
        readiness = core.inspect_state(self.state, self.root)
        decision = {'id': 'shared', 'topic': 'format', 'statement': 'Kurmaca biçim',
                    'status': 'accepted', 'rationale': 'Sentetik gerekçe',
                    'source': 'Sentetik brief', 'supersedes': None, 'accepted_by': 'test-author'}
        for t in self.state['tasks']:
            t['decision_ids'] = ['shared']
        for status, count in (('rejected', 2), ('superseded', 2), ('accepted', 2),
                              ('proposed', 1), ('open', 1), ('unknown', 1)):
            with self.subTest(status=status):
                self.state['decisions'] = [{**decision, 'status': status}]
                if status == 'superseded':
                    self.state['decisions'].append({**decision, 'id': 'successor',
                                                   'supersedes': 'shared'})
                    # Even a new proposal on this topic cannot reopen history.
                    self.state['decisions'].append({**decision, 'id': 'proposal',
                                                   'status': 'proposed', 'supersedes': 'successor',
                                                   'accepted_by': None})
                with patch.object(derive.core, 'inspect_state', return_value=readiness):
                    report = derive.lanes(self.state, self.root)
                self.assertEqual(len(report['lanes']), count)
                self.assertEqual(report['queued_lanes'], [])
                self.assertEqual(sorted(t for lane in report['lanes'] for t in lane['tasks']),
                                 ['run-a', 'run-b'])
                if count == 1:
                    self.assertEqual(report['lanes'][0]['tasks'], ['run-a', 'run-b'])
                    self.assertTrue(any('shared_open_decision: shared' in edge['reasons']
                                        for edge in report['lanes'][0]['reasons']))
                else:
                    self.assertEqual(sorted(lane['tasks'] for lane in report['lanes']),
                                     [['run-a'], ['run-b']])

    def test_open_decision_groups_lanes_and_invalid_max_rejected(self):
        self.state['tasks'] = [t for t in self.state['tasks'] if t['id'].startswith('run-')]
        self.state['decisions'] = [
            {'id': 'accepted', 'topic': 'format', 'statement': 'Kurmaca biçim', 'status': 'accepted',
             'rationale': 'Sentetik gerekçe', 'source': 'Sentetik brief', 'supersedes': None,
             'accepted_by': 'test-author'},
            {'id': 'replacement', 'topic': 'format', 'statement': 'Başka kurmaca biçim', 'status': 'proposed',
             'rationale': 'Sentetik seçenek', 'source': 'Sentetik öneri', 'supersedes': 'accepted',
             'accepted_by': None}]
        for t in self.state['tasks'][:2]:
            t['decision_ids'] = ['accepted']
        self.install()
        lanes = self.cli('lanes')['lanes']
        a = next(l for l in lanes if 'run-a' in l['tasks'])
        self.assertIn('run-b', a['tasks'])
        self.assertTrue(any('shared_open_decision' in r for e in a['reasons'] for r in e['reasons']))
        failed = subprocess.run([sys.executable, str(SCRIPTS / 'project.py'), 'lanes', str(self.root),
                                 '--max', '0'], capture_output=True)
        self.assertNotEqual(failed.returncode, 0)

    def test_source_export_conflict_and_unknown_task_is_alone(self):
        self.scenario()
        self.state['tasks'] = [task('unknown', ['experiment-a'], []),
                               task('known', [], ['artifact-c'])]
        self.install()
        report = self.cli('lanes')
        unknown = next(l for l in report['lanes'] if 'unknown' in l['tasks'])
        self.assertEqual(unknown['tasks'], ['unknown'])
        self.assertEqual(unknown['mode'], 'serial')
        before = (self.root / 'scenario.txt').read_bytes()
        failed = subprocess.run([sys.executable, str(SCRIPTS / 'project.py'), 'derive', str(self.root),
                                 '--out', str(self.root / 'scenario.txt')], capture_output=True)
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual((self.root / 'scenario.txt').read_bytes(), before)

    def test_mutation_cleared_evidence_retains_history_without_reopening_review(self):
        self.install()
        for identifier in ('run-a', 'evaluate-a'):
            for action in ('start_task', 'submit_evidence', 'complete_task'):
                fields = {'task_id': identifier}
                if action == 'submit_evidence':
                    fields['items'] = evidence(identifier)
                self.state = core.apply_event(self.state, event(action, **fields), self.root)
        obj = copy.deepcopy(next(o for o in self.state['objects'] if o['id'] == 'criterion-shared'))
        obj['properties']['version'] = 2
        self.state = core.apply_event(self.state, event('mutate_graph', operations=[
            {'op': 'replace_object', 'object': obj}]), self.root)
        self.save()
        current = next(t for t in self.state['tasks'] if t['id'] == 'evaluate-a')
        self.assertEqual(current['status'], 'review')
        self.assertEqual(current['evidence'], [])
        self.assertTrue(current['review_reasons'])
        change = next(c for c in self.state['history'][-1]['change']['task_changes']
                      if c['task_id'] == 'evaluate-a')
        old_objects = change['before']['input_snapshot']['manifest']['graph']['objects']
        old = next(o for o in old_objects if o['id'] == 'criterion-shared')
        self.assertNotEqual(derive._hash(old), derive._hash(obj))
        self.assertFalse(any(p['task_id'] == 'evaluate-a' for p in self.cli('derive')['proposals']))

    def test_unrelated_open_or_done_task_does_not_cover_scenario_acceptance(self):
        self.scenario()
        self.state['tasks'] = [task('unrelated', ['scenario-x'], [])]
        self.state['tasks'][0]['acceptance'] = ['Metinde yazım yanlışı yok.']
        self.install()
        self.assertTrue(any(p['because'].get('object_id') == 'scenario-x'
                            for p in self.cli('derive')['proposals']))
        for action in ('start_task', 'submit_evidence', 'complete_task'):
            fields = {'task_id': 'unrelated'}
            if action == 'submit_evidence':
                (self.root / 'review.txt').write_text('Sentetik inceleme.', encoding='utf-8')
                fields['items'] = [{'path': 'review.txt', 'criterion': 0,
                                   'note': 'Sentetik inceleme.', 'reviewer': 'test-reviewer'}]
            self.state = core.apply_event(self.state, event(action, **fields), self.root)
        self.save()
        self.assertTrue(any(p['because'].get('object_id') == 'scenario-x'
                            for p in self.cli('derive')['proposals']))
        # Only matching, currently evidenced acceptance covers a scenario.
        current = self.state['tasks'][0]
        definition = {key: copy.deepcopy(current[key]) for key in
                      core.DEFINITION_FIELDS | {'input_ids', 'output_ids'}}
        definition['acceptance'] = ['Tam olarak kayıtlı ölçüt.']
        self.state = core.apply_event(self.state, event('revise_task', task_id='unrelated',
                                                       definition=definition), self.root)
        self.save()
        self.assertFalse(any(p['because'].get('object_id') == 'scenario-x'
                             for p in self.cli('derive')['proposals']))

    def test_cancelled_derived_task_does_not_suppress_unproven_contract(self):
        self.scenario()
        self.install()
        proposal = next(p for p in self.cli('derive')['proposals'] if p['because'].get('object_id') == 'scenario-x')
        self.state = core.apply_event(self.state, proposal['event'], self.root)
        next(t for t in self.state['tasks'] if t['id'] == proposal['task_id'])['status'] = 'cancelled'
        self.save()
        replacement = next(p for p in self.cli('derive')['proposals'] if p['because'].get('object_id') == 'scenario-x')
        self.assertNotEqual(replacement['task_id'], proposal['task_id'])
        core.preview_event(self.state, replacement['event'], self.root)


if __name__ == '__main__':
    unittest.main()
