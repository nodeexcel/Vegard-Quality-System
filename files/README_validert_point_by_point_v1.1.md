# VALIDERT – filpakke for punkt-for-punkt-output (v1.0/v1.1)

Dato: 2026-01-05

Denne pakken inneholder **filene dere trenger** for å få et robust “punkt-for-punkt”-output i Verifisert/Validert:

## 1) detected_points.json (v1.0)
- **Formål:** Første steg i pipelinen. Systemet skal alltid detektere alle punkter som faktisk finnes i innlastet rapport (uansett rapporttype).
- **Bruk:** Genereres én gang per dokument_hash og caches/fryses før regel-evaluering.

Filer:
- `validert_detected_points_v1.0.schema.json` (JSON Schema)
- `detected_points.example.json` (eksempel)

## 2) Feedback/Result output (v1.1)
- **Formål:** Output som alltid viser ALLE punkter (points_overview), uansett TG og uansett om det finnes funn.
- **Krav:** `points_overview[]` MUST inneholde alle punkter fra detected_points-listen.

Filer:
- `validert_feedback_v1.1.schema.json` (JSON Schema)
- `feedback.example.v1.1.json` (eksempel)

## Viktige regler (må håndheves i backend)
1. detected_points genereres først og fryses.
2. Alle findings MUST ha point_id (ingen unntak).
3. points_overview MUST inneholde alle punkt i samme rekkefølge som detected_points.
4. Aggregert tekst (f.eks. “flere punkter …”) kan vises som “top_drivers”, men aldri alene uten punktliste.

