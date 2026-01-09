# VALIDERT – Filpakke for punkt-for-punkt output (final)

## Formål
Gi presis, punkt-for-punkt feedback uansett rapportleverandør.
Systemet hardkoder aldri forventet hierarki, men leser det som faktisk finnes i opplastet rapport.

## Backend-må-krav
1) Generer og frys detected_points.json før scoring.
2) Dedupe før sort (for å fjerne 11.1/4.2/5.1/8.1-duplikater).
3) Hvis >=70% av punktene har numeric_id: sorter numerisk hierarkisk (parent før child).
4) Ellers: sorter etter order_in_doc (fallback: page_start).
5) points_overview bygges fra sortert punktliste – aldri fra funnrekkefølge.
6) Sett `ordering` i output for enkel feilsøking.
