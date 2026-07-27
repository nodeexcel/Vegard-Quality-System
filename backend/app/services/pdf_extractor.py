import pdfplumber
from typing import Optional, Dict, List, Tuple
import logging
import re
try:
    from PyPDF2 import PdfReader
except Exception:  # pragma: no cover - optional fallback only
    PdfReader = None

logger = logging.getLogger(__name__)


_PDF_FOOTER_LINE_RE = re.compile(
    r"(?ix)^\s*(?:"
    r"Ingeni[øo]r\s+H[åa]vard\s+Hansen\s+AS|"
    r"Lundestadtoppen\s+2A|"
    r"Berjmannsveien\s+16C.*|"
    r"Gnr\s+\d+.*|"
    r"\d{4}\s+FREDRIKSTAD|"
    r"Oppdragsnr\.?\s*:.*|"
    r"Befaringsdato\s*:.*|"
    r"Side\s*:\s*\d+\s+av\s+\d+(?:\s+\d{1,3})?.*|"
    r"\d{1,3}\s+av\s+\d{1,3}|"
    r"\d{1,3}"
    r")\s*$"
)
_TRAILING_FOOTER_DATE_RE = re.compile(r"(?m)^\s*\d{1,2}\.\d{1,2}\.\d{4}\s*$")


def _normalize_pdf_text_artifacts(text: str) -> str:
    """Repair common broken PDF text-layer artifacts before analysis."""
    if not text:
        return ""
    glyph_replacements = {
        "琀椀": "ti",
        "琀昀": "tf",
        "昀氀": "fl",
        "昀琀": "ft",
        "琀琀": "tt",
        "昀昀": "ff",
        "昀樀": "fj",
        "琀": "t",
        "椀": "i",
        "昀": "f",
        "氀": "l",
        "樀": "j",
    }
    for old, new in glyph_replacements.items():
        text = text.replace(old, new)
    replacements = (
        (r"\blstand\b", "tilstand"),
        (r"\bLstand\b", "Tilstand"),
        (r"\blstrekkelig\b", "tilstrekkelig"),
        (r"\bLstrekkelig\b", "Tilstrekkelig"),
        (r"\bfukghet\b", "fuktighet"),
        (r"\bFukghet\b", "Fuktighet"),
        (r"\buoere\b", "utfoere"),
        (r"\bUoere\b", "Utfoere"),
        (r"\bskies\b", "skiftes"),
        (r"\bSkies\b", "Skiftes"),
        (r"\bleved\b", "levetid"),
        (r"\bLeved\b", "Levetid"),
        (r"\btltak\b", "tiltak"),
        (r"\bTltak\b", "Tiltak"),
        (r"\bltak\b", "tiltak"),
        (r"\bLtak\b", "Tiltak"),
    )
    out = text
    for pattern, replacement in replacements:
        out = re.sub(pattern, replacement, out)
    return _TRAILING_FOOTER_DATE_RE.sub("", out)


def _strip_pdf_footer_lines(text: str) -> str:
    lines = []
    for line in str(text or "").splitlines():
        cleaned = line.strip()
        if not cleaned:
            lines.append(line)
            continue
        if _PDF_FOOTER_LINE_RE.match(cleaned):
            continue
        lines.append(line)
    return "\n".join(lines).strip()

