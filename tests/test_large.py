"""w102: bounded analysis work and frozen-runtime byte equivalence."""
import copy
import json
import os
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

from large_fixtures import large_record, replacement, runtime
from ontology_fixtures import event
from derive_compat import legacy_report


class LargeRecordTests(unittest.TestCase):
    def test_graph_validation_is_bounded_per_transaction(self):
        with tempfile.TemporaryDirectory() as temp, runtime() as m:
            root = Path(temp)
            state = large_record(root, m, count=100, task_count=5)
            with patch.object(m['ontology'], '_validate_graph', wraps=m['ontology']._validate_graph) as check:
                m['core'].preview_event(state, replacement(state), root)
                self.assertLessEqual(check.call_count, 4, 'unchanged graph repeatedly validated')

    def test_twenty_seeded_sequences_match_frozen_runtime_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with runtime(True) as old:
                initial = large_record(root, old, count=12, task_count=3)
            for seed in range(20):
                rng = random.Random(seed)
                states = [copy.deepcopy(initial), copy.deepcopy(initial)]
                for step in range(8):
                    payload = replacement(states[0], rng.randrange(12), seed * 10 + step + 1)
                    if step == 1:
                        payload = event('sync_code_graph', operations=[])
                    if step == 2:
                        payload['operations'][0]['object']['properties']['version'] = True
                    if step == 4:
                        obj = copy.deepcopy(states[0]['objects'][rng.randrange(12)])
                        obj['label'] += ' display'
                        payload = event('mutate_graph', operations=[{'op': 'replace_object', 'object': obj}])
                    if step == 5:
                        payload = event('mutate_graph', operations=[{'op': 'remove_relation',
                                                                   'relation_id': 'R0-1'}])
                    if step == 6:
                        payload = event('mutate_graph', operations=[{'op': 'remove_object', 'object_id': 'M1'}])
                    if step == 7:
                        definition = copy.deepcopy(states[0]['ontology'])
                        definition['relation_types'][0]['impact'] = 'none'
                        payload = event('mutate_graph', operations=[{'op': 'replace_ontology', 'ontology': definition}])
                    results = []
                    for reference, state in zip((True, False), states):
                        with runtime(reference) as m:
                            core = m['core']
                            before = m['project'].json_text(state).encode()
                            try:
                                preview = core.preview_event(state, payload, root)
                                new = core.apply_event(state, payload, root)
                            except ValueError as exc:
                                results.append(('error', str(exc), before))
                            else:
                                persisted = root / ('reference-state.json' if reference else 'candidate-state.json')
                                m['project'].atomic_write(persisted, m['project'].json_text(new))
                                results.append(('ok', persisted.read_bytes(),
                                                json.dumps(preview, sort_keys=True, ensure_ascii=False).encode(),
                                                core.render_context(new, root).encode(),
                                                json.dumps(legacy_report(m['derive'].derive(new, root)), sort_keys=True).encode(),
                                                json.dumps(m['derive'].lanes(new, root), sort_keys=True).encode()))
                                states[reference is False] = new
                            self.assertEqual(m['project'].json_text(state).encode(), before)
                    self.assertEqual(results[0], results[1], (seed, step))

    def test_file_drift_same_size_restored_mtime_is_detected(self):
        with tempfile.TemporaryDirectory() as temp, runtime() as m:
            root = Path(temp)
            state = large_record(root, m, count=12, task_count=3)
            file = root / 'module-0.py'
            info = file.stat()
            before = m['core'].inspect_state(state, root)
            content = file.read_bytes()
            file.write_bytes(content.replace(b'0', b'9'))
            os.utime(file, ns=(info.st_atime_ns, info.st_mtime_ns))
            after = m['core'].inspect_state(state, root)
            self.assertEqual(before['tasks'][0]['effective_status'], 'done')
            self.assertEqual(after['tasks'][0]['effective_status'], 'needs_review')
            self.assertTrue(any('file changed' in s for s in after['tasks'][0]['issues']))


    def test_scoped_file_cache_checks_drift_and_never_caches_errors(self):
        with tempfile.TemporaryDirectory() as temp, runtime() as m:
            root = Path(temp)
            file = root / 'evidence.txt'
            file.write_bytes(b'first')
            info = file.stat()
            with m['ontology'].analysis_scope():
                original = m['core']._hash_file(root, 'evidence.txt')
                if os.name != 'nt':
                    with patch.object(Path, 'open', side_effect=AssertionError('hash reread')):
                        self.assertEqual(original, m['core']._hash_file(root, 'evidence.txt'))
                file.write_bytes(b'other')
                os.utime(file, ns=(info.st_atime_ns, info.st_mtime_ns))
                self.assertNotEqual(original, m['core']._hash_file(root, 'evidence.txt'))
                file.unlink()
                with self.assertRaises(ValueError):
                    m['core']._hash_file(root, 'evidence.txt')
                file.write_bytes(b'first')
                self.assertEqual(original, m['core']._hash_file(root, 'evidence.txt'))

    def test_mutated_records_and_cardinality_still_reject_like_reference(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with runtime(True) as old:
                initial = large_record(root, old, count=12, task_count=3)
            bad_states = []
            for bad_value in (True, float('nan'), {'bad': object()}):
                bad = copy.deepcopy(initial)
                bad['objects'][0]['properties']['version'] = bad_value
                bad_states.append(bad)
            bad = copy.deepcopy(initial)
            bad['relations'].append(copy.deepcopy(bad['relations'][0]))
            bad_states.append(bad)
            bad = copy.deepcopy(initial)
            bad['ontology']['relation_types'][0]['from_min'] = 3
            bad_states.append(bad)
            for bad in bad_states:
                errors = []
                for reference in (True, False):
                    with runtime(reference) as m:
                        with self.assertRaises(ValueError) as caught:
                            m['core'].validate(bad)
                        errors.append(str(caught.exception))
                self.assertEqual(errors[0], errors[1])


    def test_only_changed_object_properties_are_revalidated(self):
        with tempfile.TemporaryDirectory() as temp, runtime() as m:
            root = Path(temp)
            state = large_record(root, m, count=100, task_count=5)
            candidate = copy.deepcopy(state)
            candidate['objects'][0]['properties']['version'] = 1
            with m['ontology'].analysis_scope():
                m['ontology'].validate_graph(state)
                with patch.object(m['ontology'], '_property_value',
                                  wraps=m['ontology']._property_value) as check:
                    m['ontology'].validate_graph(candidate)
                    self.assertEqual(check.call_count, 2)
                candidate['objects'][0]['properties']['version'] = True
                m['ontology'].invalidate(candidate)
                with self.assertRaisesRegex(ValueError, 'expected integer'):
                    m['ontology'].validate_graph(candidate)


    def test_non_json_types_cannot_alias_cached_valid_records(self):
        class Text(str):
            pass
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with runtime(True) as m:
                initial = large_record(root, m, count=12, task_count=3)
            for field, value in (('label', Text('Module 0')),
                                 ('properties', {'path': Text('module-0.py'), 'version': 0}),
                                 ('id', ['M0'])):
                first = copy.deepcopy(initial['objects'][1])
                first['properties']['version'] = 1
                bad = copy.deepcopy(initial['objects'][0])
                bad[field] = value
                payload = event('mutate_graph', operations=[
                    {'op': 'replace_object', 'object': first},
                    {'op': 'replace_object', 'object': bad}])
                errors = []
                for reference in (True, False):
                    with runtime(reference) as m:
                        with self.assertRaises(ValueError) as caught:
                            m['core'].apply_event(initial, payload, root)
                        errors.append(str(caught.exception))
                self.assertEqual(errors[0], errors[1])
            # A tuple serializes like an array but violates string_list.
            initial['ontology']['object_types'][0]['properties']['tags'] = {
                'type': 'string_list', 'required': False, 'enum': None}
            initial['objects'][0]['properties']['tags'] = ['tag']
            bad = copy.deepcopy(initial['objects'][0])
            bad['properties']['tags'] = ('tag',)
            payload = event('mutate_graph', operations=[
                {'op': 'replace_object', 'object': bad}])
            errors = []
            for reference in (True, False):
                with runtime(reference) as m:
                    with self.assertRaises(ValueError) as caught:
                        m['core'].apply_event(initial, payload, root)
                    errors.append(str(caught.exception))
            self.assertEqual(errors[0], errors[1])


    def test_cli_scope_preserves_final_file_drift_guard(self):
        import argparse
        with tempfile.TemporaryDirectory() as temp, runtime() as m:
            root = Path(temp)
            state = large_record(root, m, count=12, task_count=3)
            folder = root / '.project'
            (folder / 'scripts').mkdir(parents=True)
            for name in ('core', 'project'):
                (folder / 'scripts' / (name + '.py')).write_text('# synthetic marker')
            original_bytes = m['project'].json_text(state).encode()
            (folder / 'state.json').write_bytes(original_bytes)
            payload = event('sync_code_graph', operations=[])
            path = root / 'event.json'
            path.write_text(json.dumps(payload))
            args = argparse.Namespace(root=str(root), event=str(path), expected_revision=1,
                                      preview_digest=None)
            render = m['core'].render_context
            def edit_after_render(*args):
                text = render(*args)
                file = root / 'module-0.py'
                info = file.stat()
                file.write_bytes(file.read_bytes().replace(b'0', b'9'))
                os.utime(file, ns=(info.st_atime_ns, info.st_mtime_ns))
                return text
            with patch.object(m['core'], 'render_context', side_effect=edit_after_render):
                with self.assertRaisesRegex(ValueError, 'Files changed during apply'):
                    m['project'].apply_command(args)
            self.assertEqual((folder / 'state.json').read_bytes(), original_bytes)


    def test_windows_creation_ctime_does_not_enable_hash_reuse(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temp, runtime() as m:
            root = Path(temp)
            (root / 'evidence.txt').write_bytes(b'synthetic')
            original_open = Path.open
            with m['ontology'].analysis_scope(), patch.object(m['core'], 'os', SimpleNamespace(name='nt')):
                with patch.object(Path, 'open', autospec=True, side_effect=original_open) as opened:
                    first = m['core']._hash_file(root, 'evidence.txt')
                    second = m['core']._hash_file(root, 'evidence.txt')
                    self.assertEqual(first, second)
                    self.assertEqual(opened.call_count, 2)
