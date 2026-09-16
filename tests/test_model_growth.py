"""Model growth acceptance scenarios; no imports of other unittest modules."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest

from fixtures import build_spec, event, write_artifacts

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "proje-baslat" / "scripts"))
import core


DEFINITION_FIELDS = ("title", "object_ids", "depends_on", "decision_ids", "acceptance")


class ModelGrowthTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        write_artifacts(self.root)
        self.state = build_spec()

    def task(self, identifier):
        return next(task for task in self.state["tasks"] if task["id"] == identifier)

    def definition(self, identifier, **changes):
        return {**{key: copy.deepcopy(self.task(identifier)[key]) for key in DEFINITION_FIELDS}, **changes}

    def apply(self, action, **fields):
        original = copy.deepcopy(self.state)
        payload = event(action, **fields)
        original_payload = copy.deepcopy(payload)
        result = core.apply_event(self.state, payload, self.root)
        self.assertEqual(self.state, original, "event must not mutate state")
        self.assertEqual(payload, original_payload, "event must not mutate its payload")
        self.assertEqual(result["revision"], original["revision"] + 1)
        self.assertEqual(result["history"][:-1], original["history"])
        core.validate(result)
        self.state = result
        return result

    def reject(self, payload):
        original, original_payload = copy.deepcopy(self.state), copy.deepcopy(payload)
        with self.assertRaises(ValueError):
            core.apply_event(self.state, payload, self.root)
        self.assertEqual(self.state, original, "rejected event must be atomic")
        self.assertEqual(payload, original_payload)

    def proof(self, identifier):
        path = f"{identifier}.md"
        if not (self.root / path).exists():
            (self.root / path).write_text(f"Synthetic reviewer evidence for {identifier}.\n", encoding="utf-8")
        return [{"path": path, "criterion": index, "note": "Synthetic review declaration.",
                 "reviewer": "test-agent"} for index in range(len(self.task(identifier)["acceptance"]))]

    def finish(self, identifier):
        self.apply("start_task", task_id=identifier)
        self.apply("submit_evidence", task_id=identifier, items=self.proof(identifier))
        self.apply("complete_task", task_id=identifier)

    def extension(self):
        task = copy.deepcopy(self.task("T-DATA"))
        task.update(id="T-S4", title="Dördüncü kaynağı incele", object_ids=["S4"],
                    status="todo", evidence=[], acceptance=["S4 için inceleme kaydı yazıldı."])
        return {"objects": [{"id": "S4", "type": "Source", "label": "Dördüncü kaynak",
                             "properties": {"path": "S4.md"}}],
                "relations": [{"from": "O-REPORT", "type": "cites", "to": "S4"}],
                "tasks": [task]}

    def test_s4_registration_and_existing_claim_revision_complete_workflow(self):
        # C1 already has a completed prerequisite and recorded acceptance.
        self.state["tasks"][1]["id"] = "C1"
        self.state["tasks"][2]["depends_on"] = ["C1"]
        for identifier in ("T-DATA", "C1", "T-CHECK"):
            self.finish(identifier)
        old = copy.deepcopy(self.state)
        additions = self.extension()
        self.apply("extend_model", **additions)
        self.assertEqual(self.state["schema_version"], 2)
        for collection in ("objects", "relations", "tasks"):
            self.assertEqual(self.state[collection], old[collection] + additions[collection])
        self.assertEqual(self.state["history"][-1]["change"], additions)
        before_claim = copy.deepcopy(self.task("C1"))
        definition = self.definition("C1", title="C1 iddiasını S4 ile yeniden incele",
                                     object_ids=["O-REPORT", "S4"],
                                     depends_on=["T-DATA", "T-S4"],
                                     acceptance=["C1 incelemesi S4 kaydına bağlıdır.", "Güncel sonuç yazıldı."])
        self.apply("revise_task", task_id="C1", definition=definition)
        self.assertEqual({key: self.task("C1")[key] for key in DEFINITION_FIELDS}, definition)
        self.assertEqual(self.task("C1")["status"], "todo")
        self.assertEqual(self.task("C1")["evidence"], [])
        self.assertEqual(self.task("T-CHECK")["status"], "review")
        self.assertEqual(self.task("T-CHECK")["evidence"], [])
        self.assertEqual(self.state["history"][-1]["change"],
                         {"before": before_claim, "after": self.task("C1")})
        self.reject(event("start_task", task_id="C1"))
        self.finish("T-S4")
        self.finish("C1")
        self.reject(event("complete_task", task_id="T-CHECK"))
        self.finish("T-CHECK")
        self.assertTrue(all(task["effective_status"] == "done"
                            for task in core.inspect_state(self.state, self.root)["tasks"]))

    def test_growth_rejects_duplicate_dangling_cycles_and_forged_completion_atomically(self):
        valid = self.extension()
        invalid = []
        for collection, extra in (("objects", self.state["objects"][0]),
                                  ("relations", self.state["relations"][0]),
                                  ("tasks", self.state["tasks"][0])):
            bad = copy.deepcopy(valid)
            bad[collection].append(copy.deepcopy(extra))
            invalid.append(bad)
        for field in ("object_ids", "depends_on", "decision_ids"):
            bad = copy.deepcopy(valid)
            bad["tasks"][0][field] = ["missing"]
            invalid.append(bad)
        bad = copy.deepcopy(valid)
        bad["relations"][0]["to"] = "missing"
        invalid.append(bad)
        bad = copy.deepcopy(valid)
        second = copy.deepcopy(bad["tasks"][0])
        second.update(id="T-S5", depends_on=["T-S4"])
        bad["tasks"][0]["depends_on"] = ["T-S5"]
        bad["tasks"].append(second)
        invalid.append(bad)
        for status in ("doing", "review", "done", "cancelled"):
            bad = copy.deepcopy(valid)
            bad["tasks"][0]["status"] = status
            # Make even a forged done task structurally valid; rejection must
            # come from the growth contract, not missing done-task evidence.
            if status == "done":
                bad["tasks"][0]["evidence"] = [{"path": "tools.json", "criterion": 0,
                    "note": "Old acceptance", "reviewer": "test-agent", "sha256": "0" * 64}]
            invalid.append(bad)
        bad = copy.deepcopy(valid)
        bad["tasks"][0]["evidence"] = [{"path": "tools.json", "criterion": 0,
            "note": "Smuggled proof", "reviewer": "test-agent", "sha256": "0" * 64}]
        invalid.append(bad)
        invalid.extend([{"objects": [], "relations": [], "tasks": []},
                        {"objects": [], "relations": []}, {**valid, "decisions": []},
                        {**valid, "objects": {}}])
        for index, additions in enumerate(invalid):
            with self.subTest(case=index):
                self.reject(event("extend_model", **additions))
        self.reject(event("revise_task", task_id="T-DATA",
                          definition=self.definition("T-DATA", depends_on=["T-CHECK"])))
        self.apply("extend_model", **valid)
        self.assertEqual(self.state["revision"], 1)

    def test_revision_requires_explicit_definition_and_current_decision_topics(self):
        self.reject(event("revise_task", task_id="T-DATA", definition=self.definition("T-DATA")))
        for definition in (self.definition("T-DATA", status="done"), {"title": "Partial update"},
                           self.definition("T-DATA", object_ids=["missing"]),
                           self.definition("T-DATA", depends_on=None),
                           self.definition("T-DATA", decision_ids=[]),
                           self.definition("T-DATA", decision_ids=["D-DB"])):
            self.reject(event("revise_task", task_id="T-DATA", definition=definition))
        self.apply("accept_decision", decision_id="D-DB")
        self.reject(event("revise_task", task_id="T-DATA",
                          definition=self.definition("T-DATA", title="Still old decision")))
        additions = self.extension()
        self.reject(event("extend_model", **additions))
        additions["tasks"][0]["decision_ids"] = ["D-DB"]
        self.apply("extend_model", **additions)
        self.apply("revise_task", task_id="T-DATA",
                   definition=self.definition("T-DATA", decision_ids=["D-DB"]))
        self.assertEqual(self.task("T-DATA")["decision_ids"], ["D-DB"])
        self.task("T-DATA")["status"] = "cancelled"
        self.reject(event("revise_task", task_id="T-DATA",
                          definition=self.definition("T-DATA", title="Resurrect cancelled task")))

    def test_revision_invalidates_diamond_through_mixed_statuses_and_needs_new_proof(self):
        alternate = copy.deepcopy(self.task("T-COMPARE"))
        alternate.update(id="T-ALT", title="Alternative branch")
        self.state["tasks"].insert(2, alternate)
        self.task("T-CHECK")["depends_on"] = ["T-COMPARE", "T-ALT"]
        for identifier in ("T-DATA", "T-COMPARE", "T-ALT", "T-CHECK"):
            self.finish(identifier)
        old_proof = copy.deepcopy(self.task("T-DATA")["evidence"])
        self.task("T-COMPARE")["status"] = "doing"
        self.task("T-ALT")["status"] = "review"
        core.validate(self.state)
        self.apply("revise_task", task_id="T-DATA",
                   definition=self.definition("T-DATA", acceptance=["Completely revised requirement."]))
        self.assertEqual(self.task("T-DATA")["status"], "todo")
        self.assertEqual(self.task("T-DATA")["evidence"], [])
        self.assertEqual(self.state["history"][-1]["change"]["before"]["evidence"], old_proof)
        for identifier in ("T-COMPARE", "T-ALT", "T-CHECK"):
            self.assertEqual(self.task(identifier)["status"], "review")
            self.assertEqual(self.task(identifier)["evidence"], [])
        self.apply("start_task", task_id="T-DATA")
        self.reject(event("complete_task", task_id="T-DATA"))
        self.apply("submit_evidence", task_id="T-DATA", items=self.proof("T-DATA"))
        self.apply("complete_task", task_id="T-DATA")
        for identifier in ("T-COMPARE", "T-ALT", "T-CHECK"):
            self.reject(event("complete_task", task_id=identifier))
        self.finish("T-COMPARE")
        self.reject(event("start_task", task_id="T-CHECK"))
        self.finish("T-ALT")
        self.reject(event("complete_task", task_id="T-CHECK"))
        self.finish("T-CHECK")

    def repairs(self):
        before = copy.deepcopy(self.state)
        suggestions = core.inspect_state(self.state, self.root)["repair_actions"]
        self.assertEqual(self.state, before)
        for suggestion in suggestions:
            self.assertIn(suggestion["action"], {"reopen_task", "reconcile_task"})
            self.assertTrue(suggestion["reason"].strip())
            self.assertNotIn("actor", suggestion)
            # Each suggestion must work immediately against the inspected state,
            # rather than depend on another suggestion being applied first.
            repaired = core.apply_event(self.state, {**suggestion, "actor": "test-agent"}, self.root)
            self.assertEqual(repaired["revision"], self.state["revision"] + 1)
            core.validate(repaired)
            self.assertEqual(self.state, before)
        return suggestions

    def test_repair_suggestions_are_currently_executable_and_advance_recovery(self):
        self.finish("T-DATA")
        (self.root / "T-DATA.md").write_text("Changed source version.\n", encoding="utf-8")
        suggestions = self.repairs()
        self.assertIn(("reopen_task", "T-DATA"), [(s["action"], s["task_id"]) for s in suggestions])
        self.apply("accept_decision", decision_id="D-DB")
        suggestions = self.repairs()
        self.assertNotIn(("reconcile_task", "T-DATA"), [(s["action"], s["task_id"]) for s in suggestions])
        for identifier in ("T-COMPARE", "T-CHECK"):
            repair = next(s for s in suggestions if s["action"] == "reconcile_task" and s["task_id"] == identifier)
            self.assertEqual(repair["decision_ids"], ["D-DB"])
        reopen = next(s for s in suggestions if s["action"] == "reopen_task" and s["task_id"] == "T-DATA")
        self.state = core.apply_event(self.state, {**reopen, "actor": "test-agent"}, self.root)
        reconcile = next(s for s in self.repairs() if s["action"] == "reconcile_task" and s["task_id"] == "T-DATA")
        self.state = core.apply_event(self.state, {**reconcile, "actor": "test-agent"}, self.root)
        self.assertEqual(self.task("T-DATA")["status"], "todo")
        self.assertEqual(self.task("T-DATA")["decision_ids"], ["D-DB"])

    def test_repairs_are_empty_for_normal_work_blocked_tasks_and_cancelled_tasks(self):
        self.assertEqual(self.repairs(), [])
        self.apply("start_task", task_id="T-DATA")
        self.assertEqual(self.repairs(), [])
        self.apply("submit_evidence", task_id="T-DATA", items=self.proof("T-DATA"))
        self.assertEqual(self.repairs(), [])
        self.apply("complete_task", task_id="T-DATA")
        self.assertEqual(self.repairs(), [])
        for task in self.state["tasks"]:
            task["status"] = "cancelled"
        self.apply("accept_decision", decision_id="D-DB")
        self.assertEqual(self.repairs(), [])

    def test_legacy_schema_one_history_reads_without_rewriting_and_survives_upgrade(self):
        self.finish("T-DATA")
        self.assertEqual(self.state["schema_version"], 1)
        self.assertTrue(all(set(entry) == {"revision", "action", "actor", "reason"}
                            for entry in self.state["history"]))
        legacy = copy.deepcopy(self.state)
        core.validate(legacy)
        report = core.inspect_state(legacy, self.root)
        self.assertEqual(report["ready"], ["T-COMPARE"])
        self.assertIn("T-COMPARE", core.render_context(legacy, self.root))
        self.assertEqual(legacy, self.state)
        self.apply("extend_model", **self.extension())
        self.assertEqual(self.state["schema_version"], 2)
        self.assertEqual(self.state["history"][:-1], legacy["history"])
        # Version two also accepts legacy entries without a change snapshot.
        no_snapshot = copy.deepcopy(self.state)
        no_snapshot["history"][-1].pop("change")
        core.validate(no_snapshot)
        # A legacy reader must not silently accept a new action by removing
        # only its change payload and falsely labelling the model version one.
        no_snapshot["schema_version"] = 1
        with self.assertRaises(ValueError):
            core.validate(no_snapshot)


if __name__ == "__main__":
    unittest.main()
