# BMTF binding-metadata deviation register v1.0

Status: proposed for client approval.

Parity wording: **semantic parity with declared binding-metadata deviations**.

All 20 differences are offset/length metadata on points 7.2 and 7.3. Raw text, binding text, model inputs, verdicts and final findings are unchanged.

Concrete cause: commit `a6f02a5288988e478b3bd68912810752ff12e34b`, function `_extract_explicit_arkat_subsection_binding_evidence`, introduced exact inline heading anchoring and whitespace trimming.

| JSON path | Point | Before | After | Delta |
|---|---:|---:|---:|---:|
| `$.points[1].arkat_field_binding_evidence.aarsak[0].length_chars` | 7.2 | 785 | 787 | +2 |
| `$.points[1].arkat_field_binding_evidence.aarsak[0].offset` | 7.2 | 67 | 68 | +1 |
| `$.points[1].arkat_field_binding_evidence.aarsak[1].length_chars` | 7.2 | 132 | 133 | +1 |
| `$.points[1].arkat_field_binding_evidence.aarsak[1].offset` | 7.2 | 860 | 863 | +3 |
| `$.points[1].arkat_field_binding_evidence.anbefalt_tiltak[0].length_chars` | 7.2 | 259 | 260 | +1 |
| `$.points[1].arkat_field_binding_evidence.anbefalt_tiltak[0].offset` | 7.2 | 1360 | 1366 | +6 |
| `$.points[1].arkat_field_binding_evidence.konsekvens[0].length_chars` | 7.2 | 160 | 161 | +1 |
| `$.points[1].arkat_field_binding_evidence.konsekvens[0].offset` | 7.2 | 1182 | 1187 | +5 |
| `$.points[1].arkat_field_binding_evidence.risiko[0].length_chars` | 7.2 | 168 | 169 | +1 |
| `$.points[1].arkat_field_binding_evidence.risiko[0].offset` | 7.2 | 1001 | 1005 | +4 |
| `$.points[2].arkat_field_binding_evidence.aarsak[0].length_chars` | 7.3 | 322 | 324 | +2 |
| `$.points[2].arkat_field_binding_evidence.aarsak[0].offset` | 7.3 | 69 | 70 | +1 |
| `$.points[2].arkat_field_binding_evidence.aarsak[1].length_chars` | 7.3 | 71 | 72 | +1 |
| `$.points[2].arkat_field_binding_evidence.aarsak[1].offset` | 7.3 | 399 | 402 | +3 |
| `$.points[2].arkat_field_binding_evidence.anbefalt_tiltak[0].length_chars` | 7.3 | 170 | 171 | +1 |
| `$.points[2].arkat_field_binding_evidence.anbefalt_tiltak[0].offset` | 7.3 | 750 | 756 | +6 |
| `$.points[2].arkat_field_binding_evidence.konsekvens[0].length_chars` | 7.3 | 136 | 137 | +1 |
| `$.points[2].arkat_field_binding_evidence.konsekvens[0].offset` | 7.3 | 596 | 601 | +5 |
| `$.points[2].arkat_field_binding_evidence.risiko[0].length_chars` | 7.3 | 104 | 105 | +1 |
| `$.points[2].arkat_field_binding_evidence.risiko[0].offset` | 7.3 | 479 | 483 | +4 |
