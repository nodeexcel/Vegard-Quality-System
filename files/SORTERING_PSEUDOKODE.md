# SORTERING_PSEUDOKODE – Validert punktliste

## Kortversjon
- Bygg `points_overview` fra `detected_points` (ikke fra funnrekkefølge).
- Dedupe før sort.
- Sorter numerisk hvis nok punkter har `numeric_id`, ellers sorter etter `order_in_doc` / side.

## Pseudokode

function isNumericPointId(id):
    return regexMatch(id, "^[0-9]+(\\.[0-9]+)*$")

function parseNumericId(id):
    parts = split(id, ".")
    return map(parts, toInt)

function compareNumericIds(a, b):
    arrA = parseNumericId(a)
    arrB = parseNumericId(b)
    maxLen = max(len(arrA), len(arrB))

    for i in range(0, maxLen):
        valA = (i < len(arrA)) ? arrA[i] : null
        valB = (i < len(arrB)) ? arrB[i] : null

        if valA == null and valB != null: return -1   // parent før child
        if valA != null and valB == null: return 1

        if valA < valB: return -1
        if valA > valB: return 1

    return 0

function detectSortMode(points):
    numericCount = 0
    for p in points:
        if p.numeric_id != null and isNumericPointId(p.numeric_id):
            numericCount += 1
    ratio = numericCount / len(points)
    return (ratio >= 0.7) ? "NUMERIC" : "DOCUMENT_ORDER"

function dedupe(points, dedupeKey):
    map = {}
    for p in points:
        key = (dedupeKey == "numeric_id") ? p.numeric_id : p.point_key
        if key == null: key = p.point_key

        if key not in map:
            map[key] = p
        else:
            // merge-minimum (utvid etter behov)
            map[key].finding_ids = union(map[key].finding_ids, p.finding_ids)
            map[key].tg = maxTG(map[key].tg, p.tg)

    return values(map)

function sortPoints(points):
    mode = detectSortMode(points)

    if mode == "NUMERIC":
        unique = dedupe(points, "numeric_id")
        sorted = sort(unique, (a,b) => compareNumericIds(a.numeric_id, b.numeric_id))
        return (mode, "numeric_id", sorted)
    else:
        unique = dedupe(points, "point_key")
        if allHave(unique, "order_in_doc"):
            sorted = sort(unique, by("order_in_doc"))
        else:
            sorted = sort(unique, by("page_start"))
        return (mode, "point_key", sorted)

function buildPointsOverview(detected_points, findingsByPointKey):
    (mode, dedupeKey, sortedPoints) = sortPoints(detected_points.points)

    overview = []
    idx = 1
    for p in sortedPoints:
        if p.kind not in ["point","subpoint"]: continue

        fids = findingsByPointKey.get(p.point_key, [])
        status = deriveStatus(fids)
        summary = deriveSummary(p, fids)

        overview.append({
            "display_index": idx,
            "point_key": p.point_key,
            "native_label": p.native_label,
            "numeric_id": p.numeric_id,
            "native_path": p.native_path,
            "title": p.title,
            "tg": p.tg,
            "status": status,
            "summary": summary,
            "finding_ids": fids,
            "where": {"page": p.page_start, "anchor_text": p.anchor_text}
        })
        idx += 1

    return {
        "ordering": {"mode": mode, "dedupe_key": dedupeKey, "source":"detected_points"},
        "points_overview": overview
    }
