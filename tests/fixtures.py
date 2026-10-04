"""Small synthetic project shared by tests and the optional demo."""
import json
from pathlib import Path


def build_spec():
    def task(identifier, title, dependencies, criteria):
        return {
            "id": identifier, "title": title, "status": "todo",
            "object_ids": ["O-CATALOG"], "depends_on": dependencies,
            "decision_ids": ["D-LOCAL"], "acceptance": criteria, "evidence": [],
        }
    return {
        "schema_version": 1, "revision": 0,
        "project": {
            "id": "synthetic-tools", "name": "Sentetik AI araç karşılaştırması",
            "goal": "Üç kurmaca aracı kaynaklarına bağlı biçimde karşılaştırmak.",
            "audience": "AI kullanıcıları", "scope": ["Üç sentetik araç", "Türkçe karşılaştırma"],
            "out_of_scope": ["Canlı fiyat", "Gerçek performans iddiası", "Veritabanı"],
            "constraints": ["Veriler yerel JSON dosyasında tutulur."],
            "open_questions": ["Görsel tasarım henüz belirlenmedi."],
        },
        "objects": [
            {"id": "O-CATALOG", "type": "Catalog", "label": "Sentetik araç kataloğu", "properties": {}},
            {"id": "O-REPORT", "type": "Report", "label": "Karşılaştırma metni", "properties": {}},
        ],
        "relations": [{"from": "O-REPORT", "type": "uses", "to": "O-CATALOG"}],
        "tasks": [
            task("T-DATA", "Sentetik veriyi hazırla", [], ["Üç araç bulunur.", "Verinin sentetik olduğu açıkça belirtilir."]),
            task("T-COMPARE", "Karşılaştırma metnini oluştur", ["T-DATA"], ["Metin veri dosyasındaki üç aracı kapsar."]),
            task("T-CHECK", "Karşılaştırmayı kontrol et", ["T-COMPARE"], ["Canlı fiyat ve gerçek performans iddiası yoktur."]),
        ],
        "decisions": [
            {"id": "D-LOCAL", "topic": "storage", "statement": "İlk sürümde yerel JSON kullanılır.",
             "status": "accepted", "rationale": "Küçük sentetik örnek için yeterli.",
             "source": "Sentetik test brief'i; gerçek kullanıcı kabulü değildir.",
             "supersedes": None, "accepted_by": "test-author"},
            {"id": "D-DB", "topic": "storage", "statement": "İleride veritabanına geçilebilir.",
             "status": "proposed", "rationale": "Veri büyürse değerlendirilecek seçenek.",
             "source": "Sentetik ajan önerisi.", "supersedes": "D-LOCAL", "accepted_by": None},
        ],
        "history": [],
    }


def write_artifacts(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "tools.json").write_text(json.dumps({"synthetic": True, "tools": [
        {"name": "Kurmaca Alfa", "category": "metin"},
        {"name": "Kurmaca Beta", "category": "görsel"},
        {"name": "Kurmaca Gama", "category": "kod"},
    ]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (root / "comparison.md").write_text(
        "# Sentetik karşılaştırma\n\nKurmaca Alfa: metin. Kurmaca Beta: görsel. Kurmaca Gama: kod.\n"
        "Bu örnek gerçek performans veya canlı fiyat iddiası içermez.\n", encoding="utf-8")
    (root / "review.md").write_text(
        "Sentetik inceleme beyanı: karşılaştırmada canlı fiyat ve gerçek performans iddiası yok.\n",
        encoding="utf-8")


def event(action, **fields):
    return {"action": action, "actor": "test-agent", "reason": "Sentetik test işlemi.", **fields}


def evidence_items(task_id):
    mapping = {"T-DATA": ("tools.json", 2), "T-COMPARE": ("comparison.md", 1), "T-CHECK": ("review.md", 1)}
    path, count = mapping[task_id]
    return [{"path": path, "criterion": index, "note": "Sentetik ölçüt inceleme beyanı.", "reviewer": "test-agent"}
            for index in range(count)]
