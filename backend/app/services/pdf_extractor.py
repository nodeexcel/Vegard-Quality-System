import pdfplumber
from typing import Optional
import logging

logger = logging.getLogger(__name__)

class PDFExtractor:
    """Extract text from PDF files using pdfplumber"""
    
    @staticmethod
    def extract_text(pdf_file) -> str:
        """
        Extract text from PDF file
        
        Args:
            pdf_file: File-like object or file path
            
        Returns:
            Extracted text as string
        """
        try:
            text_content = []
            
            with pdfplumber.open(pdf_file) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text_content.append(page_text)
            
            full_text = "\n\n".join(text_content)
            logger.info(f"Successfully extracted {len(full_text)} characters from PDF")
            return full_text
            
        except Exception as e:
            logger.error(f"Error extracting text from PDF: {str(e)}")
            raise Exception(f"Failed to extract text from PDF: {str(e)}")

