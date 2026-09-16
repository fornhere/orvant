"""Domain-behavior acceptance tests, including state edits outside the action API."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest

from ontology_fixtures import build_notebook, evidence, event, link, prop, relation_type, write_notebook_files
from fixtures import build_spec, evidence_items, write_artifacts

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "skills" / "proje-baslat" / "scripts"))
import core
import ontology


class NotebookCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = build_notebook()
        write_notebook_files(self.root)

    def obj(self, identifier, state=None):
        return next(item for item in (state or self.state)["objects"] if item["id"] == identifier)

    def task(self, identifier, state=None):
        return next(item for item in (state or self.state)["tasks"] if item["id"] == identifier)

    def view(self, identifier, state=None):
        report = core.inspect_state(state or self.state, self.root)
        return next(item for item in report["tasks"] if item["id"] == identifier)

    def apply(self, action, **fields):
        before = copy.deepcopy(self.state)
        self.state = core.apply_event(self.state, event(action, **fields), self.root)
        self.assertEqual(self.state["revision"], before["revision"] + 1)
        core.validate(self.state)

    def finish(self, identifier):
        self.apply("start_task", task_id=identifier)
        self.apply("submit_evidence", task_id=identifier, items=evidence(identifier))
        self.apply("complete_task", task_id=identifier)

    def finish_all(self):
        for identifier in ("run-a", "evaluate-a", "run-b", "evaluate-b", "run-c", "evaluate-c", "compare-ab"):
            self.finish(identifier)

    def replace(self, identifier, **properties):
        obj = copy.deepcopy(self.obj(identifier))
        obj["properties"].update(properties)
        return {"op": "replace_object", "object": obj}

    def reject(self, payload):
        before = copy.deepcopy(self.state)
        with self.assertRaises(ValueError):
            core.apply_event(self.state, payload, self.root)
        self.assertEqual(self.state, before)

    def fp(self, inputs, state=None):
        return ontology.fingerprint(state or self.state, inputs, self.root, core._hash_file)


class TypedGraphTests(NotebookCase):
    def test_typed_properties_and_enum_validate_values_not_just_presence(self):
        core.validate(self.state)
        invalid = [
            ("experiment-a", "repeats", True), ("experiment-a", "repeats", 1.5),
            ("experiment-a", "prompt", 3), ("experiment-a", "synthetic", 1),
            ("experiment-a", "tags", ["text", 1]), ("experiment-a", "configuration", float("nan")),
            ("evaluation-a", "score", True), ("evaluation-a", "score", float("inf")),
            ("evaluation-a", "verdict", "maybe"),
        ]
        for identifier, name, value in invalid:
            with self.subTest(identifier=identifier, property=name, value=value):
                bad = copy.deepcopy(self.state)
                self.obj(identifier, bad)["properties"][name] = value
                with self.assertRaises(ValueError):
                    core.validate(bad)
        bad = copy.deepcopy(self.state)
        del self.obj("experiment-a", bad)["properties"]["prompt"]
        with self.assertRaisesRegex(ValueError, "required"):
            core.validate(bad)
        bad = copy.deepcopy(self.state)
        self.obj("experiment-a", bad)["properties"]["typo"] = "untyped"
        with self.assertRaises(ValueError):
            core.validate(bad)

    def test_schema_definitions_are_checked_even_without_instances(self):
        for definition in (prop("number", enum=[True]), prop("unknown"),
                           {"type": "string", "required": "yes", "enum": None}):
            bad = copy.deepcopy(self.state)
            bad["ontology"]["object_types"].append({"id": "Unused", "label": "Unused",
                                                        "properties": {"value": definition}})
            with self.subTest(definition=definition), self.assertRaises(ValueError):
                core.validate(bad)

    def test_file_property_paths_reject_escape_and_managed_state(self):
        for path in ("/tmp/result.txt", "../result.txt", ".project/state.json", "nested/../result",
                     "C:\\result.txt", "C:result.txt", "result\x00.txt", "."):
            with self.subTest(path=path):
                bad = copy.deepcopy(self.state)
                self.obj("artifact-a", bad)["properties"]["path"] = path
                with self.assertRaises(ValueError):
                    core.validate(bad)
        safe = copy.deepcopy(self.state)
        self.obj("artifact-a", safe)["properties"]["path"] = "Türkçe klasör/çıktı.txt"
        core.validate(safe)

    def test_file_fingerprint_rejects_missing_file_and_symlink(self):
        (self.root / "çıktı-a.txt").unlink()
        with self.assertRaises(ValueError):
            self.fp(["artifact-a"])
        (self.root / "çıktı-a.txt").symlink_to(self.root / "çıktı-b.txt")
        with self.assertRaises(ValueError):
            self.fp(["artifact-a"])

    def test_relation_endpoints_ids_and_duplicates_are_validated(self):
        bad = copy.deepcopy(self.state)
        bad["relations"][0]["to"] = "criterion-shared"
        with self.assertRaisesRegex(ValueError, "expected Artifact"):
            core.validate(bad)
        for duplicate_id in (True, False):
            bad = copy.deepcopy(self.state)
            copied = copy.deepcopy(bad["relations"][-1])
            if not duplicate_id:
                copied["id"] = "same-triple-new-id"
            bad["relations"].append(copied)
            with self.subTest(duplicate_id=duplicate_id), self.assertRaises(ValueError):
                core.validate(bad)

    def test_minimum_counts_include_unlinked_instances_on_both_sides(self):
        for identifier, kind, properties in (("unused-evaluation", "Evaluation", {"score": 0, "verdict": "fail"}),
                                              ("unused-artifact", "Artifact", {"path": "unused.txt"})):
            bad = copy.deepcopy(self.state)
            bad["objects"].append({"id": identifier, "type": kind, "label": identifier,
                                   "properties": properties})
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "cardinality"):
                core.validate(bad)

    def test_relation_replace_is_atomic_and_maximum_blocks_duplicates(self):
        remove = {"op": "remove_relation", "relation_id": "uses-a"}
        add = {"op": "add_relation", "relation": link("uses-a-replaced", "evaluation-a", "uses", "criterion-independent")}
        self.reject(event("mutate_graph", operations=[remove]))
        self.reject(event("mutate_graph", operations=[add]))
        self.apply("mutate_graph", operations=[remove, add])
        self.assertNotIn("uses-a", {r["id"] for r in self.state["relations"]})
        self.assertIn("uses-a-replaced", {r["id"] for r in self.state["relations"]})

    def test_replace_schema_checks_final_values_and_link_limits(self):
        bad = copy.deepcopy(self.state["ontology"])
        bad["object_types"][0]["properties"]["required-new-field"] = prop("string")
        self.reject(event("mutate_graph", operations=[{"op": "replace_ontology", "ontology": bad}]))
        bad = copy.deepcopy(self.state["ontology"])
        next(r for r in bad["relation_types"] if r["id"] == "uses")["to_max"] = 1
        self.reject(event("mutate_graph", operations=[{"op": "replace_ontology", "ontology": bad}]))


class ImpactTests(NotebookCase):
    def test_forward_and_reverse_paths_follow_declared_meaning(self):
        changed = copy.deepcopy(self.state)
        self.obj("criterion-shared", changed)["properties"]["version"] = 2
        affected = ontology.impact(self.state, changed)
        self.assertEqual(set(affected["affected_objects"]),
                         {"criterion-shared", "evaluation-a", "evaluation-b", "comparison-ab"})
        self.assertTrue(any("uses-a" in step for step in affected["paths"]["evaluation-a"]))
        changed = copy.deepcopy(self.state)
        self.obj("experiment-a", changed)["properties"]["prompt"] = "Farklı komut"
        self.assertEqual(set(ontology.impact(self.state, changed)["affected_objects"]),
                         {"experiment-a", "artifact-a", "evaluation-a", "comparison-ab"})

    def test_none_links_do_not_invalidate_or_enter_fingerprint(self):
        before = self.fp(["experiment-a"])
        changed = copy.deepcopy(self.state)
        changed["relations"] = [r for r in changed["relations"] if r["id"] != "display-related"]
        self.assertEqual(ontology.impact(self.state, changed)["affected_objects"], [])
        self.assertEqual(before, self.fp(["experiment-a"], changed))
        self.assertNotIn("experiment-c", ontology.upstream(self.state, ["experiment-a"]))

    def test_removed_relation_affects_dependent_but_not_source(self):
        changed = copy.deepcopy(self.state)
        relation = next(r for r in changed["relations"] if r["id"] == "uses-a")
        relation["to"] = "criterion-independent"
        affected = ontology.impact(self.state, changed)
        self.assertEqual(set(affected["affected_objects"]), {"evaluation-a", "comparison-ab"})
        self.assertEqual(self.fp(["criterion-shared"]), self.fp(["criterion-shared"], changed))
        self.assertNotEqual(self.fp(["evaluation-a"]), self.fp(["evaluation-a"], changed))

    def test_cycles_and_bidirectional_effects_terminate(self):
        graph = copy.deepcopy(self.state)
        graph["ontology"]["relation_types"].append(relation_type("peer", "Experiment", "Experiment", "both"))
        graph["relations"] += [link("peer-ab", "experiment-a", "peer", "experiment-b"),
                                link("peer-bc", "experiment-b", "peer", "experiment-c"),
                                link("peer-ca", "experiment-c", "peer", "experiment-a")]
        ontology.validate_graph(graph)
        self.assertEqual(ontology.upstream(graph, ["experiment-a"]),
                         {"experiment-a", "experiment-b", "experiment-c"})
        changed = copy.deepcopy(graph)
        self.obj("experiment-a", changed)["properties"]["prompt"] = "Changed"
        self.assertEqual(len(ontology.impact(graph, changed)["affected_objects"]), 10)

    def test_fingerprint_is_stable_across_record_order(self):
        reordered = copy.deepcopy(self.state)
        for key in ("objects", "relations"):
            reordered[key].reverse()
        for key in ("object_types", "relation_types"):
            reordered["ontology"][key].reverse()
        self.assertEqual(self.fp(["evaluation-a", "evaluation-b"]),
                         self.fp(["evaluation-b", "evaluation-a"], reordered))


class DomainWorkflowTests(NotebookCase):
    def test_producer_dependencies_gate_tasks_without_explicit_dependencies(self):
        self.assertEqual(self.task("evaluate-a")["depends_on"], [])
        self.reject(event("start_task", task_id="evaluate-a"))
        self.finish("run-a")
        self.assertIn("evaluate-a", core.inspect_state(self.state, self.root)["ready"])
        self.assertIn("run-a", self.view("evaluate-a")["effective_depends_on"])

    def test_shared_criterion_edit_invalidates_only_affected_completed_work(self):
        self.finish_all()
        old_files = {p.name: p.read_bytes() for p in self.root.iterdir()}
        self.apply("mutate_graph", operations=[self.replace("criterion-shared", version=2)])
        for identifier in ("evaluate-a", "evaluate-b", "compare-ab"):
            self.assertEqual(self.task(identifier)["status"], "review")
            self.assertEqual(self.task(identifier)["evidence"], [])
            self.assertTrue(self.task(identifier)["review_reasons"])
        for identifier in ("run-a", "run-b", "run-c", "evaluate-c"):
            self.assertEqual(self.view(identifier)["effective_status"], "done")
        self.assertEqual(old_files, {p.name: p.read_bytes() for p in self.root.iterdir()})

    def test_producer_redo_never_restores_consumer_old_acceptance(self):
        self.finish_all()
        old_generation = self.task("run-a")["generation"]
        self.apply("reopen_task", task_id="run-a")
        self.finish("run-a")
        self.assertEqual(self.task("run-a")["generation"], old_generation + 1)
        self.assertNotEqual(self.view("evaluate-a")["effective_status"], "done")
        self.assertEqual(self.task("evaluate-a")["evidence"], [])
        self.assertNotEqual(self.view("compare-ab")["effective_status"], "done")

    def test_external_producer_generation_change_is_detected(self):
        self.finish_all()
        self.task("run-a")["generation"] += 1
        self.assertEqual(self.view("evaluate-a")["effective_status"], "needs_review")
        self.assertEqual(self.view("compare-ab")["effective_status"], "needs_review")
        self.assertEqual(self.view("evaluate-c")["effective_status"], "done")

    def test_recorded_manifest_does_not_alias_live_domain_objects(self):
        self.finish("run-a")
        self.finish("evaluate-a")
        recorded = copy.deepcopy(self.task("evaluate-a")["input_snapshot"])
        self.obj("criterion-shared")["properties"]["definition"] = "Yeni ölçüt tanımı"
        self.assertEqual(self.task("evaluate-a")["input_snapshot"], recorded,
                         "Past acceptance changed when the current domain object was edited")

    def test_own_output_change_while_doing_does_not_invalidate_producer_inputs(self):
        self.finish("run-a")
        self.apply("start_task", task_id="evaluate-a")
        snapshot = copy.deepcopy(self.task("evaluate-a")["run_snapshot"])
        self.apply("mutate_graph", operations=[self.replace("evaluation-a", score=3)])
        self.assertEqual(self.view("evaluate-a")["effective_status"], "doing")
        self.assertEqual(self.task("evaluate-a")["run_snapshot"], snapshot)
        self.apply("submit_evidence", task_id="evaluate-a", items=evidence("evaluate-a"))
        self.apply("complete_task", task_id="evaluate-a")

    def test_accepted_output_tamper_requires_review_even_when_inputs_unchanged(self):
        self.finish_all()
        self.obj("evaluation-a")["properties"]["score"] = 3
        self.assertEqual(self.view("evaluate-a")["effective_status"], "needs_review")
        self.assertNotEqual(self.view("compare-ab")["effective_status"], "done")

    def test_input_closure_cannot_consume_own_output(self):
        bad = copy.deepcopy(self.state)
        run = self.task("run-a", bad)
        run["input_ids"] = ["evaluation-a"]
        run["object_ids"].append("evaluation-a")
        with self.assertRaisesRegex(ValueError, "own output"):
            core.validate(bad)

    def test_producer_edges_and_explicit_dependencies_cannot_form_cycle(self):
        bad = copy.deepcopy(self.state)
        self.task("run-a", bad)["depends_on"] = ["evaluate-a"]
        with self.assertRaisesRegex(ValueError, "cycle"):
            core.validate(bad)
        bad = copy.deepcopy(self.state)
        for suffix, other in (("a", "b"), ("b", "a")):
            task = self.task(f"run-{suffix}", bad)
            task["input_ids"] = [f"artifact-{other}"]
            task["object_ids"].append(f"artifact-{other}")
        with self.assertRaisesRegex(ValueError, "cycle"):
            core.validate(bad)

    def test_direct_object_and_relationship_edits_are_detected_by_snapshot(self):
        self.finish_all()
        bad = copy.deepcopy(self.state)
        self.obj("criterion-shared", bad)["properties"]["version"] = 2
        self.assertEqual(self.view("evaluate-a", bad)["effective_status"], "needs_review")
        self.assertEqual(self.view("compare-ab", bad)["effective_status"], "needs_review")
        self.assertEqual(self.view("evaluate-c", bad)["effective_status"], "done")
        bad = copy.deepcopy(self.state)
        next(r for r in bad["relations"] if r["id"] == "uses-a")["to"] = "criterion-independent"
        self.assertEqual(self.view("compare-ab", bad)["effective_status"], "needs_review")
        self.assertEqual(self.view("evaluate-b", bad)["effective_status"], "done")

    def test_direct_input_file_change_invalidates_consumers_with_unchanged_reports(self):
        self.finish_all()
        before = (self.root / "inceleme-evaluate-a.md").read_bytes()
        (self.root / "çıktı-a.txt").write_text("Dosya dışarıdan değişti.", encoding="utf-8")
        self.assertEqual(self.view("evaluate-a")["effective_status"], "needs_review")
        self.assertEqual(self.view("compare-ab")["effective_status"], "needs_review")
        self.assertEqual(self.view("evaluate-c")["effective_status"], "done")
        self.assertEqual(before, (self.root / "inceleme-evaluate-a.md").read_bytes())

    def test_complete_rejects_input_changed_since_submission(self):
        self.finish("run-a")
        self.apply("start_task", task_id="evaluate-a")
        self.apply("submit_evidence", task_id="evaluate-a", items=evidence("evaluate-a"))
        self.obj("criterion-shared")["properties"]["version"] = 2
        self.reject(event("complete_task", task_id="evaluate-a"))

    def test_preview_matches_apply_without_mutating_state_or_files(self):
        self.finish_all()
        payload = event("mutate_graph", operations=[self.replace("criterion-shared", version=2)])
        before = copy.deepcopy(self.state)
        files = {p.name: p.read_bytes() for p in self.root.iterdir()}
        preview = core.preview_event(self.state, payload, self.root)
        self.assertEqual(self.state, before)
        self.assertEqual(files, {p.name: p.read_bytes() for p in self.root.iterdir()})
        committed = core.apply_event(self.state, payload, self.root)
        self.assertEqual(preview["next_context"], core.inspect_state(committed, self.root))
        self.assertEqual(preview["impact"], committed["history"][-1]["change"]["impact"])
        self.assertFalse(preview["state_committed"])

    def test_schema_change_invalidates_relevant_instances_and_missing_properties_reject(self):
        self.finish_all()
        replacement = copy.deepcopy(self.state["ontology"])
        criterion = next(t for t in replacement["object_types"] if t["id"] == "Criterion")
        criterion["properties"]["version"]["enum"] = [1, 2]
        self.apply("mutate_graph", operations=[{"op": "replace_ontology", "ontology": replacement}])
        self.assertEqual(self.task("evaluate-a")["status"], "review")
        self.assertEqual(self.task("evaluate-c")["status"], "review")
        self.assertEqual(self.view("run-a")["effective_status"], "done")

    def test_display_label_changes_preserve_domain_acceptance(self):
        self.finish_all()
        replacement = copy.deepcopy(self.state["ontology"])
        next(t for t in replacement["object_types"] if t["id"] == "Criterion")["label"] = "Görünen yeni ad"
        next(t for t in replacement["relation_types"] if t["id"] == "uses")["label"] = "Görünen yeni bağ adı"
        obj = copy.deepcopy(self.obj("criterion-shared"))
        obj["label"] = "Görünen ölçüt adı"
        self.apply("mutate_graph", operations=[{"op": "replace_ontology", "ontology": replacement},
                                                {"op": "replace_object", "object": obj}])
        self.assertEqual(self.view("evaluate-a")["effective_status"], "done")
        self.assertEqual(self.view("compare-ab")["effective_status"], "done")

    def test_migration_preserves_history_and_requires_explicit_all_task_bindings(self):
        legacy = build_spec()
        write_artifacts(self.root)
        for action, fields in (("start_task", {}),
                               ("submit_evidence", {"items": evidence_items("T-DATA")}),
                               ("complete_task", {})):
            legacy = core.apply_event(legacy, event(action, task_id="T-DATA", **fields), self.root)
        graph = build_notebook()
        bindings = [
            {"task_id": "T-DATA", "object_ids": ["experiment-a", "artifact-a"],
             "input_ids": ["experiment-a"], "output_ids": ["artifact-a"]},
            {"task_id": "T-COMPARE", "object_ids": ["artifact-a", "evaluation-a"],
             "input_ids": ["artifact-a"], "output_ids": ["evaluation-a"]},
            {"task_id": "T-CHECK", "object_ids": ["evaluation-a", "comparison-ab"],
             "input_ids": ["evaluation-a"], "output_ids": ["comparison-ab"]},
        ]
        payload = event("migrate_ontology", ontology=graph["ontology"], objects=graph["objects"],
                        relations=graph["relations"], bindings=bindings)
        incomplete = copy.deepcopy(payload)
        incomplete["bindings"].pop()
        before = copy.deepcopy(legacy)
        with self.assertRaises(ValueError):
            core.apply_event(legacy, incomplete, self.root)
        self.assertEqual(legacy, before)
        migrated = core.apply_event(legacy, payload, self.root)
        self.assertEqual(migrated["schema_version"], 3)
        self.assertEqual(migrated["history"][:-1], legacy["history"])
        preserved = migrated["history"][-1]["change"]["previous_tasks"]
        self.assertEqual(preserved, legacy["tasks"])
        self.assertEqual(self.task("T-DATA", migrated)["status"], "review")
        self.assertEqual(self.task("T-DATA", migrated)["evidence"], [])
        self.assertIsNone(self.task("T-DATA", migrated)["input_snapshot"])
        self.assertEqual(self.task("T-DATA", migrated)["generation"], 0)


if __name__ == "__main__":
    unittest.main()
