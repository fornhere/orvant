"""The optional adviser must preserve project state and refuse unbound evidence."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from ontology_fixtures import build_notebook, link, prop, relation_type, task

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "skills" / "orvant" / "scripts"))
import jev_review


class JevReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = self.root / "source.txt"
        source.write_text("Ölçüm sonucu 0. Deney yalnız A koşulunda yapıldı.\n", encoding="utf-8")
        self.state = build_notebook()
        self.state["ontology"] = {
            "object_types": [
                {"id": "Source", "label": "Kaynak", "properties": {"path": prop("file")}},
                {"id": "Claim", "label": "İddia", "properties": {"text": prop("string")}},
            ],
            "relation_types": [relation_type("supports", "Source", "Claim", "none")],
        }
        self.state["objects"] = [
            {"id": "source-a", "type": "Source", "label": "Kaynak A", "properties": {"path": "source.txt"}},
            {"id": "claim-a", "type": "Claim", "label": "İddia A", "properties": {"text": "Ölçüm sonucu 100."}},
        ]
        self.state["relations"] = [link("support-a", "source-a", "supports", "claim-a")]
        self.state["tasks"] = [task("review-claim", [], ["claim-a"])]
        self.state["tasks"][0]["support_groups"] = [{"id": "support", "mode": "any", "branches": [
            {"id": "a", "input_ids": ["source-a"], "relation_ids": ["support-a"]}]}]
        folder = self.root / ".project"
        (folder / "scripts").mkdir(parents=True)
        (folder / "scripts" / "project.py").write_text("# fixture\n", encoding="utf-8")
        (folder / "scripts" / "core.py").write_text("# fixture\n", encoding="utf-8")
        self.state_path = folder / "state.json"
        self.save()
        self.request = {
            "project_id": self.state["project"]["id"], "task_id": "review-claim",
            "criterion": 0, "claim": "Ölçüm sonucu 100.",
            "claim_object_id": "claim-a", "claim_property": "text", "source_object_id": "source-a",
            "source_property": "path", "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "quote": "Ölçüm sonucu 0.",
        }

    def save(self):
        self.state_path.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")

    def fake_answer(self, verdict="contradicts"):
        return {"model": "jev-1.13.0", "answers": {"relation": {
            "type": "choice", "choice": verdict,
            "probabilities": {"supports": 0.01, "contradicts": 0.98, "insufficient": 0.01},
            "confidence": 0.96}}, "usage": {"input_tokens": 34, "output_tokens": 8}}

    def shadow(self, transport, **overrides):
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "fixture-key"}):
            return jev_review.review(self.root, self.request, mode="shadow", send_source=True,
                                     transport=transport, **overrides)

    def test_off_is_read_only_and_does_not_call_provider(self):
        before = self.state_path.read_bytes()
        result = jev_review.review(self.root, self.request,
                                   transport=lambda *_: self.fail("provider was called"))
        self.assertEqual(result["status"], "off")
        self.assertFalse(result["approved"])
        self.assertEqual(self.state_path.read_bytes(), before)

    def test_shadow_requires_explicit_source_send(self):
        with self.assertRaisesRegex(jev_review.ReviewError, "source_send_not_authorized"):
            jev_review.review(self.root, self.request, mode="shadow",
                              transport=lambda *_: self.fail("provider was called"))

    def test_shadow_reports_contradiction_without_completing_task(self):
        before = self.state_path.read_bytes()
        def transport(endpoint, body, key, timeout):
            self.assertEqual(endpoint, "https://api.typesafe.ai/v1/systemone")
            self.assertEqual(body["state"]["claim"], "Ölçüm sonucu 100.")
            self.assertEqual(body["state"]["exact_quote"], "Ölçüm sonucu 0.")
            self.assertNotIn("state.json", json.dumps(body))
            return self.fake_answer()
        result = self.shadow(transport)
        self.assertEqual(result["status"], "advisory_only")
        self.assertEqual(result["verdict"], "contradicts")
        self.assertFalse(result["approved"])
        self.assertFalse(result["state_written"])
        self.assertEqual(self.state_path.read_bytes(), before)

    def test_scope_quote_revision_and_file_type_are_local_gates(self):
        variants = [
            ({"project_id": "other"}, "project_mismatch"),
            ({"claim_object_id": "other"}, "claim_not_task_output"),
            ({"claim": "Ölçüm sonucu 200."}, "claim_not_recorded"),
            ({"source_object_id": "other"}, "source_not_bound_to_task"),
            ({"quote": "Ölçüm sonucu 100."}, "quote_not_in_source"),
            ({"source_sha256": "0" * 64}, "source_revision_unreviewed"),
            ({"source_property": "text"}, "source_property_not_file"),
        ]
        for changes, error in variants:
            with self.subTest(error=error):
                request = {**self.request, **changes}
                with self.assertRaisesRegex(jev_review.ReviewError, error):
                    jev_review.review(self.root, request, mode="shadow", send_source=True,
                                      transport=lambda *_: self.fail("provider was called"))

    def test_unlinked_source_is_not_reviewed(self):
        self.state["relations"] = []
        self.save()
        with self.assertRaisesRegex(jev_review.ReviewError, "source_not_linked_to_claim"):
            jev_review.review(self.root, self.request, mode="shadow", send_source=True,
                              transport=lambda *_: self.fail("provider was called"))

    def test_changed_source_after_call_degrades_without_verdict(self):
        def transport(*_):
            (self.root / "source.txt").write_text("Ölçüm sonucu 200.\n", encoding="utf-8")
            return self.fake_answer()
        result = self.shadow(transport)
        self.assertEqual(result["status"], "degraded")
        self.assertIsNone(result["verdict"])
        self.assertEqual(result["diagnostic"], "source_revision_unreviewed")

    def test_changed_project_after_call_degrades_without_verdict(self):
        def transport(*_):
            self.state["project"]["goal"] = "Değişmiş hedef"
            self.save()
            return self.fake_answer()
        result = self.shadow(transport)
        self.assertEqual(result["status"], "degraded")
        self.assertIsNone(result["verdict"])
        self.assertEqual(result["diagnostic"], "source_or_project_changed")

    def test_malformed_provider_answer_degrades(self):
        for raw in ({"answers": {}}, self.fake_answer("supports")):
            with self.subTest(raw=raw):
                result = self.shadow(lambda *_: copy.deepcopy(raw))
                self.assertEqual(result["status"], "degraded")
                self.assertEqual(result["diagnostic"], "answer_invalid")
                self.assertIsNone(result["verdict"])

    def test_secret_and_unsafe_endpoint_never_reach_transport(self):
        self.state["objects"][1]["properties"]["text"] = "api_key=abcdefghijklmnopqrstuvwxyz"
        self.save()
        self.request["claim"] = "api_key=abcdefghijklmnopqrstuvwxyz"
        with self.assertRaisesRegex(jev_review.ReviewError, "possible_secret_in_input"):
            self.shadow(lambda *_: self.fail("provider was called"))
        self.state["objects"][1]["properties"]["text"] = "Ölçüm sonucu 100."
        self.save()
        self.request["claim"] = "Ölçüm sonucu 100."
        with self.assertRaisesRegex(jev_review.ReviewError, "endpoint_invalid"):
            self.shadow(lambda *_: self.fail("provider was called"), base_url="http://remote.example")


if __name__ == "__main__":
    unittest.main()
