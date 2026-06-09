import json
from pathlib import Path

from app.services.ai_analyzer import AIAnalyzer
from app.services.pdf_extractor import PDFExtractor


def main() -> None:
    pdf_path = Path("/home/ubuntu/Quality/Vegard-Quality-System/files/E3-Horten-02.03.26.pdf")
    #pdf_path = Path("/home/ubuntu/Quality/Vegard-Quality-System/files/e3-pedersen-23.03.26.pdf")
    #pdf_path = Path("/home/ubuntu/Quality/Vegard-Quality-System/files/fremtind-fredrikstad-03.03.26 (1).pdf")
    #pdf_path = Path("/home/ubuntu/Quality/Vegard-Quality-System/files/Rapportutkast_Tilstandsrapport_20816-1116 (1).pdf")
    #pdf_path = Path("/home/ubuntu/Quality/Vegard-Quality-System/files/e3-bolavi-granberg-22.04.26.pdf")
    pdf_path = Path("/home/ubuntu/Quality/Vegard-Quality-System/files/e3-mathisen-27.03.26.pdf")
    #pdf_path = Path("/home/ubuntu/Quality/Vegard-Quality-System/files/e3rapport-joachimandrekarlsen-halden-15.04.26.pdf")
    output_path = Path("/home/ubuntu/Quality/Vegard-Quality-System/files/dommer_b_real_report_1810_full.json")

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    extracted_text = PDFExtractor.extract_text(str(pdf_path))
    pdf_metadata = PDFExtractor.get_pdf_metadata(str(pdf_path))

    analyzer = AIAnalyzer()
    _, full_analysis, detected_points_payload, scoring_result_payload = analyzer.analyze_report(
        text=extracted_text,
        report_system=None,
        building_year=None,
        pdf_metadata=pdf_metadata,
        document_title=pdf_path.name,
        document_id="1810",
        document_hash=None,
    )

    payload = {
        "meta": {
            "document_title": pdf_path.name,
            "document_id": "1810",
        },
        "dommer_b_full": full_analysis.get("arkat_semantic_pipeline", {}),
        "analysis_output": full_analysis,
        "detected_points_payload": detected_points_payload,
        "scoring_result_payload": scoring_result_payload,
    }

    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote: {output_path}")


if __name__ == "__main__":
    main()
