Dommer B Test Set v1.3
Generated: 2026-05-25
Purpose: Validation fasit for Atul after Dommer B revision implementation.
v1.1: Aligns consequence expectations with Dommer B prompt v8. Case 9 konsekvens changed from TECHNICAL_DEVELOPMENT_AS_KONSEKVENS to CORRECT; Case 7 annotation updated to v8 rationale.
v1.2: Internal consistency fixes — Case 7 has_errors corrected true to false (all four fields are CORRECT, no tgiu findings); summary table row 9 aligned to detailed case 9 (5 points / MISSING (anbefalt_tiltak)); stale parenthetical removed from summary table row 7; test case count corrected 7 to 9.
v1.3: Case 8 fasit corrected per ARKAT ruling — risiko CORRECT to MISSING (risiko) (extracted_fields.risiko empty and raw_point_text contains no forward-looking risiko statement); konsekvens CORRECT to WRONG / TILTAK_AS_KONSEKVENS (combined "Konsekvens/Anbefalte tiltak" field is tiltak content, not a følge); has_errors false to true; case 8 deductions 0 to 7; test set total 30 to 37. Cases 4, 6, 9 risiko mismatches reviewed against the pipeline LLM run — fasit confirmed CORRECT (LLM over-fired risiko error types, no fasit change). Case 9 annotation's contrast-with-case-8 paragraph rewritten to match the corrected case 8 fasit; case 4 konsekvens explanation reworded to the v8 følge-definition (functional loss / damage / reduced lifespan / investigation need / cost risk).
Overview
This test set contains 9 test cases covering all major branches of the Dommer B evaluation logic. Each test case provides:
Input: Exact JSON structure that should be passed to Dommer B
Expected output: The fasit that Dommer B's output must match
Rationale: Why the classification was chosen
Deduction total: Points that Dommer A should calculate from the output via mapping
After Atul implements the revision, he runs Dommer B against these 9 inputs and verifies output matches the fasit.
Principles established during test set construction
TGIU-begrunnelse kreves i selve punktet. `report_context` kan ikke redde et dårlig formulert punkt.
`report_context` kan trigge moisture_flag-indikatorer (risikovurdering av konstruksjonen), men ikke redde punkt-formulering.
TG2 kan inneholde inspeksjonsbegrensninger uten å være feil, så lenge det er reelt observasjonsgrunnlag.
"Fra byggeår" skal ikke gi trekk i TG2/TG3-ARKAT — regelen gjelder kun TGIU/moisture_flag.
I compressed_mixed vurderes helheten — ett felt kan dekke flere ARKAT-komponenter semantisk.
Et punkt kan være teknisk godt skrevet og likevel strukturelt feil — Dommer B belønner ikke bare tekstmengde.
Et punkt kan ha sterk risiko og sterke tiltak, men fortsatt mangle konsekvens.
Beskrivelse av hva en utbedring innebærer er IKKE det samme som en anbefaling om tiltak.
Tiltakskrav ved TG2 er versjonsavhengig: Under NS 3600:2018 er tiltak ikke påkrevd ved TG2 (forskrift § 2-22). Under NS 3600:2025 er tiltak påkrevd også ved TG2 (punkt 13).
Vage tiltak er faglig akseptable ved TG2, uansett NS-versjon. TILTAK_VAGUE_WITHOUT_NECESSITY fyrer kun ved TG3.
Test case summary
#	Report	Point	Mode	Format	NS version	Deductions	Primary finding
1	Halden	5.1 Loft	TGIU	unlabeled_prose	2018	11	Three TGIU error types
2	Halden	2.1 Yttervegger	TG3	unlabeled_prose	2018	0	All correct (konsekvens weak)
3	Halden	1.1 Byggegrunn	TG2	unlabeled_prose	2018	0	All correct (konsekvens weak)
4	Halden	1.2 Krypekjeller	TG2	unlabeled_prose	2018	9	MISSING (konsekvens)
5	Halden	7.2.2 Vaskerom gulv	TG3	unlabeled_prose	2018	5	MISSING (anbefalt_tiltak)
6	Fredrikstad	Nedløp og beslag	TG3	compressed_mixed	2018	0	All correct
7	Fredrikstad	Veggkonstruksjon	TG2	compressed_mixed	2018	0	All correct
8	Synthetic (2025)	Etasjeskille/gulv	TG2	structured_arkat	2025	7	MISSING (risiko) + TILTAK_AS_KONSEKVENS in combined field
9	Synthetic (2025)	Generic TG2	TG2	structured_arkat	2025	5	MISSING (anbefalt_tiltak) under 2025 rules
Total deductions across test set: 37 points
Rule coverage
Rule	Test case
TGIU mode selection	1
TG2 mode selection	3, 4, 7, 8, 9
TG3 mode selection	2, 5, 6
TGIU_MISSING_REASON	1
TGIU_MISSING_FURTHER_INVESTIGATION	1
TGIU_MISSING_MOISTURE_FLAG (positive)	1
Moisture_flag negative test (not fired on TG2)	4
Kategori A Steg A short-circuit	4 (krypkjeller, but TG2 so stopped at mode select)
"Fra byggeår" handling	3
MISSING (konsekvens)	4
MISSING (anbefalt_tiltak) at TG3	5
MISSING (anbefalt_tiltak) at TG2 + NS3600:2025	9
NOT_APPLICABLE at TG2 + NS3600:2018 with vague tiltak	3, 4, 7
Vague tiltak acceptable at TG2 + NS3600:2025	8
TG2 anbefalt_tiltak as NOT_APPLICABLE (NS3600:2018 absent)	3, 4, 7 (implicit — no MISSING fired)
Konsekvens minimum threshold	2, 3 (accepted), 4 (rejected)
Compressed_mixed semantic extraction	6, 7
Structured_arkat with combined "Konsekvens/Anbefalte tiltak" label	8, 9
Risiko embedded in Konsekvens/tiltak field	6
Kostnadsklasse as konsekvens coverage	6
MISSING (risiko)	8
TILTAK_AS_KONSEKVENS	8
NS version selection	1-7 (2018), 8-9 (2025)
Test cases
Test case 1 — Halden 5.1 Loft (TGIU)
Input:
```json
{
  "point_id": "5.1",
  "point_label": "Loft (konstruksjonsoppbygging)",
  "tg_grade": "TGIU",
  "report_format": "unlabeled_prose",
  "ns_version": "NS3600:2018",
  "raw_point_text": "Loftkonstruksjonen er lukket, ingen tilkomst for vurdering av bygningsdelen.",
  "extracted_fields": {
    "aarsak": "",
    "risiko": "",
    "konsekvens": "",
    "anbefalt_tiltak": ""
  },
  "report_context": {
    "building_year": 1969,
    "dwelling_type": "enebolig",
    "building_method_summary": "Boligen er opprinnelig oppført i 1969, fundamentert med støpt gulv mot grunn og grunnmur/yttervegger av betong. Bindingsverkskonstruksjon i tre, utvendig kledd med stående og liggende trekledning. Takkonstruksjonen er utført som saltak, antatt med plassbygde sperrer i tre, og yttertaket er tekket med takshingel på undertak av rupanel.",
    "relevant_component_context": "Saltak tekket med takshingel. Inspeksjonsluke i himling mellom soverom og gang var skrudd fast på befaringsdagen — ingen tilkomst til loftet. Undertaket antas å være av rupanel, alder ukjent. I relaterte punkter: 'grunnet manglende tilkomst kan ikke ventilering/lufting påvises å være tilstrekkelig'; 'undertak, taktekking og tilhørende deler er av ukjent alder, over halvparten av forventet bruks- og levetid er forbigått'. Bolig oppført 1969."
  }
}
```
Expected output:
```json
{
  "point_id": "5.1",
  "tg_grade": "TGIU",
  "field_results": {
    "aarsak": { "status": "NOT_APPLICABLE", "error_type": null, "explanation": "" },
    "risiko": { "status": "NOT_APPLICABLE", "error_type": null, "explanation": "" },
    "konsekvens": { "status": "NOT_APPLICABLE", "error_type": null, "explanation": "" },
    "anbefalt_tiltak": { "status": "NOT_APPLICABLE", "error_type": null, "explanation": "" }
  },
  "tgiu_findings": {
    "findings": [
      {
        "error_type": "TGIU_MISSING_REASON",
        "explanation": "Punktet oppgir kun at konstruksjonen er lukket uten å forklare hvorfor inspeksjon ikke var mulig."
      },
      {
        "error_type": "TGIU_MISSING_FURTHER_INVESTIGATION",
        "explanation": "Ingen anbefaling om ytterligere undersøkelser er gitt."
      },
      {
        "error_type": "TGIU_MISSING_MOISTURE_FLAG",
        "explanation": "Loftet er en kategori B-konstruksjon, og rapportkonteksten viser sterk risikofaktor i form av ukjent alder på undertak/taktekking; punktet formidler likevel ikke eksplisitt fuktrisiko ved manglende inspeksjon."
      }
    ]
  },
  "has_errors": true
}
```
Deductions: TGIU_MISSING_REASON (4) + TGIU_MISSING_FURTHER_INVESTIGATION (4) + TGIU_MISSING_MOISTURE_FLAG (3) = 11 points
Test case 2 — Halden 2.1 Yttervegger (TG3)
Input:
```json
{
  "point_id": "2.1",
  "point_label": "Yttervegger",
  "tg_grade": "TG3",
  "report_format": "unlabeled_prose",
  "ns_version": "NS3600:2018",
  "raw_point_text": "Det er påvist avvik på vannbord over og under vindu, eller i overgangen mellom grunnmur og fasade og i etasjeskillere. Det er påvist skader, sprekker og råteskade på kledningen. Det er ikke påvist tilstrekkelig lufting for kledningen. Det er utført stikktaking på typiske skadesteder, slik som i nedkanten av panelet og i områdene rundt vinduene. Det er observert materialvalg ved yttervegg som kan gi forkortet levetid. Yttervegger oppført med bindingsverk av tre, som utvendig er kledd med stående og liggende malt trepanel fra byggeår. Mengde isolasjon og tilstand på vindsperre kan ikke verifiseres med sikkerhet uten fysiske inngrep. Merknader: Tilstrekkelig lufting kan ikke konstateres uten fysiske inngrep, det observeres at ytterkledning og utlekting ligger tett uten luftespalte. Ved manglende lufting vil det kunne oppstå fukt- og råteskader da konstruksjonen ikke blir tilstrekkelig tørket ut. Ytterkledning bærer preg av alder med etterslepet vedlikehold, det må kunne forventes vedlikehold og utskiftninger i nær tid. Ved manglende vedlikehold av kledning vil trevirke være mer sårbar for at fuktighet trekker seg inn som vil kunne føre til fukt- og råteskader over tid. Det er ufagmessige tilpasninger rundt vinduer- og dører som vil gjøre konstruksjon og vindu sårbar for fuktpåkjenninger, eventuelle utskiftninger og hyppigere vedlikehold må påregnes. Det er utført stikktaking på fukt- og værutsatte områder, det påvises tørkesprekker og fuktskader enkelte steder. Vedlikehold og utskiftninger må kunne forventes. Konstruksjonen er oppført etter eldre byggemåte som avviker fra dagens standard til isolasjonsmengde og tetthet. Slike konstruksjoner vil kunne kreve hyppigere vedlikehold og utskiftninger. TG3 settes på bakgrunn av påviste fuktskader, vesentlig vedlikeholdsetterslep og ufagmessige løsninger som medfører høy risiko for videre skadeutvikling. Det må påregnes tiltak, herunder utskiftning av kledning, utbedring av detaljer rundt vinduer og dører, samt kontroll og eventuell utbedring av underliggende konstruksjon. Utbedringskostnaden vurderes som middels, jf. rapportens sjablonmessige kostnadsklasser.",
  "extracted_fields": {
    "aarsak": "",
    "risiko": "",
    "konsekvens": "",
    "anbefalt_tiltak": ""
  },
  "report_context": {
    "building_year": 1969,
    "dwelling_type": "enebolig",
    "building_method_summary": "Bindingsverkskonstruksjon i tre, utvendig kledd med stående og liggende trekledning. Bolig oppført 1969.",
    "relevant_component_context": "Yttervegger oppført i bindingsverk av tre, utvendig kledd med stående og liggende malt trepanel fra byggeår (1969). Kledning minimum 57 år gammel ved befaringstidspunkt. Påvist råteskader og fuktskader ved stikktaking. Ingen luftespalte mellom kledning og utlekting."
  }
}
```
Expected output:
```json
{
  "point_id": "2.1",
  "tg_grade": "TG3",
  "field_results": {
    "aarsak": { "status": "CORRECT", "error_type": null, "explanation": "" },
    "risiko": { "status": "CORRECT", "error_type": null, "explanation": "" },
    "konsekvens": { "status": "CORRECT", "error_type": null, "explanation": "" },
    "anbefalt_tiltak": { "status": "CORRECT", "error_type": null, "explanation": "" }
  },
  "tgiu_findings": { "findings": [] },
  "has_errors": false
}
```
Deductions: 0 points
Annotation: Konsekvens is at minimum threshold ("vedlikehold og utskiftninger må påregnes") but passes. Near the floor of what Dommer B accepts.
Test case 3 — Halden 1.1 Byggegrunn (TG2)
Input:
```json
{
  "point_id": "1.1",
  "point_label": "Byggegrunn, fundamenter, grunnmur, drenering og sikring mot vann og fuktighet",
  "tg_grade": "TG2",
  "report_format": "unlabeled_prose",
  "ns_version": "NS3600:2018",
  "raw_point_text": "Byggegrunn er ikke kjent. Det er påvist synlige skader og/eller skjevheter på fundamentet. Det er påvist riss, sprekker eller skader på grunnmuren. Det er påvist skader slik at drenering og annen sikring mot vann og fuktighet er utett. Støpt gulv mot grunn i underetasjen med grunnmur/yttervegger av betong som utvendig er pusset og malt, etasjeskille i trekonstruksjon. Drenering med synlig knotteplast på garasjen og bølget eternittplater på eldre del. Takvann ledet i eget avløpssystem/tomtens terreng. Fundamentering er ikke vurdert da den ligger under bakkenivå. Det er ikke foretatt geotekniske undersøkelser i forbindelse med utarbeidelse av denne rapporten. Merknader: Det er påvist riss/sprekker enkelte steder på grunnmur, tiltak ved reparasjoner og vedlikehold må regnes med. Større sprekker bør holdes ved tilsyn ved eventuelle utvidelser. Riss og sprekker kan være forårsaket av flere forhold som f.eks belastninger, setninger eller temperaturvariasjoner. Drenering antas å være fra byggeår. Av naturlig årsaker er kontroll av drenering og drenerende masser begrenset. Knotteplast er ikke tilstrekkelig klemt med topplist. Manglende topplist kan medføre at vann trenger inn mellom plasten og grunnmuren, noe som igjen kan gi økt risiko for fuktbelastning i underliggende rom mot terreng. Det anbefales å montere topplist for å sikre korrekt funksjon. Erfaringsmessig kan drenering med denne alder ha nedsatt funksjon. Dette kan i tilfellet føre til fuktskader i rom som ligger under terreng. Forventet levetid er 20 til 60 år. Dersom dreneringen nærmer seg en alder på 40 år, vil dermed drenssvikt kunne være forventbart og utskiftning må kunne regnes med.",
  "extracted_fields": {
    "aarsak": "",
    "risiko": "",
    "konsekvens": "",
    "anbefalt_tiltak": ""
  },
  "report_context": {
    "building_year": 1969,
    "dwelling_type": "enebolig",
    "building_method_summary": "Bolig oppført 1969. Støpt gulv mot grunn i underetasjen med grunnmur/yttervegger av betong. Deler av boligen har underetasje mot terreng og kryprom under deler av bygningen.",
    "relevant_component_context": "Grunnmur og drenering i bolig fra 1969. Drenering antatt fra byggeår (ca. 57 år ved befaring). Knotteplast på garasjen, eternittplater på eldre del. Påvist riss/sprekker på grunnmur. Takvann ledet i eget avløpssystem. Boligen har underetasje og krypkjeller under deler av bygningen."
  }
}
```
Expected output:
```json
{
  "point_id": "1.1",
  "tg_grade": "TG2",
  "field_results": {
    "aarsak": { "status": "CORRECT", "error_type": null, "explanation": "" },
    "risiko": { "status": "CORRECT", "error_type": null, "explanation": "" },
    "konsekvens": { "status": "CORRECT", "error_type": null, "explanation": "" },
    "anbefalt_tiltak": { "status": "CORRECT", "error_type": null, "explanation": "" }
  },
  "tgiu_findings": { "findings": [] },
  "has_errors": false
}
```
Deductions: 0 points
Annotation: Tests "fra byggeår" regel (drenering antas å være fra byggeår) — skal IKKE gi trekk i TG2-ARKAT. Konsekvens minimum ("må regnes med"). Inspeksjonsbegrensning ("Av naturlig årsaker er kontroll...begrenset") er akseptabel i TG2 med reelt observasjonsgrunnlag.
Test case 4 — Halden 1.2 Krypekjeller (TG2)
Input:
```json
{
  "point_id": "1.2",
  "point_label": "Krypekjeller",
  "tg_grade": "TG2",
  "report_format": "unlabeled_prose",
  "ns_version": "NS3600:2018",
  "raw_point_text": "Det er utført stikktaking i treverket. Luftgjennomstrømning og luftfuktighet, herunder fuktsperre mot grunn, høyde i rommet og ventiler mot yttervegg er vurdert som ikke tilfredsstillende. Boligen har kryprom under deler boligen, via tilkomst fra luke fra kjellerstue. I kryprom er det yttervegger av betong, etasjeskille i tre og synlig fjell mot grunn med oppfylte masser. Naturlig ventilering via lufteventil i grunnmur. Merknader: Det registreres saltutslag på yttervegger. Saltutslag dannes når fuktighet trenger gjennom murkonstruksjoner og transporterer saltmineraler ut til overflaten. Saltutslag skjer som oftest ved manglende eller sviktende drenering på utside. Tilstand gjeldende drenering er kommentert i pkt. 1.1. Fuktighet i krypkjeller med terreng bestående av fjell kan skyldes fordampning fra grunnen, vann som trekker inn fra terrenget, eller for lite lufting som ikke klarer å fjerne den oppbyggede fukten. Tiltak ved f.eks å forebygge fuktighet kan være å legge plastfolie over bakken for å stoppe fordampning, god ventilering eller installere en avfukter. Tomtens form og terrengforhold vil kunne gjøre rom som ligger under terreng sårbar for ytre fuktpåkjenninger. Det tolkes ut ifra terrengforhold at overvann/regnvann vil påvirke rom som ligger under terreng da terreng heller mot grunnmur.",
  "extracted_fields": {
    "aarsak": "",
    "risiko": "",
    "konsekvens": "",
    "anbefalt_tiltak": ""
  },
  "report_context": {
    "building_year": 1969,
    "dwelling_type": "enebolig",
    "building_method_summary": "Bolig oppført 1969. Deler av boligen har underetasje mot terreng og kryprom under deler av bygningen.",
    "relevant_component_context": "Krypkjeller under deler av boligen, med tilkomst fra luke i kjellerstue. Observert saltutslag på yttervegger. Yttervegger av betong, etasjeskille i tre, synlig fjell mot grunn. Naturlig ventilering via lufteventil. Drenering er fra byggeår (ref. punkt 1.1). Bolig oppført 1969."
  }
}
```
Expected output:
```json
{
  "point_id": "1.2",
  "tg_grade": "TG2",
  "field_results": {
    "aarsak": { "status": "CORRECT", "error_type": null, "explanation": "" },
    "risiko": { "status": "CORRECT", "error_type": null, "explanation": "" },
    "konsekvens": {
      "status": "MISSING",
      "error_type": "MISSING (konsekvens)",
      "explanation": "Punktet beskriver årsak, risiko og mulige tiltak, men formidler ikke hvilken faktisk følge forholdet kan få for bygningsdelen eller kjøperen, som skade, råte, redusert levetid, funksjonstap, undersøkelsesbehov eller kostnadsrisiko."
    },
    "anbefalt_tiltak": { "status": "CORRECT", "error_type": null, "explanation": "" }
  },
  "tgiu_findings": { "findings": [] },
  "has_errors": true
}
```
Deductions: MISSING (konsekvens) = 9 points
Annotation: Negativ test for moisture_flag — krypkjeller er Kategori A, men TG2, ikke TGIU. Moisture_flag-logikk skal ikke engang evalueres. Viktig mode-switching test.
Test case 5 — Halden 7.2.2 Vaskerom gulv (TG3)
Input:
```json
{
  "point_id": "7.2.2",
  "point_label": "Vaskerom - u.etg, Overflate gulv",
  "tg_grade": "TG3",
  "report_format": "unlabeled_prose",
  "ns_version": "NS3600:2018",
  "raw_point_text": "Det er ikke påvist riss og sprekker. Det er påvist sprekker i fuger. Skjøter og underkant av plater på gulv er ikke inspisert. Det er påvist bom (hulrom) under fliser. Det er ikke påvist tilfredsstillende fall til sluket. Det er ikke påvist tilfredsstillende høydeforskjell fra toppen av sluket til toppen av membranen ved dørterskelen. Flislagt gulv med sluk i under utslagsvask. Fallforhold på gulv måles med krysslaser på tilgjengelige områder. Det er på tilfeldige områder kontrollert for sprekker, riss og hulrom. Merknader: Det registreres hulrom under flere fliser og det er påvist, eller det kan forventes skader som vil kunne kreve tiltak. Manglende høydeforskjell fra topp sluk til topp membran over ferdig gulv ved dør. Høydeforskjellen skal minimum være 25mm og er en avgrensning som skal hindre at bruks- og lekkasjevann ikke renner ut av våtrommet. Det måles tilnærmet flatt gulv og manglende fall rundt sluk. Bruks- og lekkasjevann vil ikke ledes tilstrekkelig mot sluk og kan bli liggende. Vann som eventuelt blir liggende vil gi slitasje på gulv og fuger, samt øke risiko for å skli på gulvet. Det måles stedvis motfall på gulv og det er fare for større vannsamlinger og tilstøtende bygningsdeler vil være utsatt dersom en lekkasje oppstår. TG3 settes da det er målt motfall på gulv mot toalett. Estimert utbedringskostnad må ses i sammenheng med punkt 7.2.3 Membran, da utbedring av fall normalt krever utskifting av membran og tilhørende overflater. Utbedringskostnaden vurderes som lav, jf. rapportens sjablonmessige kostnadsklasser.",
  "extracted_fields": {
    "aarsak": "",
    "risiko": "",
    "konsekvens": "",
    "anbefalt_tiltak": ""
  },
  "report_context": {
    "building_year": 1969,
    "dwelling_type": "enebolig",
    "building_method_summary": "Bolig oppført 1969. Vaskerom i underetasjen.",
    "relevant_component_context": "Vaskerom i underetasjen med flislagt gulv. Sluk plassert under utslagsvask. Påvist motfall på gulv mot toalett, manglende høydeforskjell fra topp sluk til topp membran ved dørterskel, og hulrom under flere fliser. Kostnadsklasse: lav."
  }
}
```
Expected output:
```json
{
  "point_id": "7.2.2",
  "tg_grade": "TG3",
  "field_results": {
    "aarsak": { "status": "CORRECT", "error_type": null, "explanation": "" },
    "risiko": { "status": "CORRECT", "error_type": null, "explanation": "" },
    "konsekvens": { "status": "CORRECT", "error_type": null, "explanation": "" },
    "anbefalt_tiltak": {
      "status": "CORRECT",
      "error_type": null,
      "explanation": ""
    }
  },
  "tgiu_findings": { "findings": [] },
  "has_errors": false
}
```
Deductions: MISSING (anbefalt_tiltak) at TG3 = 5 points
Annotation: Tester distinksjon mellom aktiv anbefaling og passiv kostnads-/omfangsbeskrivelse. "Utbedring av fall normalt krever utskifting av membran" er beskrivelse, ikke anbefaling. Konsekvens er det sterkeste i Halden (sikkerhet: sklirisiko + kostnad: lav + kryssreferanse). Viser at Dommer B kan skille aktiv fra passiv formulering.
Test case 6 — Fredrikstad Nedløp og beslag (TG3)
Input:
```json
{
  "point_id": "nedlop-og-beslag",
  "point_label": "Utvendig > Nedløp og beslag",
  "tg_grade": "TG3",
  "report_format": "compressed_mixed",
  "ns_version": "NS3600:2018",
  "raw_point_text": "Takrenner, nedløp og beslag av stål. Vurdering av avvik: Det mangler snøfangere på hele eller deler av taket, noe som var krav på byggemeldingstidspunktet. Mer enn halvparten av forventet brukstid er passert på renner/nedløp/beslag. Det er avvik: Det er flere områder med lekkasje fra skjøter i takrenner. Det er registrert flassing der hvor beslag er malt. Ufagmessige beslag på taktekke mot vegg ved garasjen og stålplater mot vegg mot øst. Det var på oppføringstidspunktet krav til snøfangere der hvor folk ferdes. Det er ikke montert snøfangere ved inngangen til boligen. Konsekvens/tiltak: Det må monteres snøfangere for å oppfylle byggeårets krav. Tiltak: Lekkasje i takrenner bør utbedres, og alle takrenner og beslag bør skiftes samtidig med taktekket for å sikre god funksjon og levetid. Manglende snøfangere ved inngang og andre områder der folk ferdes bør utbedres for å redusere risiko for personskade ved snø- og isras. Ufagmessige beslag på taktekke mot vegg og stålplater mot vegg mot øst bør utbedres for å hindre vanninntrenging og påfølgende fuktskader. Kostnadsestimat: 20 000 - 100 000",
  "extracted_fields": {
    "aarsak": "",
    "risiko": "",
    "konsekvens": "",
    "anbefalt_tiltak": ""
  },
  "report_context": {
    "building_year": 1978,
    "dwelling_type": "enebolig",
    "building_method_summary": "Enebolig oppført 1978. Takkonstruksjon med W-takstoler i tre, undertak av sutaksplater. Taktekking av betongtakstein, takfornying utført i 2017. Tilbygg 1998. Takrenner, nedløp og beslag av stål.",
    "relevant_component_context": "Takrenner, nedløp og beslag av stål. Hovedtaket er originalt fra 1978, takfornying i 2017. Mer enn halvparten av forventet brukstid er passert. Kostnadsklasse: Tiltak mellom 20 000-100 000 kr."
  }
}
```
Expected output:
```json
{
  "point_id": "nedlop-og-beslag",
  "tg_grade": "TG3",
  "field_results": {
    "aarsak": { "status": "CORRECT", "error_type": null, "explanation": "" },
    "risiko": { "status": "CORRECT", "error_type": null, "explanation": "" },
    "konsekvens": { "status": "CORRECT", "error_type": null, "explanation": "" },
    "anbefalt_tiltak": { "status": "CORRECT", "error_type": null, "explanation": "" }
  },
  "tgiu_findings": { "findings": [] },
  "has_errors": false
}
```
Deductions: 0 points
Annotation: Tests compressed_mixed semantic extraction. Risiko innbakt i tiltaksbegrunnelse ("for å redusere risiko for personskade", "for å hindre vanninntrenging"). Kostnadsklasse dekker konsekvens. "Det må monteres" i én setning blant ellers "bør"-formuleringer skal IKKE fyre TILTAK_IMPERATIVE_FORM — vurder helheten.
Test case 7 — Fredrikstad Veggkonstruksjon (TG2)
Input:
```json
{
  "point_id": "veggkonstruksjon",
  "point_label": "Utvendig > Veggkonstruksjon",
  "tg_grade": "TG2",
  "report_format": "compressed_mixed",
  "ns_version": "NS3600:2018",
  "raw_point_text": "Veggene har bindingsverkskonstruksjon fra byggeår. Fasade/kledning har stående bordkledning. Utvendig ble det malt for 3-4år siden. Vurdering av avvik: Det er ingen eller liten lufting i nedre kant av kledning mot grunnmur. Det er påvist spredte råteskader i bordkledningen. Det er registrert områder med råteskader i bunn av trekledning på de meste værutsatte fasadene mot sør og øst. stedvis. Stedvis områder hvor kledning har behov for vedlikehold. Det er begrenset luftespalte i bunn av trekledning. Manglende maling av forkantbord og vindskier med vedlikeholdsbehov. Konsekvens/tiltak: Råteskader i bordkledningen kan fortsette å utvikle seg både i tilliggende bordkledning og til bakenforliggende veggkonstruksjon, dersom en ikke foretar tiltak. Uten tilstrekkelig lufting bak bordkledningen kan fuktighet som trenger inn bak bordene eller gjennom veggen innenfra ikke tørke opp. Dette skaper ideelle forhold for råtesopp og muggvekst. Råteskadet trekledning bør skiftes ut for å hindre videre skade på konstruksjonen. Det bør etableres tilstrekkelig lufting i nedre kant av kledningen for å redusere risikoen for fuktskader og råteutvikling. Det anbefales å skifte kledning på hele vegger, slik at det er mulig å skifte vindsperre, utbedre lufting og evt utføre etterisolering. Vedlikehold av kledningen bør utføres for å forlenge levetiden og sikre god beskyttelse mot vær og vind.",
  "extracted_fields": {
    "aarsak": "",
    "risiko": "",
    "konsekvens": "",
    "anbefalt_tiltak": ""
  },
  "report_context": {
    "building_year": 1978,
    "dwelling_type": "enebolig",
    "building_method_summary": "Enebolig oppført 1978. Bindingsverkskonstruksjon fra byggeår. Fasade/kledning med stående bordkledning.",
    "relevant_component_context": "Bindingsverkskonstruksjon fra byggeår 1978. Stående bordkledning på fasade. Utvendig malt for 3-4 år siden. Påvist råteskader i bordkledning, særlig på fasader mot sør og øst. Begrenset luftespalte i nedre kant. Kledning 47 år gammel ved befaring."
  }
}
```
Expected output:
```json
{
  "point_id": "veggkonstruksjon",
  "tg_grade": "TG2",
  "field_results": {
    "aarsak": { "status": "CORRECT", "error_type": null, "explanation": "" },
    "risiko": { "status": "CORRECT", "error_type": null, "explanation": "" },
    "konsekvens": {
      "status": "CORRECT",
      "error_type": null,
      "explanation": ""
    },
    "anbefalt_tiltak": { "status": "CORRECT", "error_type": null, "explanation": "" }
  },
  "tgiu_findings": { "findings": [] },
  "has_errors": false
}
```
Deductions: 0 points
Annotation: Konsekvens er CORRECT fordi teksten beskriver en faktisk skadefølge — råte- og skadeutvikling kan fortsette inn i bakenforliggende veggkonstruksjon. Etter v8 kreves ikke eksplisitt kjøper-, kostnads- eller bruksformulering når teksten beskriver en faktisk følge.
Test case 8 — Synthetic NS 3600:2025 Etasjeskille/gulv på grunn (TG2)
Purpose: Tests a structured_arkat TG2 point under NS 3600:2025 on two axes — (1) vague-but-acceptable tiltak ("kan man vurdere slike tiltak") is professionally acceptable at TG2 and anbefalt_tiltak stays CORRECT; (2) the combined "Konsekvens/Anbefalte tiltak" field carries only tiltak content, so konsekvens fires TILTAK_AS_KONSEKVENS, and no forward-looking risiko statement exists in extracted_fields.risiko or raw_point_text, so risiko fires MISSING (risiko). This test case is based on a real report excerpt where the takstmann wrote: "For å få lavere tilstandsgrad må høydeforskjeller rettes opp. Det vil imidlertid sjelden være økonomisk rasjonert som et enkeltstående tiltak i en bolig som dette. Dersom boligen en gang skal renoveres, kan man vurdere slike tiltak." This is a legitimate TG2 tiltak formulation under NS 3600:2025 because the standard allows "tiltak in nær fremtid" — not immediate action.
Input:
```json
{
  "point_id": "etasjeskille-gulv-2025",
  "point_label": "Etasjeskille og gulv på grunn",
  "tg_grade": "TG2",
  "report_format": "structured_arkat",
  "ns_version": "NS3600:2025",
  "raw_point_text": "Oppsummering av etasjeskille og gulv på grunn. Etasjeskille av tre. Toleransekrav på etasjeskille er vurdert etter NS3600. Ved nivelering av laser registreres det følgende: 15 mm høydeforskjell i stue. 18 mm høydeforskjell på kjøkken. 15 mm høydeforskjell på soverom. Høydeforskjellen er utenfor toleransekrav ved NS3600. TG2 gis med bakgrunn i standardens krav til godkjente måleavvik. Konsekvens/Anbefalte tiltak: For å få lavere tilstandsgrad må høydeforskjeller rettes opp. Det vil imidlertid sjelden være økonomisk rasjonert som et enkeltstående tiltak i en bolig som dette. Dersom boligen en gang skal renoveres, kan man vurdere slike tiltak.",
  "extracted_fields": {
    "aarsak": "Høydeforskjellen er utenfor toleransekrav ved NS3600. TG2 gis med bakgrunn i standardens krav til godkjente måleavvik.",
    "risiko": "",
    "konsekvens": "For å få lavere tilstandsgrad må høydeforskjeller rettes opp.",
    "anbefalt_tiltak": "Det vil imidlertid sjelden være økonomisk rasjonert som et enkeltstående tiltak i en bolig som dette. Dersom boligen en gang skal renoveres, kan man vurdere slike tiltak."
  },
  "report_context": {
    "building_year": 1985,
    "dwelling_type": "enebolig",
    "building_method_summary": "Enebolig oppført 1985. Etasjeskille av tre.",
    "relevant_component_context": "Etasjeskille av trebjelkelag. Nivelleringsmålinger viser høydeforskjeller 15-18mm i stue, kjøkken og soverom — utenfor NS 3600 toleransekrav. TG2 gis basert på målte avvik."
  }
}
```
Expected output:
```json
{
  "point_id": "etasjeskille-gulv-2025",
  "tg_grade": "TG2",
  "field_results": {
    "aarsak": { "status": "CORRECT", "error_type": null, "explanation": "" },
    "risiko": {
      "status": "MISSING",
      "error_type": "MISSING (risiko)",
      "explanation": "Verken extracted_fields.risiko eller raw_point_text inneholder en fremtidsrettet risikovurdering — punktet har kun observasjon/måling, TG-begrunnelse og tiltakstekst."
    },
    "konsekvens": {
      "status": "WRONG",
      "error_type": "TILTAK_AS_KONSEKVENS",
      "explanation": "Konsekvens-feltet beskriver hva som må gjøres for å få lavere tilstandsgrad (et tiltak), ikke hvilken følge høydeforskjellene har for kjøper eller bygningsdelen."
    },
    "anbefalt_tiltak": { "status": "CORRECT", "error_type": null, "explanation": "" }
  },
  "tgiu_findings": { "findings": [] },
  "has_errors": true
}
```
Deductions: MISSING (risiko) 5 + TILTAK_AS_KONSEKVENS 2 = 7 points
Annotation:
anbefalt_tiltak (CORRECT): Under NS 3600:2025 + TG2, anbefalt_tiltak IS required. Here the tiltak is present but formulated vaguely ("kan man vurdere slike tiltak"). Under strict TG3 logic this would fire TILTAK_VAGUE_WITHOUT_NECESSITY. But this is TG2, not TG3. The hard rule in the prompt states: "TILTAK_VAGUE_WITHOUT_NECESSITY fyrer KUN ved TG3". At TG2, vague tiltak formulations are professionally acceptable because TG2 allows "tiltak in nær fremtid" — not immediate action. The takstmann here correctly notes that utbedring is not economically rational as a standalone measure, but can be considered as part of future renovation. This is a realistic and faglig forsvarlig TG2 evaluation.
risiko (MISSING (risiko)): extracted_fields.risiko er tomt. raw_point_text inneholder bare observasjon/måling ("15-18 mm høydeforskjell ... utenfor toleransekrav"), en TG-begrunnelse ("TG2 gis med bakgrunn i standardens krav til godkjente måleavvik") og tiltakstekst. Ingen setning beskriver hva høydeforskjellen kan føre til videre — det finnes ingen fremtidsrettet risikovurdering. Dommer B skal ikke redde et tomt risiko-felt med faglig utfylling fra egen modellforståelse; en fraværende risikosetning er MISSING (risiko), ikke CORRECT. Dette skiller seg fra test case 6, der fremtidsrettet risikoinnhold ("for å hindre vanninntrenging og påfølgende fuktskader") faktisk finnes, om enn innbakt i tiltakssetninger.
konsekvens (WRONG / TILTAK_AS_KONSEKVENS): Det kombinerte "Konsekvens/Anbefalte tiltak"-feltet sier "For å få lavere tilstandsgrad må høydeforskjeller rettes opp" pluss den økonomiske rasjonaliteten ved tiltaket. Dette beskriver hva som må gjøres, ikke hvilken følge høydeforskjellen har for kjøper eller bygningsdelen. Tiltaksinnhold i et konsekvens-felt utløser TILTAK_AS_KONSEKVENS. anbefalt_tiltak forblir CORRECT — samme tiltakstanke er en gyldig formulering i tiltaks-feltet (se avsnittet over).
Test case 9 — Synthetic NS 3600:2025 TG2 with missing tiltak
Purpose: Tests that under NS 3600:2025, if the takstmann provides TG2 without any tiltak field at all, Dommer B fires MISSING (anbefalt_tiltak) — even at TG2. This validates the core version-dependent rule that is the reason for this entire spec update.
Input:
```json
{
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
```
Expected output:
```json
{
  "point_id": "vinduer-2025-no-tiltak",
  "tg_grade": "TG2",
  "field_results": {
    "aarsak": { "status": "CORRECT", "error_type": null, "explanation": "" },
    "risiko": { "status": "CORRECT", "error_type": null, "explanation": "" },
    "konsekvens": { "status": "CORRECT", "error_type": null, "explanation": "" },
    "anbefalt_tiltak": {
      "status": "MISSING",
      "error_type": "MISSING (anbefalt_tiltak)",
      "explanation": "Anbefalt tiltak mangler. Under NS 3600:2025 er tiltak påkrevd også ved TG2 per punkt 13."
    }
  },
  "tgiu_findings": { "findings": [] },
  "has_errors": true
}
```
Deductions: MISSING (anbefalt_tiltak) under NS3600:2025 TG2 = 5 points
Annotation — why konsekvens is CORRECT and tiltak fires MISSING:
Two separate fields in this testcase:
Konsekvens is CORRECT: The text "Vinduer som har passert 20 år kan føre til redusert energieffektivitet" describes an actual følge — functional loss through reduced energy efficiency. Per the v8 threshold, a konsekvens does not need to explicitly state cost, buyer obligation, or use impact when it describes a real consequence such as functional loss, damage, reduced lifespan, or reduced performance. A more buyer-oriented formulation ("dette kan gi varmetap, dårligere komfort og økte energikostnader") would be stronger, but the threshold for CORRECT is whether an actual følge is described — not whether the wording is optimal.
Anbefalt_tiltak is MISSING under NS3600:2025 TG2: Under NS 3600:2018, the same point with missing tiltak would have status NOT_APPLICABLE and produce 0 points. But under NS 3600:2025, tiltak is required at TG2, so MISSING (anbefalt_tiltak) fires.
Contrast with test case 8 (Etasjeskille): TC8 is different because the combined "Konsekvens/Anbefalte tiltak" field contains only tiltak content — what must be done to lower the condition grade, whether it is economically rational as a standalone measure, and that it can be considered during future renovation. It does not describe a consequence/følge of the height differences. Therefore TC8 konsekvens is WRONG / TILTAK_AS_KONSEKVENS, while TC9 konsekvens is CORRECT because "redusert energieffektivitet" describes an actual functional loss.
Critical verification for scoring layer: Dommer A's scoring layer must respect the version-dependent applies_to rule in the mapping file. If the pipeline incorrectly passes NS3600:2018 for this report, the scoring layer would (correctly) ignore the MISSING (anbefalt_tiltak) error type at TG2 — even if Dommer B had (incorrectly) emitted it. The two-layer defense: Dommer B should not emit MISSING at TG2 under NS3600:2018 in the first place (the prompt hard rule), but if it does by mistake, Dommer A's applies_to lookup would catch it. This is defensive design.
Contrast with test case 3 (Halden 1.1 TG2 under NS3600:2018): Halden 1.1 had anbefalt_tiltak present ("Det anbefales å montere topplist..."), which is why it passed as CORRECT. If Halden 1.1 had been on a NS 3600:2025 report and the takstmann had NOT written any tiltak, MISSING would have fired — even though the takstmann technically met the NS 3600:2018 requirement by omitting tiltak. This is the core behavioral change introduced by NS 3600:2025.
Validation procedure for Atul
After implementing the revision:
For each test case, construct the input JSON exactly as specified
Call Dommer B with the input
Compare output to the expected output
Differences are regressions — investigate and fix before proceeding
If all 9 test cases pass, Dommer B meets the revised specification.
