"""Synthetic experiment notebook: comparable runs plus an independent branch.

The fixture states domain relationships independently of workflow dependencies:
no task has an explicit depends_on edge. Producers must be inferred correctly.
"""
from pathlib import Path


def prop(kind, required=True, enum=None):
    return {"type": kind, "required": required, "enum": enum}


def relation_type(identifier, source, target, impact, source_min=0, source_max=None,
                  target_min=0, target_max=None):
    return {"id": identifier, "label": identifier, "from_type": source, "to_type": target,
            "from_min": source_min, "from_max": source_max, "to_min": target_min,
            "to_max": target_max, "impact": impact}


def link(identifier, source, kind, target):
    return {"id": identifier, "from": source, "type": kind, "to": target}


def task(identifier, inputs, outputs):
    return {"id": identifier, "title": identifier, "status": "todo",
            "object_ids": inputs + outputs, "input_ids": inputs, "output_ids": outputs,
            "depends_on": [], "decision_ids": [], "acceptance": ["Kurmaca çıktı girdilere göre incelendi."],
            "evidence": [], "input_snapshot": None, "generation": 0, "review_reasons": []}


def build_notebook():
    types = [
        {"id": "Experiment", "label": "Deney", "properties": {
            "prompt": prop("string"), "repeats": prop("integer"),
            "synthetic": prop("boolean"), "tags": prop("string_list"),
            "configuration": prop("json"), "note": prop("string", required=False)}},
        {"id": "Artifact", "label": "Çıktı", "properties": {"path": prop("file")}},
        {"id": "Criterion", "label": "Ölçüt", "properties": {
            "definition": prop("string"), "version": prop("integer")}},
        {"id": "Evaluation", "label": "Değerlendirme", "properties": {
            "score": prop("number"), "verdict": prop("string", enum=["pass", "fail"])}},
        {"id": "Comparison", "label": "Karşılaştırma", "properties": {"conclusion": prop("string")}},
    ]
    relations = [
        relation_type("produces", "Experiment", "Artifact", "forward", 1, 1, 1, 1),
        relation_type("examines", "Evaluation", "Artifact", "reverse", 1, 1),
        relation_type("uses", "Evaluation", "Criterion", "reverse", 1, 1),
        relation_type("includes", "Comparison", "Evaluation", "reverse", 2, 2),
        relation_type("related", "Experiment", "Experiment", "none"),
    ]
    objects, links, tasks = [], [], []
    for suffix in ("a", "b", "c"):
        objects += [
            {"id": f"experiment-{suffix}", "type": "Experiment", "label": f"Kurmaca deney {suffix}",
             "properties": {"prompt": f"Örnek {suffix}", "repeats": 1, "synthetic": True,
                            "tags": ["kurmaca"], "configuration": {"temperature": 0}}},
            {"id": f"artifact-{suffix}", "type": "Artifact", "label": f"Çıktı {suffix}",
             "properties": {"path": f"çıktı-{suffix}.txt"}},
            {"id": f"evaluation-{suffix}", "type": "Evaluation", "label": f"Değerlendirme {suffix}",
             "properties": {"score": 4.0, "verdict": "pass"}},
        ]
        links += [link(f"produces-{suffix}", f"experiment-{suffix}", "produces", f"artifact-{suffix}"),
                  link(f"examines-{suffix}", f"evaluation-{suffix}", "examines", f"artifact-{suffix}"),
                  link(f"uses-{suffix}", f"evaluation-{suffix}", "uses",
                       "criterion-independent" if suffix == "c" else "criterion-shared")]
        tasks += [task(f"run-{suffix}", [f"experiment-{suffix}"], [f"artifact-{suffix}"]),
                  task(f"evaluate-{suffix}", [f"artifact-{suffix}", "criterion-independent" if suffix == "c"
                                             else "criterion-shared"], [f"evaluation-{suffix}"])]
    objects += [
        {"id": "criterion-shared", "type": "Criterion", "label": "Ortak açıklık ölçütü",
         "properties": {"definition": "Kısa ve anlaşılır anlatım", "version": 1}},
        {"id": "criterion-independent", "type": "Criterion", "label": "Bağımsız kod ölçütü",
         "properties": {"definition": "Kod örneği çalışıyor", "version": 1}},
        {"id": "comparison-ab", "type": "Comparison", "label": "İki kurmaca deneyi karşılaştır",
         "properties": {"conclusion": "A ile B aynı puanda"}},
    ]
    links += [link("includes-a", "comparison-ab", "includes", "evaluation-a"),
              link("includes-b", "comparison-ab", "includes", "evaluation-b"),
              link("display-related", "experiment-a", "related", "experiment-c")]
    tasks += [task("compare-ab", ["evaluation-a", "evaluation-b"], ["comparison-ab"])]
    return {"schema_version": 3, "revision": 0,
            "project": {"id": "synthetic-notebook", "name": "Kurmaca deney defteri",
                        "goal": "İki deneyi ortak ölçüte göre karşılaştırmak.", "audience": "Test okuyucusu",
                        "scope": ["Kurmaca yerel deneyler"], "out_of_scope": ["Gerçek performans iddiası"],
                        "constraints": [], "open_questions": []},
            "ontology": {"object_types": types, "relation_types": relations},
            "objects": objects, "relations": links, "tasks": tasks, "decisions": [], "history": []}


def write_notebook_files(root):
    root = Path(root)
    for suffix in ("a", "b", "c"):
        (root / f"çıktı-{suffix}.txt").write_text(f"Kurmaca çıktı {suffix}.\n", encoding="utf-8")
    for identifier in ["run-a", "evaluate-a", "run-b", "evaluate-b", "run-c", "evaluate-c", "compare-ab"]:
        (root / f"inceleme-{identifier}.md").write_text(
            f"Kurmaca test incelemesi: {identifier}.\n", encoding="utf-8")


def evidence(identifier):
    return [{"path": f"inceleme-{identifier}.md", "criterion": 0,
             "note": "Kurmaca örneğin girdileri ve sonucu karşılaştırıldı.", "reviewer": "test-reviewer"}]


def event(action, **fields):
    return {"action": action, "actor": "test-agent", "reason": "Kurmaca alan davranışını doğrula.", **fields}
