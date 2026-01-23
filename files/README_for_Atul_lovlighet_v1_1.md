# Validert lovlighetskontroll (v1.1)

Denne pakken legger til **Kategori F (Lovlighetsmangler)** slik at UI kan vise egne trekk og gate-blokkering.

## Hva er nytt i v1.1
- Alle lovlighetsregler har `category: "F"`.
- `severity: hard_stop` er ment å mappes til feedback `severity: critical` + `affects_96_gate: true` og **skal blokkere 96%-gate**.
- ARKAT-templatefelt bruker **anbefalt_tiltak** (entall) for å matche `validert_feedback_v1.2.schema.json` (`arkat_section` enum).
- `validert_category_config_v1_0.json` definerer Kategori F for UI (maks trekk 15) + gate-logikk.

## Implementasjonsnotat (mapping til feedback v1.2)
Når en lovlighetsregel trigges:
- `rule_family`: `"LEGALITY"`
- `severity` mapping:
  - `hard_stop` -> `critical`
  - `major` -> `high` eller `medium` (avhengig av praksis)
  - `minor` -> `low`
- `affects_96_gate`:
  - true når `gate_impact.blocks_96_gate == true`
- `arkat_section`:
  - bruk `"anbefalt_tiltak"` for ARKAT-forslag (samle alle ARKAT-linjer i `example_fix.good_example`)

### Kategori F i score
- `score.category_deductions` må inkludere element for `category="F"` (selv ved 0).
- Ved hard_stop: sett `gate.blocked_96 = true` og inkluder rule_id i `gate.blocked_by`.

