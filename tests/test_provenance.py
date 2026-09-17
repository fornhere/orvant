"""Immutable history, scoped run provenance and preview/apply external inputs."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ontology_fixtures import build_notebook, event, link, prop, relation_type, task

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "skills" / "orvant" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import core


def projected_source_spec():
    """A summary consumes source.text, deliberately not its archived file."""
    state = build_notebook()
    state["ontology"] = {
        "object_types": [
            {"id": "Source", "label": "Kaynak", "properties": {"text": prop("string"), "archive": prop("file")}},
            {"id": "Summary", "label": "Özet", "properties": {"text": prop("string")}},
        ],
        "relation_types": [relation_type("derived", "Source", "Summary", "forward")],
    }
    state["objects"] = [
        {"id": "source", "type": "Source", "label": "Kurmaca kaynak",
         "properties": {"text": "Özetlenecek metin", "archive": "archive.txt"}},
        {"id": "summary", "type": "Summary", "label": "Kurmaca özet", "properties": {"text": "Kısa metin"}},
    ]
    state["relations"] = [link("source-summary", "source", "derived", "summary")]
    state["tasks"] = [task("summarize", ["source"], ["summary"])]
    state["tasks"][0]["input_fields"] = {"source": ["text"]}
    return state


class ProvenanceCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = projected_source_spec()
        (self.root / "proof.md").write_text("Kurmaca özet girdisiyle karşılaştırıldı.", encoding="utf-8")

    def apply(self, action, **fields):
        self.state = core.apply_event(self.state, event(action, **fields), self.root)

    def rejected(self, payload, pattern=None):
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, pattern) if pattern else self.assertRaises(ValueError):
            core.apply_event(self.state, payload, self.root)
        self.assertEqual(self.state, before)

    def submit(self):
        self.apply("submit_evidence", task_id="summarize", items=[
            {"path": "proof.md", "criterion": 0, "note": "Kurmaca özet metinle karşılaştırıldı.", "reviewer": "test"}])


class ImmutableProvenanceTests(ProvenanceCase):
    def test_immutable_object_semantics_cannot_be_replaced_but_display_label_can(self):
        self.state["ontology"]["object_types"][0]["immutable"] = True
        changed = copy.deepcopy(self.state["objects"][0])
        changed["properties"]["text"] = "Tarihi kaynak değiştirildi"
        self.rejected(event("mutate_graph", operations=[{"op": "replace_object", "object": changed}]), "immutable")
        changed = copy.deepcopy(self.state["objects"][0])
        changed["label"] = "Yeni görünen ad"
        self.apply("mutate_graph", operations=[{"op": "replace_object", "object": changed}])
        self.assertEqual(self.state["objects"][0]["label"], "Yeni görünen ad")

    def test_unfreezing_object_type_in_same_transaction_cannot_rewrite_history(self):
        self.state["ontology"]["object_types"][0]["immutable"] = True
        schema = copy.deepcopy(self.state["ontology"])
        schema["object_types"][0]["immutable"] = False
        changed = copy.deepcopy(self.state["objects"][0])
        changed["properties"]["text"] = "Yeni değer"
        self.rejected(event("mutate_graph", operations=[
            {"op": "replace_ontology", "ontology": schema}, {"op": "replace_object", "object": changed}]), "immutable")

    def test_immutable_relation_cannot_be_removed_after_schema_unfreeze(self):
        self.state["ontology"]["relation_types"][0]["immutable"] = True
        schema = copy.deepcopy(self.state["ontology"])
        schema["relation_types"][0]["immutable"] = False
        for operations in (
            [{"op": "remove_relation", "relation_id": "source-summary"}],
            [{"op": "replace_ontology", "ontology": schema}, {"op": "remove_relation", "relation_id": "source-summary"}],
        ):
            with self.subTest(operations=operations):
                self.rejected(event("mutate_graph", operations=operations), "immutable")

    def test_retired_object_id_cannot_return_through_mutation_or_extension(self):
        spare = {"id": "spare-summary", "type": "Summary", "label": "İlk kimlik",
                 "properties": {"text": "Eski örnek"}}
        self.apply("mutate_graph", operations=[{"op": "add_object", "object": spare}])
        self.apply("mutate_graph", operations=[{"op": "remove_object", "object_id": spare["id"]}])
        replacement = copy.deepcopy(spare)
        replacement["properties"]["text"] = "Aynı kimlikte farklı örnek"
        self.rejected(event("mutate_graph", operations=[{"op": "add_object", "object": replacement}]), "retired")
        self.rejected(event("extend_model", objects=[replacement], relations=[], tasks=[]), "retired")

    def test_retired_relation_id_cannot_return_through_mutation_or_extension(self):
        retired = copy.deepcopy(self.state["relations"][0])
        self.apply("mutate_graph", operations=[{"op": "remove_relation", "relation_id": retired["id"]}])
        self.rejected(event("mutate_graph", operations=[{"op": "add_relation", "relation": retired}]), "retired")
        self.rejected(event("extend_model", objects=[], relations=[retired], tasks=[]), "retired")


class RunScopeTests(ProvenanceCase):
    def test_excluded_missing_upstream_file_does_not_block_output_acceptance(self):
        self.assertFalse((self.root / "archive.txt").exists())
        self.apply("start_task", task_id="summarize")
        self.submit()
        self.apply("complete_task", task_id="summarize")
        report = core.inspect_state(self.state, self.root)
        self.assertEqual(report["tasks"][0]["effective_status"], "done")
        self.assertEqual(self.state["tasks"][0]["output_snapshot"]["manifest"]["files"], [])

    def test_input_change_since_start_cannot_be_hidden_by_fresh_evidence(self):
        self.apply("start_task", task_id="summarize")
        self.state["objects"][0]["properties"]["text"] = "Çalıştırmadan sonra farklı girdi"
        payload = event("submit_evidence", task_id="summarize", items=[
            {"path": "proof.md", "criterion": 0, "note": "Yeni dosya var", "reviewer": "test"}])
        self.rejected(payload, "run inputs changed")

    def test_unselected_source_field_change_does_not_stale_running_task(self):
        self.apply("start_task", task_id="summarize")
        changed = copy.deepcopy(self.state["objects"][0])
        changed["properties"]["archive"] = "different-missing.txt"
        self.apply("mutate_graph", operations=[{"op": "replace_object", "object": changed}])
        self.assertEqual(core.inspect_state(self.state, self.root)["tasks"][0]["effective_status"], "doing")
        self.submit()
        self.apply("complete_task", task_id="summarize")


class PreviewDigestCLITests(ProvenanceCase):
    def setUp(self):
        super().setUp()
        # CLI commands use real source bytes, without a field projection.
        self.state["tasks"][0].pop("input_fields")
        (self.root / "archive.txt").write_text("Original source bytes", encoding="utf-8")
        self.project_root = self.root / "project"
        self.project_root.mkdir()
        for name in ("archive.txt", "proof.md"):
            (self.project_root / name).write_bytes((self.root / name).read_bytes())
        self.spec_path = self.root / "spec.json"
        self.spec_path.write_text(json.dumps(self.state), encoding="utf-8")
        self.cli("init", str(self.project_root), "--spec", str(self.spec_path))
        self.event_path = self.root / "event.json"

    def cli(self, *args, expected=0):
        result = subprocess.run([sys.executable, str(SCRIPTS / "project.py"), *args],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stdout + result.stderr)
        return json.loads(result.stdout)

    def prepare(self, action="start_task"):
        payload = event(action, task_id="summarize")
        self.event_path.write_text(json.dumps(payload), encoding="utf-8")
        state = json.loads((self.project_root / ".project" / "state.json").read_text())
        revision = state["revision"]
        report = self.cli("preview", str(self.project_root), "--event", str(self.event_path),
                          "--expected-revision", str(revision))
        self.assertIn("preview_digest", report)
        return report["preview_digest"], revision

    def apply_previewed(self, digest, revision, expected=0):
        return self.cli("apply", str(self.project_root), "--event", str(self.event_path),
                        "--expected-revision", str(revision), "--preview-digest", digest, expected=expected)

    def test_preview_digest_allows_unchanged_source_and_event(self):
        digest, revision = self.prepare()
        self.assertEqual(len(digest), 64)
        report = self.apply_previewed(digest, revision)
        self.assertTrue(report["ok"])
        state = json.loads((self.project_root / ".project" / "state.json").read_text())
        self.assertEqual(state["tasks"][0]["status"], "doing")

    def test_preview_digest_rejects_source_edit_without_revision_change(self):
        digest, revision = self.prepare()
        before = (self.project_root / ".project" / "state.json").read_bytes()
        (self.project_root / "archive.txt").write_text("Changed after preview", encoding="utf-8")
        report = self.apply_previewed(digest, revision, expected=1)
        self.assertIn("preview", report["error"].lower())
        self.assertEqual((self.project_root / ".project" / "state.json").read_bytes(), before)

    def test_preview_digest_rejects_evidence_edit_between_preview_and_apply(self):
        state = json.loads((self.project_root / ".project" / "state.json").read_text())
        state = core.apply_event(state, event("start_task", task_id="summarize"), self.project_root)
        (self.project_root / ".project" / "state.json").write_text(json.dumps(state), encoding="utf-8")
        # A fresh submit normally accepts the CURRENT proof bytes. The preview
        # guard must bind the bytes actually reviewed, not merely rely on the
        # older complete_task stale-evidence check.
        payload = event("submit_evidence", task_id="summarize", items=[
            {"path": "proof.md", "criterion": 0, "note": "Kurmaca inceleme", "reviewer": "test"}])
        self.event_path.write_text(json.dumps(payload), encoding="utf-8")
        revision = state["revision"]
        preview = self.cli("preview", str(self.project_root), "--event", str(self.event_path),
                           "--expected-revision", str(revision))
        digest = preview["preview_digest"]
        before = (self.project_root / ".project" / "state.json").read_bytes()
        (self.project_root / "proof.md").write_text("Changed evidence after preview", encoding="utf-8")
        report = self.apply_previewed(digest, revision, expected=1)
        self.assertIn("preview", report["error"].lower())
        self.assertEqual((self.project_root / ".project" / "state.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
