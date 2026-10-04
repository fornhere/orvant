"""Portable I/O and real process locking, run on each OS in CI."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/orvant/scripts'
sys.path.insert(0, str(SCRIPTS))
import core
import project
from fixtures import build_spec


class PlatformTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / 'Türkçe proje boşluk'

    def test_utf8_bom_input_and_unicode_cli_with_legacy_pipe_encoding(self):
        spec = self.base / 'girdi.json'
        spec.write_text(json.dumps(build_spec(), ensure_ascii=False), encoding='utf-8-sig')
        env = dict(os.environ, PYTHONIOENCODING='ascii', PYTHONUTF8='0')
        for args in ([str(SCRIPTS / 'project.py'), 'init', str(self.root), '--spec', str(spec)],
                     [str(self.root / '.project/scripts/project.py'), 'context', str(self.root), '--json']):
            result = subprocess.run([sys.executable, *args], env=env, capture_output=True,
                                    encoding='utf-8', timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            json.loads(result.stdout)
        self.assertEqual(project.read_json(self.root / '.project/state.json')['revision'], 0)

    def test_competing_writer_waits_then_acquires(self):
        code = """
import sys
sys.path.insert(0, sys.argv[1])
import project
print('ready', flush=True)
with project.writer_lock(sys.argv[2]):
    print('acquired', flush=True)
"""
        child = None
        try:
            with project.writer_lock(str(self.root)):
                child = subprocess.Popen([sys.executable, '-c', code, str(SCRIPTS), str(self.root)],
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8')
                self.assertEqual(child.stdout.readline().strip(), 'ready')
                time.sleep(0.2)
                self.assertIsNone(child.poll(), 'second writer entered while lock was held')
            out, err = child.communicate(timeout=10)
            self.assertEqual(child.returncode, 0, err)
            self.assertEqual(out.strip(), 'acquired')
        finally:
            if child is not None:
                if child.poll() is None:
                    child.kill()
                child.communicate()

    def test_lock_released_after_exception(self):
        with self.assertRaises(RuntimeError):
            with project.writer_lock(str(self.root)):
                raise RuntimeError('interrupted operation')
        with project.writer_lock(str(self.root)):
            pass

    def test_nonportable_evidence_paths_rejected(self):
        for value in ['.PROJECT/state.json', 'report.txt:stream', 'NUL.txt', 'COM1',
                      'folder./result.txt', 'folder /result.txt', 'LPT¹.txt']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                core._relative_path(value)
        self.assertEqual(str(core._relative_path('Türkçe klasör/çıktı.txt')),
                         'Türkçe klasör/çıktı.txt')

    @unittest.skipUnless(os.name == 'nt', 'Windows junction behavior')
    def test_windows_junction_rejected(self):
        target = self.base / 'target'
        target.mkdir()
        link = self.base / 'junction'
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)],
                                capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        try:
            with self.assertRaises(ValueError):
                project.no_symlink(link)
            (target / 'proof.txt').write_text('proof', encoding='utf-8')
            with self.assertRaises(ValueError):
                core._hash_file(self.base, 'junction/proof.txt')
        finally:
            link.rmdir()
