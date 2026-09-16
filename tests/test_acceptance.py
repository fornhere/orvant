"""Behavioral checks for reviewed alternative support and safe domain rules."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest

from ontology_fixtures import build_notebook, prop, relation_type, link, task as domain_task, event

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "skills" / "proje-baslat" / "scripts"))
import acceptance
import core
import ontology


def support_graph():
    return {
        "ontology": {
            "object_types": [
                {"id": "Source", "label": "Kaynak", "properties": {"text": prop("string"), "path": prop("file")}},
                {"id": "Claim", "label": "İddia", "properties": {"text": prop("string")}},
            ],
            "relation_types": [relation_type("supports", "Source", "Claim", "none")],
        },
        "objects": [
            {"id": "source-a", "type": "Source", "label": "Kaynak A", "properties": {"text": "İlk destek", "path": "a.txt"}},
            {"id": "source-b", "type": "Source", "label": "Kaynak B", "properties": {"text": "Alternatif destek", "path": "b.txt"}},
            {"id": "claim", "type": "Claim", "label": "Dar iddia", "properties": {"text": "İncelenecek iddia"}},
        ],
        "relations": [link("supports-a", "source-a", "supports", "claim"),
                      link("supports-b", "source-b", "supports", "claim")],
    }


def supports(mode="any"):
    return {"support_groups": [{"id": "claim-support", "mode": mode, "branches": [
        {"id": "a", "input_ids": ["source-a"], "relation_ids": ["supports-a"]},
        {"id": "b", "input_ids": ["source-b"], "relation_ids": ["supports-b"]},
    ]}]}


def selector(roots, *steps, property=None):
    return {"roots": roots, "path": [{"relation_type": kind, "direction": direction}
                                      for kind, direction in steps], "property": property}


class SupportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "a.txt").write_text("Original A", encoding="utf-8")
        (self.root / "b.txt").write_text("Original B", encoding="utf-8")
        self.state, self.task = support_graph(), supports()

    def capture(self, branches, statuses=None):
        return acceptance.support_snapshot(self.state, self.task, self.root, core._hash_file,
                                           branches, ontology.snapshot, statuses or {})

    def inspect(self, stored, statuses=None):
        return acceptance.inspect_supports(self.state, self.task, stored, self.root, core._hash_file,
                                           ontology.snapshot, statuses or {})

    def remove_source(self, suffix):
        self.state["objects"] = [obj for obj in self.state["objects"] if obj["id"] != f"source-{suffix}"]
        self.state["relations"] = [rel for rel in self.state["relations"] if rel["id"] != f"supports-{suffix}"]

    def test_any_only_snapshots_explicitly_reviewed_branches(self):
        stored = self.capture(["claim-support/a"])
        self.assertEqual([b["id"] for b in stored["groups"][0]["branches"]], ["a"])
        self.assertTrue(self.inspect(stored)["current"])
        self.remove_source("a")
        result = self.inspect(stored)
        self.assertFalse(result["current"], "Unreviewed B must not substitute for A")
        self.assertEqual(result["lost"], ["claim-support/a"])
        self.assertEqual(result["groups"][0]["unreviewed"], ["b"])

    def test_reviewed_any_survives_one_loss_but_all_does_not(self):
        for mode, expected in (("any", True), ("all", False)):
            with self.subTest(mode=mode):
                self.state, self.task = support_graph(), supports(mode)
                stored = self.capture(["claim-support/a", "claim-support/b"])
                self.remove_source("a")
                result = self.inspect(stored)
                self.assertEqual(result["current"], expected)
                self.assertEqual(result["lost"], ["claim-support/a"])

    def test_no_implicit_review_unknown_or_duplicate_review_ids_rejected(self):
        for branches in ([], ["claim-support/missing"], ["claim-support/a", "claim-support/a"]):
            with self.subTest(branches=branches), self.assertRaises(ValueError):
                self.capture(branches)
        self.task = supports("all")
        with self.assertRaises(ValueError):
            self.capture(["claim-support/a"])

    def test_missing_source_is_inspectable_contract_but_cannot_be_reviewed(self):
        self.remove_source("a")
        acceptance.validate_contract(self.task, self.state)
        with self.assertRaisesRegex(ValueError, "missing"):
            self.capture(["claim-support/a"])

    def test_witness_removed_or_retargeted_loses_exact_review(self):
        stored = self.capture(["claim-support/a"])
        original = copy.deepcopy(self.state)
        self.state["relations"].pop(0)
        self.assertEqual(self.inspect(stored)["lost"], ["claim-support/a"])
        self.state = original
        self.state["relations"][0]["from"] = "source-b"
        self.assertFalse(self.inspect(stored)["current"])

    def test_changed_source_file_invalidates_even_if_source_text_unchanged(self):
        stored = self.capture(["claim-support/a"])
        (self.root / "a.txt").write_text("Edited file A", encoding="utf-8")
        self.assertFalse(self.inspect(stored)["current"])
        self.assertEqual(self.inspect(stored)["lost"], ["claim-support/a"])

    def test_latched_loss_stays_lost_after_restoration_until_new_review(self):
        original = copy.deepcopy(self.state)
        stored = self.capture(["claim-support/a", "claim-support/b"])
        self.remove_source("a")
        result = self.inspect(stored)
        self.assertTrue(result["current"])
        latched = {"groups": result["groups"]}
        self.state = original
        restored = self.inspect(latched)
        self.assertTrue(restored["current"], "Still supported by reviewed B")
        self.assertEqual(restored["lost"], [], "Already reported loss must not be new again")
        a = next(b for b in restored["groups"][0]["branches"] if b["id"] == "a")
        self.assertTrue(a["lost"])
        self.remove_source("b")
        self.assertFalse(self.inspect(latched)["current"], "Restoring A must not re-accept it")
        renewed = self.capture(["claim-support/a"])
        self.assertTrue(self.inspect(renewed)["current"])

    def test_pending_input_producer_rejects_but_pending_claim_output_does_not(self):
        with self.assertRaisesRegex(ValueError, "producer not current"):
            self.capture(["claim-support/a"], {"source-a": "pending"})
        stored = self.capture(["claim-support/a"], {"source-a": "current", "claim": "pending"})
        self.assertFalse(self.inspect(stored, {"source-a": "needs_review"})["current"])

    def test_full_support_closure_producer_and_generation_are_checked(self):
        self.state["ontology"]["relation_types"].append(relation_type("derived", "Source", "Source", "forward"))
        self.state["relations"].append(link("derived-ba", "source-b", "derived", "source-a"))
        with self.assertRaisesRegex(ValueError, "source-b"):
            self.capture(["claim-support/a"], {"source-a": "current", "source-b": "pending"})
        generations = {"run-a": 1}
        def manifest(state, inputs, root, hash_file):
            value = ontology.snapshot(state, inputs, root, hash_file)
            value["producer_generations"] = dict(generations)
            return value
        stored = acceptance.support_snapshot(self.state, self.task, self.root, core._hash_file,
                                             ["claim-support/a"], manifest, {})
        generations["run-a"] = 2
        self.assertFalse(acceptance.inspect_supports(self.state, self.task, stored, self.root,
                                                    core._hash_file, manifest, {})["current"])

    def test_snapshot_and_inspection_never_alias_or_mutate_accepted_data(self):
        stored = self.capture(["claim-support/a"])
        before = copy.deepcopy(stored)
        self.state["objects"][0]["properties"]["text"] = "Changed"
        self.assertEqual(stored, before)
        result = self.inspect(stored)
        self.assertFalse(result["current"])
        self.assertEqual(stored, before)

    def test_contract_changes_and_witness_schema_changes_require_review(self):
        stored = self.capture(["claim-support/a"])
        self.state["ontology"]["relation_types"][0]["from_max"] = 2
        self.assertFalse(self.inspect(stored)["current"])
        self.state = support_graph()
        self.task["support_groups"][0]["mode"] = "all"
        self.assertFalse(self.inspect(stored)["current"])
        self.task["support_groups"] = []
        self.assertFalse(self.inspect(stored)["current"], "Removing contract cannot restore accepted claim")

    def test_display_labels_do_not_change_reviewed_support_semantics(self):
        stored = self.capture(["claim-support/a"])
        self.state["objects"][0]["label"] = "Kaynak için yeni görünen ad"
        self.state["ontology"]["object_types"][0]["label"] = "Yeni tür adı"
        self.state["ontology"]["relation_types"][0]["label"] = "Yeni ilişki adı"
        self.assertTrue(self.inspect(stored)["current"])

    def test_empty_optional_contract_is_backward_compatible(self):
        acceptance.validate_contract({}, self.state)
        stored = acceptance.support_snapshot(self.state, {}, self.root, core._hash_file,
                                             [], ontology.snapshot, {})
        self.assertEqual(stored, {"groups": []})
        self.assertTrue(acceptance.inspect_supports(self.state, {}, stored, self.root, core._hash_file,
                                                   ontology.snapshot, {})["current"])


class RuleTests(unittest.TestCase):
    def setUp(self):
        self.state = build_notebook()

    def rule(self, op, **fields):
        return {"id": "rule", "label": "Kurmaca kabul koşulu", "op": op, **fields}

    def test_comparison_requires_exact_shared_criterion(self):
        rule = self.rule("equal_sets",
                         left=selector(["evaluation-a"], ("uses", "out")),
                         right=selector(["evaluation-b"], ("uses", "out")))
        self.assertTrue(acceptance.evaluate_rules(self.state, [rule])[0]["passed"])
        next(r for r in self.state["relations"] if r["id"] == "uses-b")["to"] = "criterion-independent"
        ontology.validate_graph(self.state)
        result = acceptance.evaluate_rules(self.state, [rule])[0]
        self.assertFalse(result["passed"])
        self.assertIn("criterion-independent", result["detail"])

    def test_inverse_multistep_selection_and_count(self):
        rule = self.rule("count", selector=selector(["criterion-shared"], ("uses", "in"), ("examines", "out")),
                         min=2, max=2)
        self.assertTrue(acceptance.evaluate_rules(self.state, [rule])[0]["passed"])

    def test_property_sets_and_disjoint_identity_sets(self):
        equal = self.rule("equal_sets", left=selector(["evaluation-a"], property="score"),
                          right=selector(["evaluation-b"], property="score"))
        distinct = {**self.rule("disjoint", left=selector(["evaluation-a"]), right=selector(["evaluation-b"])), "id": "distinct"}
        self.assertTrue(all(r["passed"] for r in acceptance.evaluate_rules(self.state, [equal, distinct])))
        self.assertFalse(acceptance.evaluate_rules(self.state, [self.rule("disjoint",
                         left=selector(["evaluation-a"], property="score"),
                         right=selector(["evaluation-b"], property="score"))])[0]["passed"])

    def test_nested_all_any_produce_explanations(self):
        passed = {"id": "two", "label": "İki sonuç", "op": "count", "selector": selector(["evaluation-a", "evaluation-b"]), "min": 2, "max": 2}
        failed = {"id": "three", "label": "Üç sonuç", "op": "count", "selector": selector(["evaluation-a", "evaluation-b"]), "min": 3, "max": 3}
        for op, expected in (("all", False), ("any", True)):
            result = acceptance.evaluate_rules(self.state, [self.rule(op, rules=[passed, failed])])[0]
            self.assertEqual(result["passed"], expected)
            self.assertIn("three: failed", result["detail"])

    def test_missing_roots_or_properties_fail_instead_of_vacuous_success(self):
        for selected in (selector(["missing"]), selector(["experiment-a"], property="note")):
            rule = self.rule("count", selector=selected, min=0, max=None)
            acceptance.validate_contract({"acceptance_rules": [rule]}, self.state)
            result = acceptance.evaluate_rules(self.state, [rule])[0]
            self.assertFalse(result["passed"])
            self.assertIn("missing", result["detail"])

    def test_count_property_values_preserves_equal_valued_nodes(self):
        rule = self.rule("count", selector=selector(["evaluation-a", "evaluation-b"], property="score"), min=2, max=2)
        self.assertTrue(acceptance.evaluate_rules(self.state, [rule])[0]["passed"])

    def test_unknown_fields_operators_and_paths_rejected_without_execution(self):
        valid = self.rule("count", selector=selector(["evaluation-a"]), min=1, max=1)
        invalid = [dict(valid, expression="__import__('os')"), dict(valid, op="eval"), dict(valid, min=True),
                   dict(valid, selector=selector(["evaluation-a"], ("invented", "out"))),
                   dict(valid, selector={**selector(["evaluation-a"]), "filter": "anything"})]
        for rule in invalid:
            with self.subTest(rule=rule), self.assertRaises(ValueError):
                acceptance.evaluate_rules(self.state, [rule])

    def test_rule_observation_tracks_membership_even_while_rule_keeps_passing(self):
        rule = self.rule("count", selector=selector(["comparison-ab"], ("includes", "out")), min=2, max=2)
        before = acceptance.observe_rules(self.state, [rule])
        self.assertTrue(acceptance.evaluate_rules(self.state, [rule])[0]["passed"])
        next(r for r in self.state["relations"] if r["id"] == "includes-b")["to"] = "evaluation-c"
        self.assertTrue(acceptance.evaluate_rules(self.state, [rule])[0]["passed"])
        after = acceptance.observe_rules(self.state, [rule])
        self.assertNotEqual(before, after)
        self.assertEqual(after["rules"][0]["selector"]["selected_ids"], ["evaluation-a", "evaluation-c"])
        self.assertEqual(before["rules"][0]["selector"]["selected_ids"], ["evaluation-a", "evaluation-b"])

    def test_rule_observation_records_empty_missing_and_property_values(self):
        empty = self.rule("count", selector=selector(["criterion-independent"], ("includes", "in")), min=0, max=0)
        observed = acceptance.observe_rules(self.state, [empty])["rules"][0]["selector"]
        self.assertEqual(observed["selected_ids"], [])
        self.assertEqual(observed["steps"][0]["relations"], [])
        missing = self.rule("count", selector=selector(["not-created"]), min=0, max=0)
        self.assertEqual(acceptance.observe_rules(self.state, [missing])["rules"][0]["selector"]["missing_roots"], ["not-created"])
        value = self.rule("count", selector=selector(["evaluation-a"], property="score"), min=1, max=1)
        before = acceptance.observe_rules(self.state, [value])
        next(o for o in self.state["objects"] if o["id"] == "evaluation-a")["properties"]["score"] = 3
        self.assertNotEqual(before, acceptance.observe_rules(self.state, [value]))

    def test_rule_observation_ignores_labels_but_tracks_semantic_schema(self):
        rule = self.rule("count", selector=selector(["comparison-ab"], ("includes", "out")), min=2, max=2)
        before = acceptance.observe_rules(self.state, [rule])
        rule["label"] = "Yeni görünen koşul adı"
        next(t for t in self.state["ontology"]["relation_types"] if t["id"] == "includes")["label"] = "Yeni görünen ilişki adı"
        self.assertEqual(before, acceptance.observe_rules(self.state, [rule]))
        next(t for t in self.state["ontology"]["relation_types"] if t["id"] == "includes")["from_max"] = 3
        self.assertNotEqual(before, acceptance.observe_rules(self.state, [rule]))


class CoreAcceptanceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("a.txt", "b.txt", "review.md"):
            (self.root / name).write_text(f"Synthetic {name}", encoding="utf-8")
        self.state = build_notebook()
        self.state.update(support_graph())
        claim = domain_task("review-claim", [], ["claim"])
        claim.update(supports())
        self.state["tasks"] = [claim]

    def apply(self, action, **fields):
        self.state = core.apply_event(self.state, event(action, **fields), self.root)

    def review(self, approved):
        self.apply("start_task", task_id="review-claim")
        self.apply("submit_evidence", task_id="review-claim", reviewed_supports=approved,
                   items=[{"path": "review.md", "criterion": 0, "note": "Kurmaca destek incelendi.", "reviewer": "test"}])
        self.apply("complete_task", task_id="review-claim")

    def view(self):
        return core.inspect_state(self.state, self.root)["tasks"][0]

    def remove(self, suffix):
        self.apply("mutate_graph", operations=[{"op": "remove_relation", "relation_id": f"supports-{suffix}"},
                                                {"op": "remove_object", "object_id": f"source-{suffix}"}])

    def test_any_review_keeps_acceptance_after_one_loss_and_latches_lost_branch(self):
        original = copy.deepcopy(self.state)
        self.review(["claim-support/a", "claim-support/b"])
        changed_source = copy.deepcopy(original["objects"][0])
        changed_source["properties"]["text"] = "Changed source A"
        self.apply("mutate_graph", operations=[{"op": "replace_object", "object": changed_source}])
        self.assertEqual(self.view()["effective_status"], "done")
        lost = next(b for b in self.state["tasks"][0]["support_snapshot"]["groups"][0]["branches"] if b["id"] == "a")
        self.assertTrue(lost["lost"])
        self.apply("mutate_graph", operations=[{"op": "replace_object", "object": original["objects"][0]}])
        self.remove("b")
        self.assertNotEqual(self.view()["effective_status"], "done", "Restored A cannot substitute without new review")
        self.assertEqual(self.state["tasks"][0]["evidence"], [])

    def test_any_unreviewed_alternative_does_not_rescue_lost_approved_source(self):
        self.review(["claim-support/a"])
        self.remove("a")
        self.assertNotEqual(self.view()["effective_status"], "done")
        self.assertTrue(any(o["id"] == "source-b" for o in self.state["objects"]))

    def test_all_review_loses_acceptance_when_one_source_lost(self):
        self.state["tasks"][0]["support_groups"][0]["mode"] = "all"
        self.review(["claim-support/a", "claim-support/b"])
        self.remove("a")
        self.assertNotEqual(self.view()["effective_status"], "done")

    def test_any_file_support_remains_current_until_all_reviewed_files_change(self):
        self.review(["claim-support/a", "claim-support/b"])
        # Source bytes belong to branch manifests. The unconditional evidence
        # is solely the actual review report, which remains unchanged.
        evidence_paths = {item["path"] for item in self.state["tasks"][0]["evidence"]}
        self.assertEqual(evidence_paths, {"review.md"})
        report_before = (self.root / "review.md").read_bytes()
        (self.root / "a.txt").write_text("A changed after review", encoding="utf-8")
        self.assertEqual(self.view()["effective_status"], "done")
        self.assertTrue(self.view()["support_status"]["current"])
        (self.root / "b.txt").write_text("B changed after review", encoding="utf-8")
        self.assertNotEqual(self.view()["effective_status"], "done")
        self.assertFalse(self.view()["support_status"]["current"])
        self.assertEqual((self.root / "review.md").read_bytes(), report_before)

    def test_file_support_loss_persisted_by_event_stays_lost_after_bytes_restored(self):
        original_a = (self.root / "a.txt").read_bytes()
        self.review(["claim-support/a", "claim-support/b"])
        (self.root / "a.txt").write_text("Temporarily changed A", encoding="utf-8")
        self.assertEqual(self.view()["effective_status"], "done")
        # A harmless display edit supplies the authorized transaction on which
        # observed loss is persisted; the read-only context itself does not write.
        claim = copy.deepcopy(next(obj for obj in self.state["objects"] if obj["id"] == "claim"))
        claim["label"] = "İddianın yeni görünen adı"
        self.apply("mutate_graph", operations=[{"op": "replace_object", "object": claim}])
        stored_a = next(branch for branch in self.state["tasks"][0]["support_snapshot"]["groups"][0]["branches"]
                        if branch["id"] == "a")
        self.assertTrue(stored_a["lost"])
        self.assertEqual(self.view()["effective_status"], "done")
        (self.root / "a.txt").write_bytes(original_a)
        (self.root / "b.txt").write_text("B now changed", encoding="utf-8")
        self.assertNotEqual(self.view()["effective_status"], "done")
        self.assertFalse(self.view()["support_status"]["current"],
                         "Restored A bytes cannot reinstate review after loss was recorded")

    def test_rule_observation_outside_run_inputs_stales_previous_acceptance(self):
        selected = selector(["claim"], ("supports", "in"))
        self.state["tasks"][0]["acceptance_rules"] = [
            {"id": "has-support", "label": "Kaynağı var", "op": "count", "selector": selected, "min": 1, "max": None}]
        self.review(["claim-support/a", "claim-support/b"])
        # The rule still passes with one source, and ANY support still passes;
        # nevertheless the accepted rule witnesses changed outside input_ids.
        self.state["relations"] = [r for r in self.state["relations"] if r["id"] != "supports-a"]
        self.assertTrue(acceptance.evaluate_rules(self.state, self.state["tasks"][0]["acceptance_rules"])[0]["passed"])
        self.assertNotEqual(self.view()["effective_status"], "done")


if __name__ == "__main__":
    unittest.main()
