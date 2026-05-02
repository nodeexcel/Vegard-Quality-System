import json
from app.services.arkat_semantic_pipeline import _evaluate_arkat_point
from app.services.ai_analyzer import _normalize_tg3_cost_text

payload = {
  "point_id": "vinduer-2025-no-tiltak",
  "point_label": "Vinduer",
  "tg_grade": "TG2",
  "report_format": "structured_arkat",
  "ns_version": "NS3600:2025",
  "raw_point_text": "Oppsummering av vinduer. Vinduer med 2-lags glass hovedsaklig fra 2001. Det ble ikke registrert noen punkterte vindusglass under befaringsdagen. Vinduer vurderes å være i normal stand med hensyn til alder. TG2 settes med bakgrunn i alder (over 20år) med økt sannsynlighet for punktering og behov for vedlikehold i tiden som kommer. Konsekvens/Anbefalte tiltak: Vinduer som har passert 20 år kan føre til redusert energieffektivitet, punktering og estetiske problemer.",
  "extracted_fields": {
    "aarsak": "Vinduer med 2-lags glass hovedsaklig fra 2001. TG2 settes med bakgrunn i alder (over 20år) med økt sannsynlighet for punktering og behov for vedlikehold.",
    "risiko": "Vinduer som har passert 20 år kan føre til punktering og estetiske problemer.",
    "konsekvens": "Vinduer som har passert 20 år kan føre til redusert energieffektivitet.",
    "anbefalt_tiltak": ""
  },
  "report_context": {
    "building_year": 2001,
    "dwelling_type": "enebolig",
    "building_method_summary": "Enebolig oppført 2001. Vinduer med 2-lags glass fra byggeår.",
    "relevant_component_context": "Vinduer fra 2001, 25 år ved befaring. Ingen punkterte glass observert. Normal stand med hensyn til alder. TG2 gitt på grunnlag av alder."
  }
}

result = _evaluate_arkat_point(
    point_id=payload["point_id"],
    point_label=payload["point_label"],
    tg_grade=payload["tg_grade"],
    report_format=payload["report_format"],
    ns_version=payload["ns_version"],
    raw_point_text=payload["raw_point_text"],
    extracted_fields=payload["extracted_fields"],
    report_context=payload["report_context"],
    normalize_text=_normalize_tg3_cost_text,
    allow_llm=True,
)

print(json.dumps(result, ensure_ascii=False, indent=2))