class PDFExtractor:
    """Extract text from PDF files using pdfplumber - ensures ALL pages, appendices, and images are processed"""

    @staticmethod
    def _words_to_lines(words: List[Dict[str, object]]) -> str:
        if not words:
            return ""
        ordered = sorted(words, key=lambda w: (float(w.get("top") or 0), float(w.get("x0") or 0)))
        line_groups: List[Tuple[float, List[Dict[str, object]]]] = []
        for word in ordered:
            top = float(word.get("top") or 0)
            if not line_groups or abs(top - line_groups[-1][0]) > 3.5:
                line_groups.append((top, [word]))
            else:
                line_groups[-1][1].append(word)
        lines: List[str] = []
        for _, group in line_groups:
            group = sorted(group, key=lambda w: float(w.get("x0") or 0))
            line = " ".join(str(w.get("text") or "").strip() for w in group if str(w.get("text") or "").strip())
            if line:
                lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def _detect_two_column_split(words: List[Dict[str, object]], page_width: float) -> Optional[float]:
        if len(words) < 80 or page_width <= 0:
            return None
        centers = sorted((float(w.get("x0") or 0) + float(w.get("x1") or 0)) / 2 for w in words)
        min_edge = page_width * 0.18
        max_edge = page_width * 0.82
        best_gap = 0.0
        best_split = None
        for left, right in zip(centers, centers[1:]):
            split = (left + right) / 2
            gap = right - left
            if split < min_edge or split > max_edge:
                continue
            left_count = sum(1 for center in centers if center < split)
            right_count = len(centers) - left_count
            if min(left_count, right_count) < len(centers) * 0.22:
                continue
            if gap > best_gap:
                best_gap = gap
                best_split = split
        if best_split is None or best_gap < max(20.0, page_width * 0.035):
            return None
        return best_split

    @staticmethod
    def _extract_page_text_reading_order(page) -> str:
        words = page.extract_words(
            x_tolerance=1,
            y_tolerance=3,
            keep_blank_chars=False,
            use_text_flow=False,
        )
        if not words:
            return page.extract_text() or ""
        page_width = float(getattr(page, "width", 0) or 0)
        split_x = PDFExtractor._detect_two_column_split(words, page_width)
        if split_x is None:
            text = PDFExtractor._words_to_lines(words)
        else:
            left_words = [w for w in words if (float(w.get("x0") or 0) + float(w.get("x1") or 0)) / 2 < split_x]
            right_words = [w for w in words if (float(w.get("x0") or 0) + float(w.get("x1") or 0)) / 2 >= split_x]
            text = "\n".join(
                part
                for part in (
                    PDFExtractor._words_to_lines(left_words),
                    PDFExtractor._words_to_lines(right_words),
                )
                if part.strip()
            )
        return _strip_pdf_footer_lines(_normalize_pdf_text_artifacts(text))
    
    @staticmethod
    def _validate_pdf_file(pdf_file) -> None:
        """
        Validate that the file is a valid PDF
        
        Args:
            pdf_file: File-like object or file path
            
        Raises:
            ValueError: If file is not a valid PDF
        """
        # Check if it's a file-like object
        if hasattr(pdf_file, 'read'):
            current_pos = pdf_file.tell()
            pdf_file.seek(0)
            # Read first few bytes to check magic bytes
            content = pdf_file.read(1024)
            pdf_file.seek(current_pos)  # Restore original position
            
            # Check minimum size (at least 4 bytes for %PDF header)
            if len(content) < 4:
                raise ValueError("PDF file is too small (likely corrupted or incomplete). File appears to be empty or incomplete.")
            
            # Check PDF magic bytes (%PDF)
            if not content.startswith(b'%PDF'):
                raise ValueError("File does not appear to be a valid PDF file. PDF files must start with '%PDF' header.")
        else:
            # It's a file path
            import os
            file_size = os.path.getsize(pdf_file)
            if file_size < 4:  # Less than 4 bytes (minimum for %PDF header)
                raise ValueError(f"PDF file is too small ({file_size} bytes). File appears to be corrupted or incomplete.")
            
            # Check first bytes
            with open(pdf_file, 'rb') as f:
                header = f.read(4)
                if len(header) < 4 or not header.startswith(b'%PDF'):
                    raise ValueError("File does not appear to be a valid PDF file. PDF files must start with '%PDF' header.")
    
    @staticmethod
    def extract_text(pdf_file) -> str:
        """
        Extract text from PDF file - processes ALL pages, appendices, and images
        
        Args:
            pdf_file: File-like object or file path
            
        Returns:
            Extracted text as string with metadata about pages processed
        """
        try:
            # Validate PDF file first
            PDFExtractor._validate_pdf_file(pdf_file)
            
            # Reset file position if it's a file-like object
            if hasattr(pdf_file, 'seek'):
                pdf_file.seek(0)
            text_content = []
            page_count = 0
            appendix_count = 0
            image_count = 0
            
            with pdfplumber.open(pdf_file) as pdf:
                total_pages = len(pdf.pages)
                logger.info(f"Processing PDF with {total_pages} total pages")
                
                for page_num, page in enumerate(pdf.pages, 1):
                    page_text = PDFExtractor._extract_page_text_reading_order(page)
                    
                    # Extract tables if present
                    tables = page.extract_tables()
                    table_text = ""
                    if tables:
                        for table in tables:
                            for row in table:
                                if row:
                                    table_text += " | ".join([str(cell) if cell else "" for cell in row]) + "\n"
                    table_text = _strip_pdf_footer_lines(_normalize_pdf_text_artifacts(table_text))
                    
                    # Combine page text and table text
                    combined_text = ""
                    if page_text:
                        combined_text += page_text
                    if table_text:
                        combined_text += "\n\n[TABELLDATA]\n" + table_text
                    
                    if combined_text:
                        # Add page marker for reference
                        text_content.append(f"[SIDE {page_num}]\n{combined_text}")
                        page_count += 1
                    
                    # Try to detect images (basic check - pdfplumber doesn't extract image text directly)
                    # Images would need OCR, but we note their presence
                    if hasattr(page, 'images') and page.images:
                        image_count += len(page.images)
                        text_content.append(f"\n[BILDE DETEKTERT på side {page_num} - {len(page.images)} bilde(r)]\n")
                
                # Detect appendices (pages that might be appendices - heuristic)
                # Appendices often come after main content
                # This is a simple heuristic - could be improved
                if total_pages > 20:
                    # Assume last 20% might be appendices
                    appendix_start = int(total_pages * 0.8)
                    appendix_count = total_pages - appendix_start
            
            full_text = "\n\n".join(text_content)
            
            # Add metadata header
            metadata_header = f"""
[PDF METADATA]
Totalt antall sider: {total_pages}
Sider med tekst: {page_count}
Antall bilder detektert: {image_count}
Estimert vedlegg: {appendix_count} sider
Full dokumentanalyse: {'JA' if page_count == total_pages else 'NEI'}

[START RAPPORTTEKST]
"""
            
            full_text = metadata_header + full_text
            
            logger.info(f"Successfully extracted {len(full_text)} characters from PDF: {page_count}/{total_pages} pages, {image_count} images")
            return full_text
            
        except ValueError as e:
            # Validation errors - pass through with clear message
            logger.error(f"PDF validation failed: {str(e)}")
            raise ValueError(str(e))
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error extracting text from PDF: {error_msg}")
            
            # Provide more helpful error messages
            if "No /Root object" in error_msg or "Is this really a PDF" in error_msg:
                raise ValueError("The uploaded file is not a valid PDF or is corrupted. Please ensure you're uploading a complete, uncorrupted PDF file.")
            elif "PDFSyntaxError" in str(type(e).__name__):
                raise ValueError("The PDF file appears to be corrupted or invalid. Please try uploading the file again or use a different PDF file.")
            else:
                raise ValueError(f"Failed to extract text from PDF: {error_msg}")
    
    @staticmethod
    def _metadata_creation_date_iso(pdf_file) -> str:
        if PdfReader is None:
            return ""
        try:
            reader = PdfReader(pdf_file if isinstance(pdf_file, str) else pdf_file)
            metadata = reader.metadata or {}
            creation = metadata.get("/CreationDate") or metadata.get("/ModDate") or ""
            raw = creation.get_object() if hasattr(creation, "get_object") else creation
            token = str(raw or "").strip()
            # Expected PDF date shape: D:YYYYMMDDHHmmSSZ
            match = re.search(r"D:(\d{4})(\d{2})(\d{2})", token)
            if not match:
                return ""
            return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
        except Exception:
            return ""

    @staticmethod
    def get_pdf_metadata(pdf_file) -> Dict[str, any]:
        """
        Get metadata about the PDF (page count, etc.)
        
        Args:
            pdf_file: File-like object or file path
            
        Returns:
            Dictionary with PDF metadata
        """
        try:
            # Validate PDF file first
            PDFExtractor._validate_pdf_file(pdf_file)
            
            # Reset file position if it's a file-like object
            if hasattr(pdf_file, 'seek'):
                pdf_file.seek(0)
            with pdfplumber.open(pdf_file) as pdf:
                total_pages = len(pdf.pages)
                pages_with_text = 0
                image_count = 0
                
                for page in pdf.pages:
                    if page.extract_text():
                        pages_with_text += 1
                    if hasattr(page, 'images') and page.images:
                        image_count += len(page.images)
                
                creation_date_iso = PDFExtractor._metadata_creation_date_iso(pdf_file)
                return {
                    "total_pages": total_pages,
                    "pages_with_text": pages_with_text,
                    "images_detected": image_count,
                    "full_document_available": pages_with_text == total_pages,
                    "creation_date": creation_date_iso,
                }
        except (ValueError, Exception) as e:
            logger.error(f"Error getting PDF metadata: {str(e)}")
            # Re-raise validation errors
            if isinstance(e, ValueError):
                raise
            # For other errors, return empty metadata
            return {
                "total_pages": 0,
                "pages_with_text": 0,
                "images_detected": 0,
                "full_document_available": False,
                "creation_date": "",
            }
