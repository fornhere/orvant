"""w107: independent proposals, bounded validation, main byte equivalence."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from w107_fixtures import modules, record, add_code_graph
from derive_compat import legacy_report


class DeriveLargeTests(unittest.TestCase):
    def test_proposals_do_not_compute_full_previews(self):
        with tempfile.TemporaryDirectory() as temp, modules() as m:
            root = Path(temp)
            state = record(root, m, count=80, proposals=35)
            before = copy.deepcopy(state)
            with patch.object(m['core'], 'preview_event', wraps=m['core'].preview_event) as preview:
                report = m['derive'].derive(state, root)
            self.assertEqual(len(report['proposals']), 35)
            self.assertEqual(preview.call_count, 0)
            self.assertEqual(state, before)

    def test_main_reference_bytes_and_each_event_preview(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with modules() as m:
                state = record(root, m, count=80, proposals=35)
                (root / 'module-0.py').write_text('# synthetic drift\n')
            reports = []
            for reference in (True, False):
                with modules(reference) as m:
                    report = m['derive'].derive(state, root)
                    reports.append(json.dumps(legacy_report(report), ensure_ascii=False).encode())
                    for proposal in report['proposals']:
                        self.assertTrue(m['core'].preview_event(state, proposal['event'], root)['ok'])
            self.assertEqual(reports[0], reports[1])

    def test_hash_seeds_match_main_bytes(self):
        import os
        import subprocess
        import sys
        script = Path(__file__).with_name('profile_derive.py')
        hashes = []
        for seed in ('1', '7', '42'):
            for reference in (True, False):
                args = [sys.executable, str(script), '--count', '80', '--proposals', '35']
                if reference:
                    args.append('--reference')
                result = subprocess.run(args, env=dict(os.environ, PYTHONHASHSEED=seed),
                                        capture_output=True, text=True, encoding='utf-8', check=True)
                hashes.append(json.loads(result.stdout)['sha256'])
        self.assertEqual(len(set(hashes)), 1)

    def test_delta_errors_match_full_preview_and_proposals_stay_independent(self):
        with tempfile.TemporaryDirectory() as temp, modules() as m:
            root = Path(temp)
            state = record(root, m, count=80, proposals=2)
            proposal = m['derive'].derive(state, root)['proposals'][0]['event']
            before = copy.deepcopy(state)
            with m['ontology'].analysis_scope():
                check = m['core'].derived_event_validator(state, root)
                self.assertEqual(len(check.file_manifest), 120)
                check(proposal)
                check(proposal)  # No proposal reserves IDs for a later one.
                variants = []
                for field, value in [('id', 'T0'), ('object_ids', ['missing']),
                                     ('acceptance', []), ('status', 'done'),
                                     ('input_ids', ['missing']), ('generation', True),
                                     ('evidence', [{}]), ('title', 3),
                                     ('acceptance_rules', [{'id': 'invalid'}]),
                                     ('decision_ids', ['missing'])]:
                    payload = copy.deepcopy(proposal)
                    payload['tasks'][0][field] = value
                    variants.append(payload)
                variants.extend([{'action': 'reopen_task', 'actor': 'fixture', 'reason': 'synthetic',
                                  'task_id': key} for key in ('missing', 'T0')])
                for payload in variants:
                    outcomes = []
                    for call in (check, lambda e: m['core'].preview_event(state, e, root)):
                        try:
                            call(payload)
                        except ValueError as exc:
                            outcomes.append(('error', str(exc)))
                        else:
                            outcomes.append(('ok',))
                    self.assertEqual(outcomes[0], outcomes[1], payload)
            self.assertEqual(state, before)

    def test_large_code_graph_preserves_legacy_events_inputs_and_lanes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with modules() as m:
                state = record(root, m, count=400, proposals=35)
                add_code_graph(state, root)
                m['ontology'].invalidate(state)
                for work in state['tasks'][:4]:
                    state = m['core'].apply_event(state, {
                        'action': 'reopen_task', 'task_id': work['id'],
                        'actor': 'fixture', 'reason': 'Synthetic implementation'}, root)
            with modules(True) as m:
                reference = m['derive'].derive(state, root)
                lanes = m['derive'].lanes(state, root)
            with modules() as m:
                current = m['derive'].derive(state, root)
                self.assertEqual(m['derive'].lanes(state, root), lanes)
                expected = {p['task_id']: p for p in reference['proposals']}
                enriched = 0
                for proposal in current['proposals']:
                    old = expected[proposal['task_id']]
                    self.assertEqual(proposal['event'], old['event'])
                    self.assertTrue(all(item in proposal['inputs'] for item in old['inputs']))
                    obj = proposal['because'].get('object_id', '')
                    if obj.startswith('M') and 1 <= int(obj[1:]) <= 35:
                        n = int(obj[1:]) - 1
                        self.assertIn({'object_id': 'source-' + str(n),
                            'paths': ['src/graph-' + str(n) + '.py']}, proposal['inputs'])
                        self.assertEqual(proposal['verification']['paths'], ['tests/graph-' + str(n) + '.py'])
                        enriched += 1
                self.assertGreater(enriched, 20)
                state['objects'].reverse()
                state['relations'].reverse()
                m['ontology'].invalidate(state)
                self.assertEqual(m['derive'].derive(state, root), current)
                self.assertEqual(m['derive'].lanes(state, root), lanes)

    def test_enriched_large_graph_hash_seeds_are_deterministic(self):
        import os
        import subprocess
        import sys
        script = Path(__file__).with_name('profile_derive.py')
        hashes = []
        for seed in ('1', '7', '42'):
            result = subprocess.run([sys.executable, str(script), '--count', '400',
                '--proposals', '35', '--graph'], env=dict(os.environ, PYTHONHASHSEED=seed),
                capture_output=True, text=True, encoding='utf-8', check=True)
            hashes.append(json.loads(result.stdout)['sha256'])
        self.assertEqual(len(set(hashes)), 1)
