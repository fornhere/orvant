"""Behavioral acceptance tests derived from TEKNIK-TASARIM.md, stdlib only."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from fixtures import build_spec, event, evidence_items, write_artifacts

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "skills" / "proje-baslat" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import core


class ProjectCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        self.state = build_spec()
        write_artifacts(self.root)

    def apply(self, action, **fields):
        original = copy.deepcopy(self.state)
        new = core.apply_event(self.state, event(action, **fields), self.root)
        self.assertEqual(self.state, original, "apply_event mutated its input")
        self.assertEqual(new["revision"], original["revision"] + 1)
        core.validate(new)
        self.state = new
        return new

    def finish(self, task_id):
        self.apply("start_task", task_id=task_id)
        self.apply("submit_evidence", task_id=task_id, items=evidence_items(task_id))
        self.apply("complete_task", task_id=task_id)

    def inspection(self):
        return core.inspect_state(self.state, self.root)

    def task_view(self, task_id):
        return next(t for t in self.inspection()["tasks"] if t["id"] == task_id)

    def rejected(self, payload):
        before = copy.deepcopy(self.state)
        with self.assertRaises(ValueError):
            core.apply_event(self.state, payload, self.root)
        self.assertEqual(self.state, before)


class ValidationTests(ProjectCase):
    def test_valid_initial_model_and_ready_task(self):
        core.validate(self.state)
        self.assertEqual(self.inspection()["ready"], ["T-DATA"])
        self.assertEqual(self.state["project"]["open_questions"], ["Görsel tasarım henüz belirlenmedi."])

    def test_invalid_references(self):
        for collection, field, value in [
            ("tasks", "object_ids", ["missing"]),
            ("tasks", "depends_on", ["missing"]),
            ("tasks", "decision_ids", ["missing"]),
            ("relations", "to", "missing"),
        ]:
            with self.subTest(collection=collection, field=field):
                bad = copy.deepcopy(self.state)
                bad[collection][0][field] = value
                with self.assertRaises(ValueError):
                    core.validate(bad)

    def test_dependency_cycles(self):
        for dependency in ["T-DATA", "T-CHECK"]:
            with self.subTest(dependency=dependency):
                bad = copy.deepcopy(self.state)
                bad["tasks"][0]["depends_on"] = [dependency]
                with self.assertRaises(ValueError):
                    core.validate(bad)

    def test_invalid_schema_and_field_types(self):
        mutations = [
            lambda s: s.update(schema_version=99),
            lambda s: s.update(revision=-1),
            lambda s: s.update(revision=True),
            lambda s: s["tasks"][0].update(status="magically_done"),
            lambda s: s["tasks"][0].update(depends_on="T-CHECK"),
            lambda s: s["objects"][0].update(properties=[]),
            lambda s: s["project"].pop("goal"),
            lambda s: s["objects"].append(copy.deepcopy(s["objects"][0])),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                bad = copy.deepcopy(self.state)
                mutate(bad)
                with self.assertRaises(ValueError):
                    core.validate(bad)

    def test_unknown_action_rejected_without_mutation(self):
        self.rejected(event("invented_action", task_id="T-DATA"))


class WorkflowTests(ProjectCase):
    def test_dependency_gate_and_reopen(self):
        self.rejected(event("start_task", task_id="T-COMPARE"))
        self.finish("T-DATA")
        self.assertIn("T-COMPARE", self.inspection()["ready"])
        self.apply("reopen_task", task_id="T-DATA")
        self.assertEqual(self.state["tasks"][0]["evidence"], [])
        self.rejected(event("start_task", task_id="T-COMPARE"))

    def test_per_criterion_evidence_required(self):
        self.apply("start_task", task_id="T-DATA")
        self.apply("submit_evidence", task_id="T-DATA", items=evidence_items("T-DATA")[:1])
        self.rejected(event("complete_task", task_id="T-DATA"))
        self.apply("submit_evidence", task_id="T-DATA", items=evidence_items("T-DATA"))
        self.apply("complete_task", task_id="T-DATA")
        self.assertEqual(self.task_view("T-DATA")["effective_status"], "done")

    def test_invalid_criterion_rejected(self):
        self.apply("start_task", task_id="T-DATA")
        for criterion in [-1, 2, True, "0"]:
            with self.subTest(criterion=criterion):
                item = evidence_items("T-DATA")[0]
                item["criterion"] = criterion
                self.rejected(event("submit_evidence", task_id="T-DATA", items=[item]))

    def test_hash_computed_and_input_not_trusted(self):
        self.apply("start_task", task_id="T-DATA")
        self.apply("submit_evidence", task_id="T-DATA", items=evidence_items("T-DATA"))
        expected = hashlib.sha256((self.root / "tools.json").read_bytes()).hexdigest()
        self.assertEqual(self.state["tasks"][0]["evidence"][0]["sha256"], expected)

    def test_changed_proof_prevents_completion(self):
        self.apply("start_task", task_id="T-DATA")
        self.apply("submit_evidence", task_id="T-DATA", items=evidence_items("T-DATA"))
        (self.root / "tools.json").write_text("changed", encoding="utf-8")
        self.rejected(event("complete_task", task_id="T-DATA"))

    def test_stale_proof_propagates_to_transitive_dependents(self):
        for identifier in ["T-DATA", "T-COMPARE", "T-CHECK"]:
            self.finish(identifier)
        before = copy.deepcopy(self.state)
        (self.root / "tools.json").write_text("changed", encoding="utf-8")
        self.assertTrue(self.inspection()["warnings"])
        for identifier in ["T-DATA", "T-COMPARE", "T-CHECK"]:
            self.assertEqual(self.task_view(identifier)["effective_status"], "needs_review")
        self.assertEqual(self.state, before, "inspection must not rewrite persisted history")

    def test_redoing_upstream_does_not_restore_old_downstream_acceptance(self):
        for identifier in ["T-DATA", "T-COMPARE", "T-CHECK"]:
            self.finish(identifier)
        self.apply("reopen_task", task_id="T-DATA")
        for identifier in ["T-COMPARE", "T-CHECK"]:
            task = next(t for t in self.state["tasks"] if t["id"] == identifier)
            self.assertEqual(task["status"], "review")
            self.assertEqual(task["evidence"], [])
        self.finish("T-DATA")
        for identifier in ["T-COMPARE", "T-CHECK"]:
            self.assertNotEqual(self.task_view(identifier)["effective_status"], "done")
            self.rejected(event("complete_task", task_id=identifier))
        self.apply("submit_evidence", task_id="T-COMPARE", items=evidence_items("T-COMPARE"))
        self.apply("complete_task", task_id="T-COMPARE")
        self.assertNotEqual(self.task_view("T-CHECK")["effective_status"], "done")
        self.rejected(event("complete_task", task_id="T-CHECK"))

    def test_missing_proof_and_cancelled_dependency(self):
        self.finish("T-DATA")
        (self.root / "tools.json").unlink()
        self.assertEqual(self.task_view("T-DATA")["effective_status"], "needs_review")
        self.assertNotIn("T-COMPARE", self.inspection()["ready"])
        self.state = build_spec()
        self.state["tasks"][0]["status"] = "cancelled"
        self.rejected(event("start_task", task_id="T-COMPARE"))

    def test_diamond_invalidation_crosses_in_progress_intermediate_tasks(self):
        for action in ["reopen_task", "reconcile_task"]:
            for middle_status in ["doing", "review"]:
                with self.subTest(action=action, middle_status=middle_status):
                    self.state = build_spec()
                    alternate = copy.deepcopy(self.state["tasks"][1])
                    alternate.update(id="T-ALT", title="İkinci karşılaştırma dalı")
                    self.state["tasks"].insert(2, alternate)
                    self.state["tasks"][-1]["depends_on"] = ["T-COMPARE", "T-ALT"]
                    for identifier in ["T-DATA", "T-COMPARE", "T-ALT", "T-CHECK"]:
                        items = evidence_items("T-COMPARE" if identifier == "T-ALT" else identifier)
                        self.apply("start_task", task_id=identifier)
                        self.apply("submit_evidence", task_id=identifier, items=items)
                        self.apply("complete_task", task_id=identifier)
                    # A loaded, schema-valid snapshot may contain work in progress
                    # and historical downstream evidence. The action must invalidate
                    # through either intermediate status, including the shared leaf.
                    for task in self.state["tasks"]:
                        if task["id"] in {"T-COMPARE", "T-ALT"}:
                            task["status"] = middle_status
                    if action == "reconcile_task":
                        self.state["tasks"][0]["status"] = "review"
                    core.validate(self.state)
                    fields = {"task_id": "T-DATA"}
                    if action == "reconcile_task":
                        fields["decision_ids"] = ["D-LOCAL"]
                    self.apply(action, **fields)
                    for task in self.state["tasks"][1:]:
                        self.assertEqual(task["status"], "review")
                        self.assertEqual(task["evidence"], [])
                    self.finish("T-DATA")
                    for identifier in ["T-COMPARE", "T-ALT", "T-CHECK"]:
                        self.assertNotEqual(self.task_view(identifier)["effective_status"], "done")
                        self.rejected(event("complete_task", task_id=identifier))

    def test_proposed_decision_does_not_replace_current(self):
        self.finish("T-DATA")
        decisions = {d["id"]: d for d in self.state["decisions"]}
        self.assertEqual(decisions["D-LOCAL"]["status"], "accepted")
        self.assertEqual(decisions["D-DB"]["status"], "proposed")
        self.assertEqual(self.task_view("T-DATA")["effective_status"], "done")

    def test_accept_supersede_and_reconcile(self):
        self.finish("T-DATA")
        self.apply("accept_decision", decision_id="D-DB")
        decisions = {d["id"]: d for d in self.state["decisions"]}
        self.assertEqual(decisions["D-LOCAL"]["status"], "superseded")
        self.assertEqual(decisions["D-DB"]["status"], "accepted")
        self.assertEqual(decisions["D-DB"]["accepted_by"], "test-agent")
        self.assertEqual(self.task_view("T-DATA")["effective_status"], "needs_review")
        self.apply("reopen_task", task_id="T-DATA")
        self.apply("reconcile_task", task_id="T-DATA", decision_ids=["D-DB"])
        self.assertEqual(self.state["tasks"][0]["status"], "todo")
        self.assertEqual(self.state["tasks"][0]["decision_ids"], ["D-DB"])
        self.assertEqual(self.state["tasks"][0]["evidence"], [])

    def test_decision_acceptance_requires_source_and_correct_predecessor(self):
        for field, value in [("source", ""), ("supersedes", None)]:
            with self.subTest(field=field):
                self.state = build_spec()
                self.state["decisions"][1][field] = value
                self.rejected(event("accept_decision", decision_id="D-DB"))

    def test_reconcile_cannot_bind_proposed_decision(self):
        self.rejected(event("reconcile_task", task_id="T-DATA", decision_ids=["D-DB"]))

    def test_propose_and_reject_preserve_accepted_decision(self):
        self.apply("propose_decision", decision={"id": "D-EXTRA", "topic": "format",
                   "statement": "CSV değerlendirilebilir.", "rationale": "Alternatif.",
                   "source": "Sentetik öneri.", "supersedes": None})
        self.apply("reject_decision", decision_id="D-EXTRA")
        decisions = {d["id"]: d for d in self.state["decisions"]}
        self.assertEqual(decisions["D-EXTRA"]["status"], "rejected")
        self.assertEqual(decisions["D-LOCAL"]["status"], "accepted")

    def test_proof_paths_reject_outside_managed_missing_and_directory(self):
        self.apply("start_task", task_id="T-DATA")
        outside = self.root.parent / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        (self.root / "escape.txt").symlink_to(outside)
        (self.root / ".project").mkdir()
        (self.root / ".project" / "state.json").write_text("{}", encoding="utf-8")
        for path in [str(outside), "../outside.txt", "escape.txt", ".project/state.json", "missing.txt", "."]:
            with self.subTest(path=path):
                item = evidence_items("T-DATA")[0]
                item["path"] = path
                self.rejected(event("submit_evidence", task_id="T-DATA", items=[item]))


class CLITests(ProjectCase):
    def setUp(self):
        super().setUp()
        self.spec = self.root.parent / "spec.json"
        self.spec.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")

    def cli(self, *args, success=True):
        result = subprocess.run([sys.executable, str(SCRIPTS / "project.py"), *map(str, args)],
                                capture_output=True, text=True)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn("Traceback", result.stdout + result.stderr)
        return result

    def init(self):
        return self.cli("init", self.root, "--spec", self.spec)

    def stored(self):
        return json.loads((self.root / ".project" / "state.json").read_text(encoding="utf-8"))

    def cli_event(self, payload, expected=0, success=True):
        path = self.root.parent / "event.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return self.cli("apply", self.root, "--event", path, "--expected-revision", expected, success=success)

    def tree_bytes(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

    def test_clean_init_check_and_context_json(self):
        self.init()
        self.cli("check", self.root)
        view = json.loads(self.cli("context", self.root, "--json").stdout)
        self.assertEqual(view["ready"], ["T-DATA"])
        self.assertEqual(self.stored(), self.state)
        self.assertTrue((self.root / "AGENTS.md").is_file())
        self.assertTrue((self.root / ".project" / "CONTEXT.md").is_file())

    def test_existing_files_preserved_and_repeat_init_noop(self):
        (self.root / "AGENTS.md").write_text("# Kullanıcı kuralları\nKorunacak.\n", encoding="utf-8")
        (self.root / "README.md").write_text("Mevcut proje.\n", encoding="utf-8")
        before = self.tree_bytes()
        self.init()
        for path, data in before.items():
            self.assertEqual((self.root / path).read_bytes(), data)
        self.cli_event(event("start_task", task_id="T-DATA"))
        installed = self.tree_bytes()
        self.init()
        self.assertEqual(self.tree_bytes(), installed)
        self.assertEqual(self.stored()["revision"], 1)

    def test_partial_install_and_symlink_conflict_preserve_files(self):
        target = self.root / ".project"
        target.mkdir()
        (target / "state.json").write_text("partial", encoding="utf-8")
        before = self.tree_bytes()
        self.cli("init", self.root, "--spec", self.spec, success=False)
        self.assertEqual(self.tree_bytes(), before)
        (target / "state.json").unlink()
        target.rmdir()
        outside = self.root.parent / "outside"
        outside.mkdir()
        target.symlink_to(outside, target_is_directory=True)
        self.cli("init", self.root, "--spec", self.spec, success=False)
        self.assertEqual(list(outside.iterdir()), [])

    def test_failed_event_and_stale_revision_preserve_state_bytes(self):
        self.init()
        before = (self.root / ".project" / "state.json").read_bytes()
        self.cli_event(event("start_task", task_id="T-COMPARE"), success=False)
        self.assertEqual((self.root / ".project" / "state.json").read_bytes(), before)
        self.cli_event(event("start_task", task_id="T-DATA"))
        after = (self.root / ".project" / "state.json").read_bytes()
        self.cli_event(event("submit_evidence", task_id="T-DATA", items=evidence_items("T-DATA")), expected=0, success=False)
        self.assertEqual((self.root / ".project" / "state.json").read_bytes(), after)

    def test_malformed_state_check_context_and_apply_fail_without_rewrite(self):
        self.init()
        state_path = self.root / ".project" / "state.json"
        for invalid in ['{"partial":', json.dumps({**build_spec(), "schema_version": 404})]:
            with self.subTest(invalid=invalid):
                state_path.write_text(invalid, encoding="utf-8")
                before = state_path.read_bytes()
                self.cli("check", self.root, success=False)
                self.cli("context", self.root, success=False)
                self.cli_event(event("start_task", task_id="T-DATA"), success=False)
                self.assertEqual(state_path.read_bytes(), before)

    def test_stale_evidence_check_fails_but_context_works(self):
        self.init()
        for revision, payload in enumerate([
            event("start_task", task_id="T-DATA"),
            event("submit_evidence", task_id="T-DATA", items=evidence_items("T-DATA")),
            event("complete_task", task_id="T-DATA"),
        ]):
            self.cli_event(payload, expected=revision)
        self.cli("check", self.root)
        (self.root / "tools.json").write_text("changed", encoding="utf-8")
        self.cli("check", self.root, success=False)
        view = json.loads(self.cli("context", self.root, "--json").stdout)
        self.assertTrue(view["warnings"])
        self.assertNotIn("T-COMPARE", view["ready"])

    def test_missing_derived_view_live_context_uses_canonical_state(self):
        self.init()
        before = (self.root / ".project" / "state.json").read_bytes()
        (self.root / ".project" / "CONTEXT.md").unlink()
        view = json.loads(self.cli("context", self.root, "--json").stdout)
        self.assertEqual(view["ready"], ["T-DATA"])
        self.assertEqual((self.root / ".project" / "state.json").read_bytes(), before)
        self.cli("init", self.root, "--spec", self.spec, success=False)
        self.assertEqual((self.root / ".project" / "state.json").read_bytes(), before)

    def test_report_and_tested_output_both_tracked_when_only_output_changes(self):
        self.init()
        original_output = (self.root / "tools.json").read_bytes()
        original_report = (self.root / "review.md").read_bytes()
        items = evidence_items("T-DATA")
        items += [{**item, "path": "review.md"} for item in evidence_items("T-DATA")]
        self.cli_event(event("start_task", task_id="T-DATA"))
        self.cli_event(event("submit_evidence", task_id="T-DATA", items=items), expected=1)
        stored_paths = {item["path"] for item in self.stored()["tasks"][0]["evidence"]}
        self.assertEqual(stored_paths, {"tools.json", "review.md"})
        (self.root / "tools.json").write_bytes(original_output + b"\nchanged after report\n")
        self.assertEqual((self.root / "review.md").read_bytes(), original_report)
        self.cli_event(event("complete_task", task_id="T-DATA"), expected=2, success=False)
        self.cli("check", self.root, success=False)
        self.assertEqual(self.stored()["revision"], 2)
        # Complete the original exact output, then change it again while the
        # report stays byte-identical: a done task must also become stale.
        (self.root / "tools.json").write_bytes(original_output)
        self.cli_event(event("complete_task", task_id="T-DATA"), expected=2)
        self.cli("check", self.root)
        (self.root / "tools.json").write_bytes(original_output + b"\nchanged after completion\n")
        self.assertEqual((self.root / "review.md").read_bytes(), original_report)
        self.cli("check", self.root, success=False)
        view = json.loads(self.cli("context", self.root, "--json").stdout)
        data_task = next(task for task in view["tasks"] if task["id"] == "T-DATA")
        self.assertEqual(data_task["effective_status"], "needs_review")


if __name__ == "__main__":
    unittest.main()
