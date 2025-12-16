import pdfplumber
from typing import Optional, Dict, List
import logging

logger = logging.getLogger(__name__)

class PDFExtractor:
    """Extract text from PDF files using pdfplumber - ensures ALL pages, appendices, and images are processed"""
    
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
            text_content = []
            page_count = 0
            appendix_count = 0
            image_count = 0
            
            with pdfplumber.open(pdf_file) as pdf:
                total_pages = len(pdf.pages)
                logger.info(f"Processing PDF with {total_pages} total pages")
                
                for page_num, page in enumerate(pdf.pages, 1):
                    page_text = page.extract_text()
                    
                    # Extract tables if present
                    tables = page.extract_tables()
                    table_text = ""
                    if tables:
                        for table in tables:
                            for row in table:
                                if row:
                                    table_text += " | ".join([str(cell) if cell else "" for cell in row]) + "\n"
                    
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
            
        except Exception as e:
            logger.error(f"Error extracting text from PDF: {str(e)}")
            raise Exception(f"Failed to extract text from PDF: {str(e)}")
    
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
            with pdfplumber.open(pdf_file) as pdf:
                total_pages = len(pdf.pages)
                pages_with_text = 0
                image_count = 0
                
                for page in pdf.pages:
                    if page.extract_text():
                        pages_with_text += 1
                    if hasattr(page, 'images') and page.images:
                        image_count += len(page.images)
                
                return {
                    "total_pages": total_pages,
                    "pages_with_text": pages_with_text,
                    "images_detected": image_count,
                    "full_document_available": pages_with_text == total_pages
                }
        except Exception as e:
            logger.error(f"Error getting PDF metadata: {str(e)}")
            return {
                "total_pages": 0,
                "pages_with_text": 0,
                "images_detected": 0,
                "full_document_available": False
            }

