"""Deterministik kod grafının davranış sözleşmesi."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/orvant/scripts'
sys.path.insert(0, str(SCRIPTS))
import core
from ontology_fixtures import build_notebook, task, event, evidence


class GraphTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = build_notebook()
        self.state.update(objects=[], relations=[], tasks=[])
        self.state['ontology'] = {'object_types': [], 'relation_types': []}

    def write(self, path, text):
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding='utf-8')

    def generate(self, **kwargs):
        import graph
        event, report = graph.generate(self.root, self.state, **kwargs)
        self.assert_applicable(event)
        return event, report

    def assert_applicable(self, event, state=None):
        """Üretilen her olay, değişiklik ve boş koşu dahil, iki kapıdan geçer."""
        state = self.state if state is None else state
        preview = core.preview_event(state, event, self.root)
        applied = core.apply_event(state, event, self.root)
        self.assertEqual(preview['next_revision'], applied['revision'])
        return applied

    def test_graph_root_may_be_reached_through_directory_symlink(self):
        import graph
        real = self.root / 'real'
        real.mkdir()
        (real / 'module.py').write_text('def h(): pass\n', encoding='utf-8')
        linked = self.root / 'linked'
        linked.symlink_to(real, target_is_directory=True)

        event, report = graph.generate(linked, self.state)

        self.assertEqual(report['modules'], 1)
        self.assertEqual([op['object']['properties']['name'] for op in event['operations']
                          if op['op'] == 'add_object' and op['object']['type'] == 'code_symbol'], ['h'])

    def test_windows_separators_normalize_to_internal_posix_paths(self):
        import graph
        self.assertEqual(graph._js_target(r'src\component', {'src/component.ts'}),
                         'src/component.ts')

    def setup_w108(self):
        self.state['ontology']['object_types'] = [{'id': 'Domain', 'label': 'Domain',
            'properties': {'source': {'type': 'json', 'required': True, 'enum': None}}}]
        for name in ('a', 'b', 'c'):
            self.write(name + '.py', ('import ' + chr(ord(name) + 1) + '\n') if name != 'c' else 'def f(): pass\n')
            self.state['objects'].append({'id': 'domain-' + name, 'type': 'Domain',
                'label': name, 'properties': {'source': {'path': name + '.py'}}})
            self.state['tasks'].append(task('check-' + name, ['domain-' + name], []))
        self.write('test_b.py', 'import b\n')
        self.state = core.apply_event(self.state, self.generate()[0], self.root)
        for name in ('a', 'b', 'c'):
            identifier = 'check-' + name
            self.write('inceleme-' + identifier + '.md', 'Synthetic review\n')
            for action, fields in [('start_task', {}), ('submit_evidence', {'items': evidence(identifier)}), ('complete_task', {})]:
                self.state = core.apply_event(self.state, event(action, task_id=identifier, **fields), self.root)
        self.assertEqual(self.w108_statuses(self.state), {'check-a': 'done', 'check-b': 'done', 'check-c': 'done'})

    def w108_statuses(self, state):
        return {t['id']: t['effective_status'] for t in core.inspect_state(state, self.root)['tasks']}

    def setup_w108_hub(self):
        self.setup_w108()
        for name in ('d', 'e'):
            self.write(name + '.py', '')
            self.state['objects'].append({'id': 'domain-' + name, 'type': 'Domain',
                'label': name, 'properties': {'source': {'path': name + '.py'}}})
            self.state['tasks'].append(task('check-' + name, ['domain-' + name], []))
        self.write('test_b.py', 'import a, b, c, d, e\n')
        self.state = core.apply_event(self.state, self.generate()[0], self.root)
        for name in ('a', 'b', 'c', 'd', 'e'):
            identifier = 'check-' + name
            self.write('inceleme-' + identifier + '.md', 'Synthetic review\n')
            for action, fields in [('start_task', {}), ('submit_evidence', {'items': evidence(identifier)}), ('complete_task', {})]:
                self.state = core.apply_event(self.state, event(action, task_id=identifier, **fields), self.root)

    def test_w108_hub_module_change_only_own_task(self):
        self.setup_w108_hub()
        self.write('b.py', 'import c\n# comment\n')
        delta, report = self.generate()
        preview = core.preview_event(self.state, delta, self.root)
        self.assertEqual([t['task_id'] for t in preview['impact']['affected_tasks']], ['check-b'])
        statuses = self.w108_statuses(core.apply_event(self.state, delta, self.root))
        self.assertNotEqual(statuses.pop('check-b'), 'done')
        self.assertEqual(set(statuses.values()), {'done'})
        self.assertEqual(report['direct_tests'], ['test_b.py'])
        self.assertIn('test_b.py', report['review_candidates'])

    def test_w108_hub_test_change_all_five_tasks(self):
        self.setup_w108_hub()
        self.write('test_b.py', 'import a, b, c, d, e\n# comment\n')
        delta, _ = self.generate()
        preview = core.preview_event(self.state, delta, self.root)
        self.assertEqual({t['task_id'] for t in preview['impact']['affected_tasks']},
                         {'check-' + name for name in 'abcde'})
        self.assertNotIn('done', self.w108_statuses(core.apply_event(self.state, delta, self.root)).values())

    def test_w108_migration_precedes_separate_source_event(self):
        import graph
        self.setup_w108_hub()
        for definition in self.state['ontology']['relation_types']:
            if definition['id'] == 'tests':
                definition['impact'] = 'both'
        self.write('b.py', 'import c\n# comment\n')
        delta, report = graph.generate(self.root, self.state)
        migration = report['migration_event']
        self.assertEqual([op['op'] for op in migration['operations']], ['replace_ontology'])
        self.assertIsNone(delta)
        migrated = self.assert_applicable(migration)
        self.assertEqual(migrated['objects'], self.state['objects'])
        self.assertEqual(migrated['relations'], self.state['relations'])
        preview = core.preview_event(self.state, migration, self.root)
        self.assertEqual(report['migration_object_count'], len(preview['impact']['affected_objects']))
        self.assertEqual(report['migration_task_count'], len(preview['impact']['affected_tasks']))
        self.assertGreater(report['migration_task_count'], 0)
        delta, report = graph.generate(self.root, migrated)
        self.assertIsNone(report['migration_event'])
        self.assertFalse(any(op['op'] == 'replace_ontology' for op in delta['operations']))
        applied = self.assert_applicable(delta, migrated)
        self.assertEqual(applied['revision'], self.state['revision'] + 2)
        self.assertEqual(applied['history'][-2]['reason'], migration['reason'])
        source_preview = core.preview_event(migrated, delta, self.root)
        self.assertEqual([t['task_id'] for t in source_preview['impact']['affected_tasks']], ['check-b'])
        self.state = applied
        self.assertIsNone(graph.generate(self.root, self.state)[1]['migration_event'])

    def test_w108_a_change_only_own_grounds_and_one_hop_review(self):
        self.setup_w108()
        self.write('a.py', 'import b\n# change\n')
        delta, report = self.generate()
        preview = core.preview_event(self.state, delta, self.root)
        new = core.apply_event(self.state, delta, self.root)
        statuses = self.w108_statuses(new)
        self.assertNotEqual(statuses['check-a'], 'done')
        self.assertEqual(statuses['check-b'], 'done')
        self.assertEqual(statuses['check-c'], 'done')
        self.assertEqual([x['task_id'] for x in preview['impact']['affected_tasks']], ['check-a'])
        self.assertEqual(report['review_candidates'], ['b.py'])
        self.assertEqual(report['review_candidate_count'], 1)
        self.assertEqual(report['direct_importers'], [])

    def test_w108_b_change_does_not_stale_importer_or_dependency(self):
        self.setup_w108()
        self.write('b.py', 'import c\n# change\n')
        delta, report = self.generate()
        new = core.apply_event(self.state, delta, self.root)
        statuses = self.w108_statuses(new)
        self.assertEqual(statuses['check-a'], 'done')
        self.assertNotEqual(statuses['check-b'], 'done')
        self.assertEqual(statuses['check-c'], 'done')
        self.assertEqual(report['direct_importers'], ['a.py', 'test_b.py'])
        self.assertEqual(report['direct_importer_count'], 2)
        self.assertEqual(report['review_candidates'], ['a.py', 'c.py', 'test_b.py'])

    def test_w108_test_change_stales_tested_module_task(self):
        self.setup_w108()
        self.write('test_b.py', 'import b\n# new test\n')
        delta, _ = self.generate()
        statuses = self.w108_statuses(core.apply_event(self.state, delta, self.root))
        self.assertNotEqual(statuses['check-b'], 'done')
        self.assertEqual(statuses['check-a'], 'done')
        self.assertEqual(statuses['check-c'], 'done')

    def test_w108_defines_is_context_and_legacy_types_are_updated(self):
        self.write('a.py', 'def f(): pass\n')
        self.state = core.apply_event(self.state, self.generate()[0], self.root)
        for definition in self.state['ontology']['relation_types']:
            definition['impact'] = {'imports': 'reverse', 'tests': 'reverse', 'defines': 'forward'}[definition['id']]
        import graph
        delta, report = graph.generate(self.root, self.state, include=['a.py'])
        self.assertIsNone(delta)
        migration = report['migration_event']
        self.assertEqual([op['op'] for op in migration['operations']], ['replace_ontology'])
        self.state = self.assert_applicable(migration)
        delta, _ = graph.generate(self.root, self.state, include=['a.py'])
        self.assertEqual(delta['operations'], [])
        self.assert_applicable(delta)
        self.assertEqual({d['id']: d['impact'] for d in self.state['ontology']['relation_types']},
                         {'imports': 'none', 'tests': 'forward', 'defines': 'none'})
        self.assertEqual(self.generate()[0]['operations'], [])
        self.write('a.py', 'def f(): return 1\n')
        delta, report = self.generate()
        preview = core.preview_event(self.state, delta, self.root)
        symbol = next(o['id'] for o in self.state['objects'] if o['type'] == 'code_symbol')
        self.assertNotIn(symbol, preview['impact']['affected_objects'])
        self.assertEqual(report['review_candidates'], [])

    def test_w108_review_filtered_deleted_and_noop(self):
        self.setup_w108()
        _, report = self.generate()
        self.assertEqual(report['review_candidates'], [])
        self.assertEqual(report['review_candidate_count'], 0)
        self.write('b.py', 'import c\n# change\n')
        delta, report = self.generate(include=['b.py'])
        self.assertEqual(report['direct_importers'], ['a.py', 'test_b.py'])
        self.assertEqual(report['direct_dependencies'], ['c.py'])
        self.state = core.apply_event(self.state, delta, self.root)
        (self.root / 'b.py').unlink()
        delta, report = self.generate()
        self.assertEqual(report['direct_importers'], ['a.py', 'test_b.py'])
        self.assertEqual(report['direct_dependencies'], ['c.py'])
        self.state = core.apply_event(self.state, delta, self.root)
        self.assertEqual(self.generate()[1]['review_candidates'], [])

    def test_generate_rejects_inapplicable_event(self):
        self.write('a.py', '')
        self.state = core.apply_event(self.state, self.generate()[0], self.root)
        module = self.state['objects'][0]['id']
        event = {'action': 'sync_code_graph', 'actor': 'test-agent', 'reason': 'test',
                 'operations': [{'op': 'add_relation', 'relation': {
                     'id': 'broken', 'from': module, 'type': 'imports', 'to': 'absent'}}]}
        with patch('graph.generate', return_value=(event, {})):
            with self.assertRaisesRegex(ValueError, 'unknown object'):
                self.generate()

    def test_jsx_closing_and_self_closing_tags_preserve_graph(self):
        for extension in ('tsx', 'jsx'):
            with self.subTest(extension=extension), tempfile.TemporaryDirectory() as folder:
                self.root = Path(folder)
                self.write('web/a.' + extension, '')
                self.write('web/x.' + extension, '')
                path = 'web/comp.' + extension
                self.write(path, "import a from './a'; export function A(){return <div>hi</div>;} export function B(){return <span>x</span>;} export const C=1;")
                event, report = self.generate()
                new = core.apply_event(self.state, event, self.root)
                self.assertEqual({o['properties']['name'] for o in new['objects'] if o['type'] == 'code_symbol'}, {'A', 'B', 'C'})
                modules = {o['id']: o['properties']['path'] for o in new['objects'] if o['type'] == 'code_module'}
                self.assertEqual({(modules[r['from']], modules[r['to']]) for r in new['relations'] if r['type'] == 'imports'}, {(path, 'web/a.' + extension)})
                self.assertEqual(report['unresolved'], [])
                self.write(path, "export function A(){return <div><span/></div>;} export { x } from './x'; export const C=1;")
                event, report = self.generate()
                new = core.apply_event(self.state, event, self.root)
                modules = {o['id']: o['properties']['path'] for o in new['objects'] if o['type'] == 'code_module'}
                self.assertEqual({o['properties']['name'] for o in new['objects'] if o['type'] == 'code_symbol'}, {'A', 'C'})
                self.assertEqual({(modules[r['from']], modules[r['to']]) for r in new['relations'] if r['type'] == 'imports'}, {(path, 'web/x.' + extension)})
                self.assertEqual(report['unresolved'], [])

    def test_jsx_text_and_expression_containers(self):
        cases = ["<i>don't</i>", '<p>"quoted" it\'s {a ? \'x\' : "y"}</p>',
                 "<A>{items.map(i => <B key={i}>{i}'s</B>)}</A>",
                 '<>\' " ` <C/> {<D/>}</>', '<C/>',
                 '<A title="it\'s">// import "./fake"; export function fake() {} `</A>']
        for extension in ('tsx', 'jsx', 'js'):
            for body in cases:
                with self.subTest(extension=extension, body=body), tempfile.TemporaryDirectory() as folder:
                    self.root = Path(folder)
                    self.write('f.js', '')
                    self.write('fake.js', '')
                    self.write('main.' + extension, 'const view = ' + body + ";\nimport q from './f';\nexport function h() {}\n")
                    event, report = self.generate()
                    new = self.assert_applicable(event)
                    self.assertEqual([o['properties']['name'] for o in new['objects'] if o['type'] == 'code_symbol'], ['h'])
                    modules = {o['id']: o['properties']['path'] for o in new['objects'] if o['type'] == 'code_module'}
                    self.assertEqual([(modules[r['from']], modules[r['to']]) for r in new['relations'] if r['type'] == 'imports'], [('main.' + extension, 'f.js')])
                    self.assertEqual(report['unresolved'], [])

    def test_jsx_expression_starts_and_typescript_generics(self):
        cases = [('js', prefix + " <i>don't</i>" + suffix) for prefix, suffix in
                 [('return (', ')'), ('const v =', ''), ('f(', ')'), ('f(a,', ')'),
                  ('a ?', ' : b'), ('a ? b :', ''), ('a &&', ''), ('a ||', ''), ('const f = () =>', '')]]
        cases += [('ts', 'const v = a < b && c > d; const xs: Array<string> = []; foo<Bar>(x)'),
                  ('tsx', 'const identity = <T,>(x: T) => x'),
                  ('tsx', 'const v = a < b && c > d; foo<Bar>(x)')]
        for extension, body in cases:
            with self.subTest(extension=extension, body=body), tempfile.TemporaryDirectory() as folder:
                self.root = Path(folder)
                self.write('f.js', '')
                self.write('main.' + extension, body + ";\nimport q from './f';\nexport function h() {}")
                event, report = self.generate()
                new = self.assert_applicable(event)
                self.assertEqual([o['properties']['name'] for o in new['objects'] if o['type'] == 'code_symbol'], ['h'])
                self.assertEqual(sum(r['type'] == 'imports' for r in new['relations']), 1)
                self.assertEqual(report['unresolved'], [])

    def test_jsx_unclosed_element_recovers_at_declaration(self):
        self.write('f.js', '')
        self.write('main.tsx', "const v = <i>don't\nimport q from './f';\nexport function h() {}")
        event, report = self.generate()
        new = self.assert_applicable(event)
        self.assertEqual([o['properties']['name'] for o in new['objects'] if o['type'] == 'code_symbol'], ['h'])
        self.assertEqual(sum(r['type'] == 'imports' for r in new['relations']), 1)
        self.assertTrue(report['unresolved'])

    def test_tsx_parenthesized_jsx_text_preserves_graph(self):
        cases = ["const cols = { label: <b>(beta) don't use</b> };",
                 "x ? null : <span>(optional) the user's name</span>",
                 "function f(type){ return <A>(it's)</A>; }",
                 'x ? 1 : <span>(none)</span>',
                 "const cols = { label: <A extends object>(none) user's</A> };"]
        for source in cases:
            with self.subTest(source=source):
                self.write('f.js', '')
                self.write('main.tsx', source + "\nimport q from './f';\nexport function h(){}")
                event, report = self.generate()
                new = self.assert_applicable(event)
                self.assertIn('h', {o['properties']['name'] for o in new['objects']
                                    if o['type'] == 'code_symbol'})
                modules = {o['id']: o['properties']['path'] for o in new['objects']
                           if o['type'] == 'code_module'}
                self.assertEqual([(modules[r['from']], modules[r['to']]) for r in new['relations']
                                  if r['type'] == 'imports'], [('main.tsx', 'f.js')])
                self.assertEqual(report['unresolved'], [])

    def test_unclosed_js_string_recovers_at_line_end(self):
        import graph
        for quote in ("'", '"'):
            for extension in ('tsx', 'ts', 'js'):
                with self.subTest(quote=quote, extension=extension):
                    source = 'const text = ' + quote + "unfinished\nimport q from './f';\nexport function h(){}"
                    symbols, dependencies, unresolved, _ = graph._javascript(
                        'main.' + extension, source.encode(), {'f.js'})
                    self.assertEqual(symbols, [('h', 'function', 3)])
                    self.assertEqual(dependencies, {'f.js'})
                    self.assertEqual(unresolved, [(1, quote + 'unfinished')])

    def test_tsx_type_generics_preserve_exports(self):
        cases = [
            ('interface Api {\n  get: <T>(url: string) => Promise<T>;\n}', {'h'}),
            ('type Props = { render: <T>(x: T) => JSX.Element };', {'h'}),
            ('class C { m: <T>(x: T) => T = (x) => x }', {'h'}),
            ('export function A() {\n const f = <T extends object>(x: T) => x;\n return null;\n}', {'A', 'h'}),
            ('type F = <T>(x: T) => T;', {'h'}),
            ('interface Api { <T>(x: T): T }', {'h'}),
            ('const f = <T = object>(x: T) => x;', {'h'}),
            ('const f = <T,>(x: T) => x;', {'h'}),
            ('type F = <T>(x: (value: T) => T) => T;', {'h'}),
            ('class C { m: <T>(x: T): Promise<T> => Promise<T> }', {'h'}),
            ('const f = <T extends object>(x: T): {value: T} => ({value: x});', {'h'}),
        ]
        for source, expected in cases:
            with self.subTest(source=source):
                self.write('main.tsx', source + '\nexport function h() {}')
                event, report = self.generate()
                new = self.assert_applicable(event)
                self.assertEqual({o['properties']['name'] for o in new['objects']
                                  if o['type'] == 'code_symbol'}, expected)
                self.assertEqual(report['unresolved'], [])

    def test_wrong_jsx_guess_restores_enclosing_delimiters(self):
        import graph
        # Tür bağlamı olmayan <T> bilerek JSX tahminine düşer; metin modu
        # hem fonksiyonun } belirtecini hem çağrının ) belirtecini yutar.
        source = 'export function A() {\n const f = call(<T>(x) => x);\n}\nexport function h() {}'
        self.write('main.tsx', source)
        event, report = self.generate()
        new = self.assert_applicable(event)
        self.assertEqual({o['properties']['name'] for o in new['objects']
                          if o['type'] == 'code_symbol'}, {'A', 'h'})
        self.assertTrue(report['unresolved'])
        punct = [value for kind, value, _ in graph._tokens(source, jsx=True) if kind == 'punct']
        self.assertEqual(punct.count('('), punct.count(')'))
        self.assertEqual(punct.count('{'), punct.count('}'))

    def test_tsx_many_attributes_scan_in_linear_time(self):
        import graph
        source = ('<C a="one" b="two" c="three"/>\n' * 40000)
        source = 'const view = <>\n' + source + '</>;\nexport function h() {}'
        started = time.perf_counter()
        symbols, _, unresolved, _ = graph._javascript('main.tsx', source.encode(), set())
        elapsed = time.perf_counter() - started
        self.assertEqual(symbols, [('h', 'function', 40003)])
        self.assertEqual(unresolved, [])
        self.assertLess(elapsed, 3.0)

    def test_tsx_many_generic_signatures_scan_in_linear_time(self):
        import graph
        source = ('type F = <T>(x: T) => T;\n' * 40000) + 'export function h() {}'
        started = time.perf_counter()
        symbols, _, unresolved, _ = graph._javascript('main.tsx', source.encode(), set())
        elapsed = time.perf_counter() - started
        self.assertEqual(symbols, [('h', 'function', 40001)])
        self.assertEqual(unresolved, [])
        self.assertLess(elapsed, 3.0)

    def test_jsx_containers_keep_javascript_dependencies(self):
        self.write('f.js', '')
        self.write('main.jsx', '''const view = <A {...{title: "don't", nested: {x: 1}}}>
{/['"}]/.test(s) ? <B>{import('./f')}</B> : a / b}
{items.map(i => { return <C>{i}'s</C>; })}</A>;
export function h() {}
''')
        event, report = self.generate()
        new = self.assert_applicable(event)
        self.assertEqual([o['properties']['name'] for o in new['objects'] if o['type'] == 'code_symbol'], ['h'])
        self.assertEqual(sum(r['type'] == 'imports' for r in new['relations']), 1)
        self.assertEqual(report['unresolved'], [])

    def test_jsx_unclosed_attribute_recovers_before_later_quote(self):
        self.write('f.js', '')
        self.write('main.jsx', 'const view = <C title="unfinished\nimport "./f";\nexport function h() {}')
        event, report = self.generate()
        new = self.assert_applicable(event)
        self.assertEqual([o['properties']['name'] for o in new['objects'] if o['type'] == 'code_symbol'], ['h'])
        self.assertEqual(sum(r['type'] == 'imports' for r in new['relations']), 1)
        self.assertTrue(report['unresolved'])

    def test_comparison_regex_and_unterminated_regex_recovery(self):
        self.write('a.ts', '')
        self.write('main.ts', 'const yes = a < /re/.source.length;\nexport const C=1; import "./a";')
        event, report = self.generate()
        new = core.apply_event(self.state, event, self.root)
        self.assertEqual([o['properties']['name'] for o in new['objects'] if o['type'] == 'code_symbol'], ['C'])
        self.assertEqual(sum(r['type'] == 'imports' for r in new['relations']), 1)
        self.assertEqual(report['unresolved'], [])
        self.write('main.ts', 'const broken = /unfinished\\\nexport const C=1; import "./a";\nconst later = /ok/;')
        event, report = self.generate()
        new = core.apply_event(self.state, event, self.root)
        self.assertEqual([o['properties']['name'] for o in new['objects'] if o['type'] == 'code_symbol'], ['C'])
        self.assertEqual(sum(r['type'] == 'imports' for r in new['relations']), 1)
        self.assertEqual(report['unresolved'], [{'file': 'main.ts', 'line': 1, 'token': '/unfinished\\'}])

    def test_mixed_exact_and_deterministic(self):
        self.write('pkg/__init__.py', '')
        self.write('pkg/base.py', 'class Base: pass\ndef run(): pass\n')
        self.write('pkg/test_base.py', 'from .base import run\nimport missing\n')
        self.write('web/index.ts', 'export const value = 1;\n')
        self.write('web/main.test.ts', '''// import './fake';
const text = "require('./fake')";
import {value} from './index';
export function go() { require('./index'); }
import(variable);
import('./absent');
''')
        event, report = self.generate()
        self.assertEqual((event, report), self.generate())
        self.assertEqual(json.dumps(event, sort_keys=True), json.dumps(self.generate()[0], sort_keys=True))
        new = core.apply_event(self.state, event, self.root)
        modules = {o['id']: o['properties']['path'] for o in new['objects'] if o['type'] == 'code_module'}
        symbols = {o['id']: (modules[o['properties']['module']], o['properties']['name'], o['properties']['kind'])
                   for o in new['objects'] if o['type'] == 'code_symbol'}
        self.assertEqual(set(modules.values()), {'pkg/__init__.py', 'pkg/base.py', 'pkg/test_base.py', 'web/index.ts', 'web/main.test.ts'})
        self.assertEqual(set(symbols.values()), {('pkg/base.py', 'Base', 'class'), ('pkg/base.py', 'run', 'function'),
                                               ('web/index.ts', 'value', 'const'), ('web/main.test.ts', 'go', 'function')})
        links = {(modules[r['from']], r['type'], modules.get(r['to'], symbols.get(r['to']))) for r in new['relations']}
        self.assertEqual(links, {('pkg/test_base.py', 'imports', 'pkg/base.py'), ('pkg/test_base.py', 'tests', 'pkg/base.py'),
                                ('web/main.test.ts', 'imports', 'web/index.ts'), ('web/main.test.ts', 'tests', 'web/index.ts')} |
                         {(m, 'defines', (m, n, k)) for m, n, k in symbols.values()})
        self.assertEqual([(x['file'], x['line'], x['token']) for x in report['unresolved']],
                         [('web/main.test.ts', 5, 'variable'), ('web/main.test.ts', 6, './absent')])
        self.assertEqual(report['external'], 1)
        self.state = new
        self.assertEqual(self.generate()[0]['operations'], [])
        self.write('pkg/base.py', 'class Base: pass\ndef run(): return 2\n')
        delta, _ = self.generate()
        self.assertEqual([x['op'] for x in delta['operations']], ['replace_object'])
        self.assertEqual(delta['operations'][0]['object']['properties']['path'], 'pkg/base.py')
        self.state = core.apply_event(self.state, delta, self.root)
        (self.root / 'pkg/base.py').unlink()
        deletion, _ = self.generate()
        deleted = core.apply_event(self.state, deletion, self.root)
        self.assertFalse(any(o['id'] in symbols and symbols[o['id']][0] == 'pkg/base.py' for o in deleted['objects']))
        self.assertFalse(any(o['type'] == 'code_module' and o['properties']['path'] == 'pkg/base.py' for o in deleted['objects']))

    def test_grounds_preview(self):
        self.state['ontology']['object_types'] = [{'id': 'Domain', 'label': 'Domain', 'properties': {'source': {'type': 'json', 'required': True, 'enum': None}}}]
        self.state['objects'] = [{'id': 'domain', 'type': 'Domain', 'label': 'domain', 'properties': {'source': {'path': 'consumer.py'}}}]
        self.write('base.py', 'def f(): pass\n')
        self.write('consumer.py', 'import base\n')
        first, _ = self.generate()
        self.state = core.apply_event(self.state, first, self.root)
        grounds = [r for r in self.state['relations'] if r['type'].startswith('grounds')]
        self.assertEqual([r['to'] for r in grounds], ['domain'])
        self.write('base.py', 'def f(): return 1\n')
        event, _ = self.generate()
        preview = core.preview_event(self.state, event, self.root)
        # Import bağlamı eskime yaymaz; yalnız consumer'ın kendi kaynağı yayar.
        self.assertNotIn('domain', preview['impact']['affected_objects'])
        self.state = core.apply_event(self.state, event, self.root)
        self.write('consumer.py', 'import base\n# change\n')
        preview = core.preview_event(self.state, self.generate()[0], self.root)
        self.assertIn('domain', preview['impact']['affected_objects'])
        self.assertTrue(any('grounds' in hop for hop in preview['impact']['paths']['domain']))

    def test_w111_migration_blocks_source_and_cli_persists_migration(self):
        import graph
        self.write('a.py', 'def f(): pass\n')
        self.state = core.apply_event(self.state, self.generate()[0], self.root)
        self.state.update(revision=0, history=[])
        for definition in self.state['ontology']['relation_types']:
            definition['impact'] = 'both'
        self.write('a.py', 'def f(): return 1\n')
        source, report = graph.generate(self.root, self.state)
        self.assertIsNone(source)
        migration = report['migration_event']
        self.assert_applicable(migration)
        spec = self.root / 'spec.json'
        spec.write_text(json.dumps(self.state), encoding='utf-8')
        def cli(*args):
            return subprocess.run([sys.executable, str(SCRIPTS / 'project.py'), *args],
                                  capture_output=True, text=True)
        initialized = cli('init', str(self.root), '--spec', str(spec))
        self.assertEqual(initialized.returncode, 0, initialized.stdout + initialized.stderr)
        output = self.root / 'source.json'
        # Önceki çıktı da göç gereken koşuda uygulanabilir kaynak sanılmamalı.
        output.write_text('{"old": true}', encoding='utf-8')
        before = (self.root / '.project/state.json').read_bytes()
        blocked = cli('graph', str(self.root), '--out', str(output))
        self.assertEqual(blocked.returncode, 1, blocked.stdout + blocked.stderr)
        self.assertFalse(output.exists())
        migration_path = Path(str(output) + '.migration.json')
        self.assertEqual(json.loads(migration_path.read_text()), migration)
        self.assertIn('önce göç olayını uygula, sonra graph', json.loads(blocked.stdout)['next_step'])
        self.assertEqual((self.root / '.project/state.json').read_bytes(), before)
        preview = cli('preview', str(self.root), '--event', str(migration_path),
                      '--expected-revision', str(self.state['revision']))
        self.assertEqual(preview.returncode, 0, preview.stdout + preview.stderr)
        applied = cli('apply', str(self.root), '--event', str(migration_path),
                      '--expected-revision', str(self.state['revision']),
                      '--preview-digest', json.loads(preview.stdout)['preview_digest'])
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        rerun = cli('graph', str(self.root), '--out', str(output))
        self.assertEqual(rerun.returncode, 0, rerun.stdout + rerun.stderr)
        self.assertIsNone(json.loads(rerun.stdout)['migration_event'])
        migrated = core.apply_event(self.state, migration, self.root)
        source = json.loads(output.read_text())
        self.assert_applicable(source, migrated)
        self.assertTrue(any(op['op'] == 'replace_object' for op in source['operations']))

    def test_cli_bytes_filters_limit(self):
        self.write('a.py', 'def f(): pass\n')
        self.write('ignored/b.py', '')
        self.write('.gitignore', 'ignored/\n')
        self.write('node_modules/b.py', '')
        for name in ('one.json', 'two.json'):
            run = subprocess.run([sys.executable, str(SCRIPTS / 'project.py'), 'graph', str(self.root), '--out', str(self.root / name)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual((self.root / 'one.json').read_bytes(), (self.root / 'two.json').read_bytes())
        self.assertFalse((self.root / 'one.json.migration.json').exists())
        event = json.loads((self.root / 'one.json').read_text())
        self.assert_applicable(event)
        self.assertEqual([x['object']['properties']['path'] for x in event['operations'] if x['op'] == 'add_object' and x['object']['type'] == 'code_module'], ['a.py'])
        with self.assertRaisesRegex(ValueError, 'max-files'):
            self.generate(max_files=0)
        self.assertEqual(self.generate(include=['other/*'])[1]['modules'], 0)
        self.assertEqual(self.generate(exclude=['*.py'])[1]['modules'], 0)

    def test_scale(self):
        for n in range(200):
            self.write(f'm{n}.py', 'def f(): pass\n')
        event, report = self.generate()
        self.assertEqual(report['modules'], 200)
        self.assertEqual(report['symbols'], 200)
        self.assertEqual(report['relations'], 200)
        self.assertEqual(len(event['operations']), 601)

    def test_git_ignore_and_recreation(self):
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        self.write('.gitignore', '*.hidden.py\nignored/\n')
        self.write('keep.py', 'def f(): pass\n')
        self.write('x.hidden.py', '')
        self.write('ignored/x.py', '')
        first, report = self.generate()
        self.assertEqual(report['modules'], 1)
        self.state = core.apply_event(self.state, first, self.root)
        original = {o['id'] for o in self.state['objects']}
        empty, _ = self.generate()
        no_op = core.apply_event(self.state, empty, self.root)
        self.assertEqual(no_op['objects'], self.state['objects'])
        self.assertEqual(no_op['relations'], self.state['relations'])
        (self.root / 'keep.py').unlink()
        deletion, _ = self.generate()
        self.state = core.apply_event(self.state, deletion, self.root)
        self.write('keep.py', 'def f(): pass\n')
        restored, _ = self.generate()
        restored_state = core.apply_event(self.state, restored, self.root)
        self.assertEqual(len(restored_state['objects']), 2)
        self.assertTrue(original.isdisjoint({o['id'] for o in restored_state['objects']}))

    def test_import_forms_and_lexical_noise(self):
        self.write('pkg/__init__.py', '')
        self.write('pkg/base.py', 'def f(): pass\n')
        self.write('pkg/sub/__init__.py', '')
        self.write('pkg/sub/use.py', 'from ..base import f\nfrom pkg import base\n')
        self.write('lib/index.cjs', 'export class Item {}\n')
        self.write('noise.ts', r"const pattern = /require('.\/lib')/;" + '\n')
        self.write('main.spec.mjs', '''/* require('./ghost') */
const text = `import './ghost'`; const pattern = /require('ghost')/;
export { Item } from './lib';
import('./lib'); require('./lib');
import('./lib' + suffix); require(dynamic);
import thing from 'external-package';
''')
        event, report = self.generate()
        new = core.apply_event(self.state, event, self.root)
        modules = {o['id']: o['properties']['path'] for o in new['objects'] if o['type'] == 'code_module'}
        imports = {(modules[r['from']], modules[r['to']]) for r in new['relations'] if r['type'] == 'imports'}
        self.assertEqual(imports, {('pkg/sub/use.py', 'pkg/base.py'), ('main.spec.mjs', 'lib/index.cjs')})
        self.assertEqual([(x['line'], x['token']) for x in report['unresolved']], [(5, './lib'), (5, 'dynamic')])

    def test_initialized_cli_preview_apply_and_protected_output(self):
        self.write('a.py', 'def f(): pass\n')
        spec = self.root / 'spec.json'
        spec.write_text(json.dumps(self.state), encoding='utf-8')
        def cli(*args):
            result = subprocess.run([sys.executable, str(SCRIPTS / 'project.py'), *args], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return json.loads(result.stdout)
        cli('init', str(self.root), '--spec', str(spec))
        copied = self.root / '.project/scripts/graph.py'
        self.assertTrue(copied.is_file())
        output = self.root / 'event.json'
        cli('graph', str(self.root), '--out', str(output))
        self.assert_applicable(json.loads(output.read_text()))
        preview = cli('preview', str(self.root), '--event', str(output), '--expected-revision', '0')
        self.assertEqual(len(preview['impact']['changed_objects']), 2)
        cli('apply', str(self.root), '--event', str(output), '--expected-revision', '0', '--preview-digest', preview['preview_digest'])
        cli('graph', str(self.root), '--out', str(output))
        import project
        self.assert_applicable(json.loads(output.read_text()), project.load_project(self.root))
        self.assertEqual(json.loads(output.read_text())['operations'], [])
        old = (self.root / '.project/state.json').read_bytes()
        result = subprocess.run([sys.executable, str(SCRIPTS / 'project.py'), 'graph', str(self.root), '--out', str(self.root / '.project/state.json')], capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual((self.root / '.project/state.json').read_bytes(), old)

    def test_nested_gitignore_and_export_constants(self):
        self.write('.gitignore', '/root.py\nblocked/\n*.hidden.py\n!keep.hidden.py\n')
        self.write('root.py', '')
        self.write('nested/root.py', '')
        self.write('blocked/keep.hidden.py', '')
        self.write('nested/.gitignore', '*.py\n!allow.py\n')
        self.write('nested/allow.py', '')
        self.write('keep.hidden.py', '')
        self.write('nested/deep/drop.py', '')
        self.write('symbols.ts', "export const a = {x: 1, y: 2}, b = [1, 2];\nexport function f() { const from = './ghost'; }\n")
        event, report = self.generate()
        self.assertEqual(report['unresolved'], [])
        new = core.apply_event(self.state, event, self.root)
        self.assertEqual({o['properties']['path'] for o in new['objects'] if o['type'] == 'code_module'},
                         {'nested/allow.py', 'keep.hidden.py', 'symbols.ts'})
        self.assertEqual({o['properties']['name'] for o in new['objects'] if o['type'] == 'code_symbol'}, {'a', 'b', 'f'})

    def test_sync_event_validation_preserves_mutation_guards(self):
        bad = {'action': 'sync_code_graph', 'actor': 'test-agent', 'reason': 'test', 'operations': [{'op': 'unknown'}]}
        with self.assertRaisesRegex(ValueError, 'unsupported graph operation'):
            core.apply_event(self.state, bad, self.root)
        bad['action'] = 'mutate_graph'
        bad['operations'] = []
        with self.assertRaisesRegex(ValueError, 'empty graph transaction'):
            core.apply_event(self.state, bad, self.root)
        bad['action'] = 'sync_code_graph'
        bad['operations'] = [{'op': 'add_relation', 'relation': {'id': 'broken', 'from': 'absent', 'type': 'imports', 'to': 'absent'}}]
        with self.assertRaises(ValueError):
            core.apply_event(self.state, bad, self.root)

    def test_filtered_deletion_removes_incoming_graph_links(self):
        self.write('a.py', 'import b\n')
        self.write('b.py', 'def f(): pass\n')
        first, _ = self.generate()
        self.state = core.apply_event(self.state, first, self.root)
        old_a = next(o for o in self.state['objects'] if o['type'] == 'code_module' and o['properties']['path'] == 'a.py')
        (self.root / 'b.py').unlink()
        deletion, _ = self.generate(include=['b.py'])
        new = core.apply_event(self.state, deletion, self.root)
        self.assertEqual(new['objects'], [old_a])
        self.assertEqual(new['relations'], [])

    def test_unscannable_import_targets_are_removed_with_links(self):
        empty = self.state
        for cause in ('gitignore', 'gitignore_git', 'dist', 'symlink'):
            with self.subTest(cause=cause), tempfile.TemporaryDirectory() as folder:
                self.root = Path(folder)
                self.state = empty
                if cause == 'gitignore_git':
                    subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
                self.write('a.py', 'import b\n')
                self.write('b.py', 'def f(): pass\n')
                self.state = core.apply_event(self.state, self.generate()[0], self.root)
                self.assertEqual(sum(r['type'] == 'imports' for r in self.state['relations']), 1)
                if cause.startswith('gitignore'):
                    self.write('.gitignore', 'b.py\n')
                elif cause == 'dist':
                    (self.root / 'dist').mkdir(exist_ok=True)
                    (self.root / 'b.py').rename(self.root / 'dist/b.py')
                else:
                    (self.root / 'b.py').rename(self.root / 'target.txt')
                    (self.root / 'b.py').symlink_to('target.txt')
                event, report = self.generate()
                core.preview_event(self.state, event, self.root)
                new = core.apply_event(self.state, event, self.root)
                self.assertEqual([o['properties']['path'] for o in new['objects'] if o['type'] == 'code_module'], ['a.py'])
                self.assertFalse(any(o['type'] == 'code_symbol' for o in new['objects']))
                self.assertEqual(new['relations'], [])
                self.assertEqual(report['modules'], 1)

    def test_regex_expression_starts_and_division(self):
        prefixes = ['(s: string) =>', 'ready &&', 'ready ||', '{', '}', '+', '-', '*', '%', '<', '>', '&', '|', '^', '~',
                    'typeof', 'void', 'delete', 'in', 'of', 'new', 'throw', 'yield', 'await', 'return', 'case', 'else', 'do']
        self.write('web/fake.ts', '')
        for prefix in prefixes:
            with self.subTest(prefix=prefix):
                statement = 'function f() {' if prefix == '{' else 'function f() {}' if prefix == '}' else 'const f = ' + prefix
                closing = '}' if prefix == '{' else ''
                self.write('web/main.ts', statement + ' /"/.test(s);' + closing + '\nconst msg = "import y from \'./fake\'";\nexport function main() {}\n')
                event, report = self.generate()
                new = core.apply_event(self.state, event, self.root)
                self.assertFalse(any(r['type'] == 'imports' for r in new['relations']))
                self.assertEqual([o['properties']['name'] for o in new['objects'] if o['type'] == 'code_symbol'], ['main'])
                self.assertEqual(report['unresolved'], [])
        self.write('web/main.ts', 'const a = a / b / c; const x = (x) / 2; import "./fake"; export function main() {}')
        event, _ = self.generate()
        new = core.apply_event(self.state, event, self.root)
        self.assertEqual(sum(r['type'] == 'imports' for r in new['relations']), 1)
        self.assertEqual([o['properties']['name'] for o in new['objects'] if o['type'] == 'code_symbol'], ['main'])

    def test_include_preserves_import_targets_and_identity(self):
        self.write('a.py', 'import b\n')
        self.write('b.py', 'def f(): pass\n')
        self.state = core.apply_event(self.state, self.generate()[0], self.root)
        original = self.state
        self.write('a.py', 'import b\n# changed\n')
        event, _ = self.generate(include=['a.py'])
        self.assertEqual([op['op'] for op in event['operations']], ['replace_object'])
        self.state = core.apply_event(self.state, event, self.root)
        self.assertEqual(self.state['relations'], original['relations'])
        self.assertEqual(self.generate()[0]['operations'], [])
        self.assertEqual({o['id'] for o in self.state['objects']}, {o['id'] for o in original['objects']})

    def test_python_external_and_dynamic_imports(self):
        self.write('b.py', '')
        self.write('a.py', 'import os\nfrom pathlib import Path\nimport missing_package\nimport importlib\nimportlib.import_module("b")\n__import__("b")\nimportlib.import_module(variable)\n__import__(other)\nfrom .absent import thing\n')
        self.write('web.ts', 'import thing from "some-package";')
        event, report = self.generate()
        self.assertEqual(report['unresolved'], [
            {'file': 'a.py', 'line': 7, 'token': 'variable'},
            {'file': 'a.py', 'line': 8, 'token': 'other'},
            {'file': 'a.py', 'line': 9, 'token': '.absent'}])
        self.assertEqual(report['external'], 5)
        new = core.apply_event(self.state, event, self.root)
        modules = {o['id']: o['properties']['path'] for o in new['objects'] if o['type'] == 'code_module'}
        self.assertEqual([(modules[r['from']], modules[r['to']]) for r in new['relations'] if r['type'] == 'imports'], [('a.py', 'b.py')])

    def test_from_fields_are_not_import_requests(self):
        self.write('web.ts', 'export interface X { from: string }\nexport type Y = { from: string };\nexport const obj = { from: "./absent" };\n')
        event, report = self.generate()
        self.assertEqual(report['unresolved'], [])
        self.assertFalse(any(op.get('relation', {}).get('type') == 'imports' for op in event['operations']))

    def test_empty_sync_preserves_revision_and_history(self):
        self.write('a.py', '')
        self.state = core.apply_event(self.state, self.generate()[0], self.root)
        event, _ = self.generate()
        self.assertEqual(event['operations'], [])
        result = core.apply_event(self.state, event, self.root)
        self.assertEqual(result, self.state)
        self.assertEqual(result['revision'], self.state['revision'])
        self.assertEqual(result['history'], self.state['history'])

    def test_python_dynamic_relative_with_package(self):
        self.write('pkg/__init__.py', '')
        self.write('pkg/b.py', '')
        self.write('a.py', 'import importlib\nimportlib.import_module(".b", "pkg")\nimportlib.import_module(".b", package="pkg")\nimportlib.import_module(".missing", "pkg")\n')
        event, report = self.generate()
        self.assertEqual(report['unresolved'], [{'file': 'a.py', 'line': 4, 'token': '.missing'}])
        new = core.apply_event(self.state, event, self.root)
        modules = {o['id']: o['properties']['path'] for o in new['objects'] if o['type'] == 'code_module'}
        self.assertEqual([(modules[r['from']], modules[r['to']]) for r in new['relations'] if r['type'] == 'imports'], [('a.py', 'pkg/b.py')])

    def test_empty_sync_preview_has_no_previous_change(self):
        self.write('a.py', '')
        self.state = core.apply_event(self.state, self.generate()[0], self.root)
        preview = core.preview_event(self.state, self.generate()[0], self.root)
        self.assertEqual(preview['revision'], preview['next_revision'])
        self.assertEqual(preview['change']['operations'], [])
        self.assertEqual(preview['impact']['changed_objects'], [])

    def import_pairs(self, event):
        new = core.apply_event(self.state, event, self.root)
        modules = {o['id']: o['properties']['path'] for o in new['objects'] if o['type'] == 'code_module'}
        return {(modules[r['from']], modules[r['to']]) for r in new['relations'] if r['type'] == 'imports'}

    def test_w105_esm_extensions(self):
        targets = ['src/types.ts', 'src/view.tsx', 'src/mod.mts', 'src/esm.mts', 'src/common.cts', 'src/real.js', 'src/real.ts']
        for path in targets:
            self.write(path, '')
        self.write('test/use.ts', '\n'.join(f'import "../src/{name}";' for name in ['types.js', 'view.js', 'mod.js', 'esm.mjs', 'common.cjs', 'real.js']))
        event, report = self.generate()
        self.assertEqual(self.import_pairs(event), {('test/use.ts', p) for p in targets if p != 'src/real.ts'})
        self.assertEqual(report['unresolved'], [])

    def test_w105_workspace_entries(self):
        packages = {
            '': {'name': '@demo/root', 'exports': './src/root.ts'},
            'packages/one': {'name': '@demo/one', 'exports': {'.': {'default': './src/default.ts', 'import': './src/import.js', 'types': './src/types.ts'}, './alt': './src/alt.js'}},
            'packages/two': {'name': '@demo/two', 'module': './src/mod.js', 'main': './src/main.ts', 'types': './src/types.ts'},
            'packages/three': {'name': '@demo/three'},
            'packages/empty': {'name': '@demo/empty'},
            'packages/condition': {'name': '@demo/condition', 'exports': {'import': './src/index.js', 'default': './src/default.ts'}},
        }
        for folder, manifest in packages.items():
            self.write((folder + '/' if folder else '') + 'package.json', json.dumps(manifest))
        targets = ['src/root.ts', 'packages/one/src/types.ts', 'packages/one/src/alt.ts', 'packages/two/src/mod.ts', 'packages/three/src/index.tsx', 'packages/condition/src/index.ts']
        for path in targets + ['packages/one/src/import.ts', 'packages/one/src/default.ts', 'packages/two/src/main.ts', 'packages/two/src/types.ts', 'packages/condition/src/default.ts']:
            self.write(path, '')
        requests = ['@demo/root', '@demo/one', '@demo/one/alt', '@demo/two', '@demo/three', '@demo/condition', '@demo/empty', '@demo/one/missing', 'outside']
        self.write('app.ts', '\n'.join(f'import "{name}";' for name in requests))
        event, report = self.generate()
        self.assertEqual(self.import_pairs(event), {('app.ts', p) for p in targets})
        self.assertEqual(report['external'], 1)
        self.assertEqual(report['local_package'], 6)
        self.assertEqual([x['token'] for x in report['unresolved']], ['@demo/empty', '@demo/one/missing'])
        self.assertTrue(all(x['reason'] == 'local package found, entry missing' for x in report['unresolved']))

    def test_w105_missing_build_exports_resolve_to_sources(self):
        self.write('packages/core/package.json', json.dumps({'name': '@kapsam/core', 'exports': {
            '.': {'types': './dist/index.d.ts', 'default': './dist/index.js'},
            './hook': {'default': './dist/hook.js'},
            './view': './dist/view.js', './esm': './dist/esm.mjs',
            './nested': './dist/nested/index.js'}}))
        self.write('packages/core/tsconfig.json', json.dumps({'compilerOptions': {
            'outDir': 'dist', 'rootDir': 'src', 'paths': {}}}))
        targets = ['packages/core/src/' + name for name in
                   ('index.ts', 'hook.ts', 'view.tsx', 'esm.mts', 'nested/index.ts')]
        for target in targets:
            self.write(target, '')
        self.write('app.ts', '\n'.join(f'import "@kapsam/core{suffix}";'
                                     for suffix in ('', '/hook', '/view', '/esm', '/nested')))
        event, report = self.generate()
        self.assertEqual(self.import_pairs(event), {('app.ts', target) for target in targets})
        self.assertEqual((report['local_package'], report['unresolved']), (5, []))
        self.assertEqual((event, report), self.generate())

    def test_w105_build_entry_mapping_and_missing_sources(self):
        cases = [
            ('custom', {'main': './output/x.js'}, {'outDir': 'output', 'rootDir': 'source'}, 'source/x.ts', True),
            ('default', {'module': './dist/x.js'}, {'outDir': 'dist'}, 'src/x.ts', True),
            ('fallback-dist', {'types': './dist/x.d.ts'}, {}, 'src/x.ts', True),
            ('fallback-build', {'main': './build/x.js'}, {}, 'src/x.tsx', True),
            ('no-config', {'main': './dist/x.js'}, None, 'src/x.ts', True),
            ('lib', {'module': './lib/x.mjs'}, {}, 'src/x.mts', True),
            ('directory', {'exports': './dist/feature.js'}, {}, 'src/feature/index.ts', True),
            ('missing', {'exports': './dist/absent.js'}, {}, 'src/x.ts', False),
            ('outside', {'exports': './dist/x.js'}, {'outDir': 'output'}, 'src/x.ts', False),
            ('unknown', {'exports': './output/x.js'}, {}, 'src/x.ts', False),
            ('existing', {'exports': './lib/x.js'}, {}, 'src/x.ts', False),
        ]
        targets, missing = set(), []
        for name, fields, options, source, resolved in cases:
            base = 'packages/' + name
            self.write(base + '/package.json', json.dumps({'name': '@kapsam/' + name, **fields}))
            if options is not None:
                self.write(base + '/tsconfig.json', json.dumps({'compilerOptions': options}))
            self.write(base + '/' + source, '')
            if name == 'existing':
                self.write(base + '/lib/x.js', '')
                self.write(base + '/.gitignore', 'lib/\n')
            if resolved:
                targets.add(('app.ts', base + '/' + source))
            else:
                missing.append('@kapsam/' + name)
        self.write('app.ts', '\n'.join(f'import "@kapsam/{case[0]}";' for case in cases))
        event, report = self.generate()
        self.assertEqual(self.import_pairs(event), targets)
        self.assertEqual([item['token'] for item in report['unresolved']], missing)
        self.assertTrue(all(item['reason'] == 'local package found, entry missing'
                            for item in report['unresolved']))
        self.assertEqual(report['local_package'], len(targets))

    def test_w105_build_sources_deterministic_and_scale(self):
        import graph
        self.write('packages/core/package.json', '{"name":"@kapsam/core","exports":{".":{"types":"./dist/index.d.ts","default":"./dist/index.js"},"./hook":"./dist/hook.js"}}')
        self.write('packages/core/tsconfig.json', '{"compilerOptions":{"outDir":"dist","rootDir":"src"}}')
        self.write('packages/core/src/index.ts', '')
        self.write('packages/core/src/hook.ts', '')
        for n in range(1000):
            self.write(f'app/{n}/use.ts', 'import "@kapsam/core"; import "@kapsam/core/hook";')
        start = time.perf_counter()
        event, report = graph.generate(self.root, self.state)
        elapsed = time.perf_counter() - start
        self.assertEqual((report['local_package'], report['unresolved_count'], report['relations']),
                         (2000, 0, 2000))
        self.assertEqual(self.import_pairs(event), {
            (f'app/{n}/use.ts', f'packages/core/src/{name}.ts')
            for n in range(1000) for name in ('index', 'hook')})
        self.assertEqual((event, report), graph.generate(self.root, self.state))
        self.assertLess(elapsed, 3.0)

    def test_w105_build_config_origin_and_inheritance(self):
        self.write('packages/tsconfig.json', json.dumps({'compilerOptions': {
            'outDir': './output', 'rootDir': './sources',
            'paths': {'@kapsam/*': ['absent/*']}}}))
        self.write('packages/core/tsconfig.json', '{"extends":"../tsconfig.json","compilerOptions":{"paths":{}}}')
        self.write('packages/core/package.json', '{"name":"@kapsam/core","exports":"../output/x.js"}')
        self.write('packages/sources/x.ts', '')
        self.write('packages/plain/package.json', '{"name":"@kapsam/plain","exports":"../output/plain.js"}')
        self.write('packages/sources/plain.ts', '')
        self.write('app.ts', 'import "@kapsam/core"; import "@kapsam/plain";')
        # Paket config'inin boş paths değeri tabanı ezer; giriş eşlemesi bağımsızdır.
        self.write('packages/core/use.ts', 'import "@kapsam/core";')
        event, report = self.generate()
        self.assertEqual(self.import_pairs(event), {
            ('app.ts', 'packages/sources/x.ts'), ('app.ts', 'packages/sources/plain.ts'),
            ('packages/core/use.ts', 'packages/sources/x.ts')})
        self.assertEqual((report['alias'], report['local_package'], report['unresolved']), (0, 3, []))

    def test_w105_tsconfig_chain_and_nearest(self):
        self.write('config/base.json', '// configuration\n{"compilerOptions":{"baseUrl":"..", "paths":{"@/*":["absent/*", "src/*"], "exact":["src/root.js"]}},}')
        self.write('tsconfig.json', '{"extends":"./config/base"}')
        self.write('apps/tsconfig.json', '{"extends":"../tsconfig.json","compilerOptions":{"baseUrl":".","paths":{"@/*":["local/*"]}}}')
        self.write('src/root.ts', '')
        self.write('apps/local/root.tsx', '')
        self.write('root.ts', 'import "@/root.js"; import "exact"; import "src/root.js"; import "@/missing";')
        self.write('apps/use.ts', 'import "@/root.js";')
        self.write('other/tsconfig.json', '{"extends":"@config/tsconfig"}')
        self.write('other/use.ts', 'import "outside";')
        event, report = self.generate()
        self.assertEqual(self.import_pairs(event), {('root.ts', 'src/root.ts'), ('apps/use.ts', 'apps/local/root.tsx')})
        self.assertEqual(report['alias'], 4)
        self.assertEqual(report['external'], 1)
        self.assertEqual([x['token'] for x in report['unresolved']], ['@/missing'])
        self.assertEqual(report['config_warnings'], [{'file': 'other/tsconfig.json', 'reason': 'package extends skipped', 'token': '@config/tsconfig'}])

    def test_w105_grounds_all_file_properties(self):
        self.state['ontology']['object_types'] = [{'id': 'Domain', 'label': 'Domain', 'properties': {
            key: {'type': kind, 'required': True, 'enum': None} for key, kind in [('path', 'file'), ('secondary', 'file'), ('text', 'string')]}}]
        self.state['objects'] = [{'id': 'domain', 'type': 'Domain', 'label': 'domain', 'properties': {'path': 'a.ts', 'secondary': 'b.ts', 'text': 'c.ts'}}]
        for path in ['a.ts', 'b.ts', 'c.ts']:
            self.write(path, '')
        event, _ = self.generate()
        new = core.apply_event(self.state, event, self.root)
        modules = {o['id']: o['properties']['path'] for o in new['objects'] if o['type'] == 'code_module'}
        self.assertEqual({modules[r['from']] for r in new['relations'] if r['type'] == 'grounds:Domain'}, {'a.ts', 'b.ts'})

    def test_w105_report_and_monorepo_scale(self):
        import graph
        self.write('package.json', '{"name":"@demo/core","exports":"./src/index.ts"}')
        self.write('tsconfig.json', '{"compilerOptions":{"baseUrl":".","paths":{"@/*":["src/*"]}}}')
        self.write('src/index.ts', '')
        for n in range(1000):
            self.write(f'app/m{n}.ts', 'import "@demo/core"; import "@/index.js"; import "../src/index.js"; import "external"; import "./missing.js";')
        start = time.perf_counter()
        event, report = graph.generate(self.root, self.state)
        elapsed = time.perf_counter() - start
        self.assertEqual((report['local_package'], report['alias'], report['external'], report['unresolved_count']), (1000, 1000, 1000, 1000))
        self.assertEqual(report['relations'], 1000)
        self.assertEqual((event, report), graph.generate(self.root, self.state))
        self.assertLess(elapsed, 3.0)

    def test_w105_dotted_extensionless_import(self):
        self.write('src/component.test.ts', '')
        self.write('use.ts', 'import "./src/component.test";')
        event, report = self.generate()
        self.assertEqual(self.import_pairs(event), {('use.ts', 'src/component.test.ts')})
        self.assertEqual(report['unresolved'], [])

    def test_w105_workspace_fallbacks_and_subpaths(self):
        for name, fields, target in [
            ('main', {'main': 'entry.js', 'types': 'other.ts'}, 'entry.ts'),
            ('types', {'types': 'entry.ts'}, 'entry.ts'),
            ('default', {'exports': {'default': 'entry.js'}}, 'entry.ts'),
            ('index', {}, 'index.js'),
            ('plain', {}, 'src/index.js'),
        ]:
            self.write(f'packages/{name}/package.json', json.dumps({'name': name, **fields}))
            self.write(f'packages/{name}/{target}', '')
        self.write('packages/plain/extra.ts', '')
        self.write('use.ts', 'import "main"; import "types"; import "default"; import "index"; import "plain"; import "plain/extra.js";')
        event, report = self.generate()
        self.assertEqual(self.import_pairs(event), {('use.ts', p) for p in [
            'packages/main/entry.ts', 'packages/types/entry.ts', 'packages/default/entry.ts',
            'packages/index/index.js', 'packages/plain/src/index.js', 'packages/plain/extra.ts']})
        self.assertEqual((report['external'], report['local_package'], report['unresolved_count']), (0, 6, 0))
        self.state = core.apply_event(self.state, event, self.root)
        self.assertEqual(self.generate(include=['use.ts'])[0]['operations'], [])

    def test_w105_paths_without_baseurl_and_specificity(self):
        self.write('config/base.json', '{"compilerOptions":{"paths":{"@/*":["wide/*"],"@/special/*":["specific/*"],"@/special/exact":["exact.ts"],"suffix/*/end":["specific/*"]}}}')
        self.write('tsconfig.json', '{"extends":"./config/base.json"}')
        for path in ['config/wide/x.ts', 'config/specific/x.ts', 'config/exact.ts']:
            self.write(path, '')
        self.write('deep/use.ts', 'import "@/x.js"; import "@/special/x.js"; import "@/special/exact"; import "suffix/x.js/end";')
        event, report = self.generate()
        self.assertEqual(self.import_pairs(event), {('deep/use.ts', p) for p in ['config/wide/x.ts', 'config/specific/x.ts', 'config/exact.ts']})
        self.assertEqual((report['alias'], report['unresolved_count']), (4, 0))

    def test_w105_config_cycle_and_repository_boundary(self):
        self.write('tsconfig.json', '{"extends":"./cycle.json","compilerOptions":{"paths":{"ok":["ok.ts"]}}}')
        self.write('cycle.json', '{"extends":"./tsconfig.json"}')
        self.write('ok.ts', '')
        self.write('use.ts', 'import "ok";')
        event, report = self.generate()
        self.assertEqual(self.import_pairs(event), {('use.ts', 'ok.ts')})
        self.assertEqual(report['config_warnings'], [{'file': 'tsconfig.json', 'reason': 'extends cycle skipped'}])
        self.write('tsconfig.json', '{"extends":"../outside.json"}')
        event, report = self.generate()
        self.assertEqual(self.import_pairs(event), set())
        self.assertEqual(report['config_warnings'], [{'file': '../outside.json', 'reason': 'configuration outside regular repository files'}])

    def test_w105_many_aliases_are_linear(self):
        import graph
        count = 10000
        self.write('tsconfig.json', json.dumps({'compilerOptions': {'paths': {
            f'alias{n}': ['target.ts'] for n in range(count)}}}))
        self.write('target.ts', '')
        self.write('use.ts', '\n'.join(f'import "alias{n}";' for n in range(count)))
        start = time.perf_counter()
        event, report = graph.generate(self.root, self.state)
        elapsed = time.perf_counter() - start
        self.assertEqual(report['alias'], count)
        self.assertEqual(report['unresolved'], [])
        self.assertEqual(self.import_pairs(event), {('use.ts', 'target.ts')})
        self.assertLess(elapsed, 3.0)

    def test_w105_many_wildcard_aliases_are_linear(self):
        import graph
        count = 10000
        self.write('target.ts', '')
        for mode in ('prefix', 'suffix'):
            with self.subTest(mode=mode):
                patterns = {f'alias{n}/*' if mode == 'prefix' else f'alias/*/tail{n}': ['target.ts'] for n in range(count)}
                requests = [f'alias{n}/value' if mode == 'prefix' else f'alias/value/tail{n}' for n in range(count)]
                self.write('tsconfig.json', json.dumps({'compilerOptions': {'paths': patterns}}))
                self.write('use.ts', '\n'.join(f'import "{request}";' for request in requests))
                start = time.perf_counter()
                event, report = graph.generate(self.root, self.state)
                elapsed = time.perf_counter() - start
                self.assertEqual(report['alias'], count)
                self.assertEqual(report['unresolved'], [])
                self.assertEqual(self.import_pairs(event), {('use.ts', 'target.ts')})
                self.assertLess(elapsed, 3.0)

    def test_w105_filtered_real_js_never_selects_ts_fallback(self):
        self.write('real.js', '')
        self.write('real.ts', '')
        self.write('use.ts', 'import "./real.js";')
        event, report = self.generate(exclude=['real.js'])
        self.assertEqual(self.import_pairs(event), set())
        self.assertEqual([x['token'] for x in report['unresolved']], ['./real.js'])

    def test_w105_package_extends_reported_without_bare_imports(self):
        self.write('tsconfig.json', '{"extends":"@config/base"}')
        self.write('use.ts', 'export const value = 1;')
        _, report = self.generate()
        self.assertEqual(report['config_warnings'], [{'file': 'tsconfig.json', 'reason': 'package extends skipped', 'token': '@config/base'}])
