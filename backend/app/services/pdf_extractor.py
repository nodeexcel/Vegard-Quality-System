import pdfplumber
from typing import Optional, Dict, List
import logging

logger = logging.getLogger(__name__)

class PDFExtractor:
    """Extract text from PDF files using pdfplumber - ensures ALL pages, appendices, and images are processed"""
    
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
                
                return {
                    "total_pages": total_pages,
                    "pages_with_text": pages_with_text,
                    "images_detected": image_count,
                    "full_document_available": pages_with_text == total_pages
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
                "full_document_available": False
            }

