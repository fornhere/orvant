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
from link_helpers import sembolik_bag_olustur

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

    def cli(self, expected=0, script=None, arguments=None):
        result = subprocess.run([sys.executable, str(script or SCRIPTS / "project.py"),
                                 *(arguments or ["upgrade", "preview", str(self.root)])],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stdout + result.stderr)
        return json.loads(result.stdout)

    def assert_backup(self, report):
        backup = Path(report["backup"])
        for name, content in self.original.items():
            self.assertEqual((backup / name).read_bytes(), content)

    def test_upgrade_preserves_data_and_original_pair_then_noop(self):
        before = self.tree()
        preview = self.cli()
        self.assertEqual(preview["result"], "upgrade_preview")
        self.assertEqual(self.tree(), before)
        self.assertEqual({item["path"] for item in preview["runtime_changes"]},
                         {"scripts/" + name for name in project.RUNTIME_FILES})
        self.assertTrue(all(item["old_sha256"] != item["new_sha256"]
                            for item in preview["runtime_changes"] if item["old_sha256"]))
        report = self.cli(arguments=["upgrade", "apply", str(self.root),
                                     "--preview-digest", preview["preview_digest"]])
        self.assertEqual(report["result"], "updated")
        self.assertFalse(report["state_changed"])
        self.assert_backup(report)
        for relative, content in before.items():
            if relative not in (".project/scripts/core.py", ".project/scripts/project.py"):
                self.assertEqual((self.root / relative).read_bytes(), content)
        for name in self.original:
            self.assertEqual((self.runtime / name).read_bytes(), (SCRIPTS / name).read_bytes())
        after = self.tree()
        noop = self.cli()
        self.assertEqual(noop["result"], "upgrade_preview")
        self.assertEqual(noop["runtime_changes"], [])
        self.assertEqual(self.tree(), after)
        copied = self.cli(expected=1, script=self.runtime / "project.py")
        self.assertIn("source skill package", copied["error"])
        self.assertEqual(self.tree(), after)

    def test_old_history_is_previewed_and_migrated_only_on_digest_apply(self):
        state = build_spec()
        state["revision"] = 1
        state["tasks"][0]["status"] = "doing"
        state["history"] = [{"revision": 1, "action": "task_started", "actor": "legacy-agent",
                             "reason": "Sentetik eski kayıt."}]
        self.state_path.write_text(json.dumps(state), encoding="utf-8")
        before = self.tree()
        preview = self.cli()
        self.assertEqual(preview["history_migrations"], [{"revision": 1, "old_action": "task_started",
                                                          "new_action": "start_task"}])
        self.assertEqual(preview["incompatible_history_actions"], [])
        self.assertEqual(self.tree(), before)
        applied = self.cli(arguments=["upgrade", "apply", str(self.root),
                                      "--preview-digest", preview["preview_digest"]])
        self.assertEqual(applied["result"], "updated")
        self.assertEqual(json.loads(self.state_path.read_text())["history"][0]["action"], "start_task")
        backup = Path(applied["backup"])
        self.assertEqual(json.loads((backup / "state.json").read_text())["history"][0]["action"],
                         "task_started")

    def test_unknown_history_action_blocks_upgrade_with_required_version(self):
        state = build_spec()
        state["revision"] = 1
        state["history"] = [{"revision": 1, "action": "future_action", "actor": "agent",
                             "reason": "Sentetik gelecek kayıt."}]
        self.state_path.write_text(json.dumps(state), encoding="utf-8")
        report = self.cli(expected=1)
        self.assertEqual(report["incompatible_history_actions"], ["future_action"])
        self.assertIn("required_runtime_version", report)
        self.assertIn("upgrade preview", report["error"])
        self.assertFalse((self.root / ".project" / "runtime-backups").exists())

    def test_init_status_check_diagnose_old_history_and_recommend_preview(self):
        state = build_spec()
        state["revision"] = 1
        state["tasks"][0]["status"] = "doing"
        state["history"] = [{"revision": 1, "action": "task_started", "actor": "legacy-agent",
                             "reason": "Sentetik eski kayıt."}]
        self.state_path.write_text(json.dumps(state), encoding="utf-8")
        spec = self.root / "spec.json"
        spec.write_text(json.dumps(build_spec()), encoding="utf-8")
        (self.root / ".project" / "integration.md").write_text("Legacy integration\n", encoding="utf-8")
        for arguments in (["init", str(self.root), "--spec", str(spec)],
                          ["status", str(self.root)], ["check", str(self.root)]):
            with self.subTest(command=arguments[0]):
                report = self.cli(expected=1, arguments=arguments)
                self.assertIn("History/runtime incompatibility", report["error"])
                self.assertIn("upgrade preview", report["error"])
                self.assertIn("Runtime version", report["error"])

    def test_apply_rejects_stale_preview_digest_without_writes(self):
        preview = self.cli()
        before = self.tree()
        report = self.cli(expected=1, arguments=["upgrade", "apply", str(self.root),
                                                 "--preview-digest", "0" * 64])
        self.assertEqual(report["result"], "error")
        self.assertEqual(self.tree(), before)

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
                sembolik_bag_olustur(target, outside)
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
            preview = json.loads(subprocess.run(
                [sys.executable, str(SCRIPTS / "project.py"), "upgrade", "preview", str(self.root)],
                capture_output=True, text=True, check=True).stdout)
            result = project.main(["upgrade", "apply", str(self.root), "--preview-digest",
                                   preview["preview_digest"]])
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
            preview = json.loads(subprocess.run(
                [sys.executable, str(SCRIPTS / "project.py"), "upgrade", "preview", str(self.root)],
                capture_output=True, text=True, check=True).stdout)
            result = project.main(["upgrade", "apply", str(self.root), "--preview-digest",
                                   preview["preview_digest"]])
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
