"""Runtime upgrade preserves project data and makes interrupted writes visible."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from fixtures import build_spec, write_artifacts

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "skills" / "orvant" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import project


class RuntimeUpgradeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "project"
        self.runtime = self.root / ".project" / "scripts"
        self.runtime.mkdir(parents=True)
        self.state_path = self.root / ".project" / "state.json"
        self.state_path.write_text(json.dumps(build_spec(), ensure_ascii=False) + "\n\n", encoding="utf-8")
        self.original = {
            "core.py": b"raise RuntimeError('Old runtime must not execute')\n",
            "project.py": b"# old copied CLI\n",
        }
        for name, content in self.original.items():
            (self.runtime / name).write_bytes(content)
        (self.root / "AGENTS.md").write_text("User instructions\n", encoding="utf-8")
        (self.root / ".project" / "CONTEXT.md").write_text("Existing context\n", encoding="utf-8")
        write_artifacts(self.root)

    def tree(self):
        return {path.relative_to(self.root).as_posix(): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file()}

    def cli(self, expected=0, script=None):
        result = subprocess.run([sys.executable, str(script or SCRIPTS / "project.py"),
                                 "upgrade", str(self.root)], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stdout + result.stderr)
        return json.loads(result.stdout)

    def assert_backup(self, report):
        backup = Path(report["backup"])
        for name, content in self.original.items():
            self.assertEqual((backup / name).read_bytes(), content)

    def test_upgrade_preserves_data_and_original_pair_then_noop(self):
        before = self.tree()
        report = self.cli()
        self.assertEqual(report["result"], "updated")
        self.assertFalse(report["state_changed"])
        self.assert_backup(report)
        for relative, content in before.items():
            if relative not in (".project/scripts/core.py", ".project/scripts/project.py"):
                self.assertEqual((self.root / relative).read_bytes(), content)
        for name in self.original:
            self.assertEqual((self.runtime / name).read_bytes(), (SCRIPTS / name).read_bytes())
        after = self.tree()
        self.assertEqual(self.cli()["result"], "noop")
        self.assertEqual(self.tree(), after)
        self.assertEqual(self.cli(script=self.runtime / "project.py")["result"], "noop")
        self.assertEqual(self.tree(), after)

    def test_malformed_and_future_schema_leave_every_file_untouched(self):
        for content in ["{broken", json.dumps({**build_spec(), "schema_version": 999})]:
            with self.subTest(content=content):
                self.state_path.write_text(content, encoding="utf-8")
                before = self.tree()
                self.assertEqual(self.cli(expected=1)["result"], "error")
                self.assertEqual(self.tree(), before)
                self.assertFalse((self.root / ".project" / "runtime-backups").exists())

    def test_managed_symlink_rejected_without_writes(self):
        outside = self.root.parent / "outside.py"
        outside.write_bytes(b"outside must remain unchanged\n")
        for target in [self.runtime / "core.py", self.root / ".project" / "extra"]:
            with self.subTest(target=target):
                previous = target.read_bytes() if target.exists() else None
                if target.exists():
                    target.unlink()
                target.symlink_to(outside)
                before = self.tree()
                self.cli(expected=1)
                self.assertEqual(self.tree(), before)
                self.assertTrue(target.is_symlink())
                self.assertFalse((self.root / ".project" / "runtime-backups").exists())
                target.unlink()
                if previous is not None:
                    target.write_bytes(previous)

    def test_second_replacement_failure_restores_pair_and_retains_backup(self):
        original_replace = project.os.replace

        def fail_second(source, destination):
            if Path(source).name == "project.py":
                raise OSError("injected install failure")
            return original_replace(source, destination)

        before = self.state_path.read_bytes()
        output = io.StringIO()
        with mock.patch.object(project.os, "replace", side_effect=fail_second), contextlib.redirect_stdout(output):
            result = project.main(["upgrade", str(self.root)])
        report = json.loads(output.getvalue())
        self.assertEqual(result, 1)
        self.assertEqual(report["result"], "restored")
        self.assert_backup(report)
        for name, content in self.original.items():
            self.assertEqual((self.runtime / name).read_bytes(), content)
        self.assertEqual(self.state_path.read_bytes(), before)

    def test_failed_rollback_reports_partial_runtime_and_manual_recovery(self):
        original_replace = project.os.replace

        def fail_install_and_restore(source, destination):
            if Path(source).name in ("project.py", "core.py.restore"):
                raise OSError("injected replacement failure")
            return original_replace(source, destination)

        before = self.state_path.read_bytes()
        output = io.StringIO()
        with mock.patch.object(project.os, "replace", side_effect=fail_install_and_restore), contextlib.redirect_stdout(output):
            result = project.main(["upgrade", str(self.root)])
        report = json.loads(output.getvalue())
        self.assertEqual(result, 1)
        self.assertEqual(report["result"], "restore_failed")
        self.assertTrue(report["restore_errors"])
        self.assertIn("BOTH", report["recovery"])
        self.assert_backup(report)
        self.assertEqual((self.runtime / "core.py").read_bytes(), (SCRIPTS / "core.py").read_bytes())
        self.assertEqual((self.runtime / "project.py").read_bytes(), self.original["project.py"])
        self.assertEqual(self.state_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
