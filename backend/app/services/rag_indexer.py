"""
RAG Indexer - Index standards documents into Pinecone vector database
"""
import os
from pinecone import Pinecone, ServerlessSpec
from openai import OpenAI
from langchain_text_splitters import RecursiveCharacterTextSplitter
from app.services.pdf_extractor import PDFExtractor
from app.config import settings
import logging
from typing import Dict
import time

logger = logging.getLogger(__name__)

class RAGIndexer:
    """Index standards documents into Pinecone vector database"""
    
    def __init__(self):
        # Initialize Pinecone
        self.pc = Pinecone(api_key=settings.PINECONE_API_KEY)
        self.index_name = settings.PINECONE_INDEX_NAME
        
        # Create or connect to index
        if self.index_name not in [idx.name for idx in self.pc.list_indexes()]:
            logger.info(f"Creating new Pinecone index: {self.index_name}")
            self.pc.create_index(
                name=self.index_name,
                dimension=1536,  # OpenAI text-embedding-3-small dimension
                metric='cosine',
                spec=ServerlessSpec(
                    cloud='aws',
                    region=settings.PINECONE_ENVIRONMENT
                )
            )
            # Wait for index to be ready
            time.sleep(10)
        
        self.index = self.pc.Index(self.index_name)
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)
        
        # Text splitter for chunking documents
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,  # ~250 tokens
            chunk_overlap=200,  # Overlap to maintain context
            length_function=len,
            separators=["\n\n", "\n", ". ", " ", ""]
        )
    
    def index_document(self, file_path: str, metadata: Dict):
        """
        Index a standards document into Pinecone
        
        Args:
            file_path: Path to PDF or text file
            metadata: Dict with 'standard' and 'category' keys
        """
        try:
            logger.info(f"Starting indexing of {metadata['standard']}")
            
            # Extract text from PDF
            if file_path.endswith('.pdf'):
                extractor = PDFExtractor()
                with open(file_path, 'rb') as f:
                    text = extractor.extract_text(f)
            else:
                with open(file_path, 'r', encoding='utf-8') as f:
                    text = f.read()
            
            logger.info(f"Extracted {len(text)} characters from {file_path}")
            
            if not text or len(text) < 100:
                raise ValueError(f"Insufficient text extracted from {file_path}")
            
            # Split into chunks
            chunks = self.text_splitter.split_text(text)
            logger.info(f"Split {metadata['standard']} into {len(chunks)} chunks")
            
            # Generate embeddings and upload in batches
            batch_size = 100
            total_uploaded = 0
            
            for i in range(0, len(chunks), batch_size):
                batch_chunks = chunks[i:i+batch_size]
                
                # Generate embeddings for batch
                try:
                    embeddings_response = self.client.embeddings.create(
                        model="text-embedding-3-small",
                        input=batch_chunks
                    )
                except Exception as e:
                    logger.error(f"Error generating embeddings: {str(e)}")
                    raise
                
                # Prepare vectors for Pinecone
                vectors = []
                for j, chunk in enumerate(batch_chunks):
                    chunk_index = i + j
                    vector_id = f"{metadata['standard'].replace(' ', '_').replace(':', '_')}-chunk-{chunk_index}"
                    embedding = embeddings_response.data[j].embedding
                    
                    vectors.append({
                        'id': vector_id,
                        'values': embedding,
                        'metadata': {
                            'text': chunk,
                            'standard': metadata['standard'],
                            'category': metadata.get('category', 'unknown'),
                            'chunk_index': chunk_index,
                            'file_name': os.path.basename(file_path)
                        }
                    })
                
                # Upload batch to Pinecone
                try:
                    self.index.upsert(vectors=vectors)
                    total_uploaded += len(vectors)
                    logger.info(f"Uploaded batch {i//batch_size + 1}/{(len(chunks)-1)//batch_size + 1} ({total_uploaded}/{len(chunks)} chunks)")
                except Exception as e:
                    logger.error(f"Error uploading to Pinecone: {str(e)}")
                    raise
                
                # Small delay to avoid rate limits
                time.sleep(0.5)
            
            logger.info(f"✅ Successfully indexed {metadata['standard']} ({total_uploaded} chunks)")
            return total_uploaded
            
        except Exception as e:
            logger.error(f"Error indexing {file_path}: {str(e)}")
            raise
    
    def get_index_stats(self):
        """Get statistics about the index"""
        try:
            stats = self.index.describe_index_stats()
            return stats
        except Exception as e:
            logger.error(f"Error getting index stats: {str(e)}")
            return None

