"""
RAG Retriever - Retrieve relevant chunks from Pinecone for analysis
"""
from pinecone import Pinecone
from openai import OpenAI
from typing import List, Dict, Optional
import logging
from app.config import settings

logger = logging.getLogger(__name__)

class RAGRetriever:
    """Retrieve relevant chunks from standards using Pinecone"""
    
    def __init__(self):
        # Initialize Pinecone
        self.pc = Pinecone(api_key=settings.PINECONE_API_KEY)
        self.index = self.pc.Index(settings.PINECONE_INDEX_NAME)
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)
    
    def retrieve_relevant_chunks(
        self, 
        query_text: str, 
        top_k: int = 5,
        filter_standard: Optional[str] = None,
        filter_category: Optional[str] = None
    ) -> List[Dict]:
        """
        Retrieve relevant chunks from standards based on query text
        
        Args:
            query_text: The report text to find relevant standards for
            top_k: Number of chunks to retrieve (default: 5)
            filter_standard: Optional filter by standard name
            filter_category: Optional filter by category
        
        Returns:
            List of dicts with 'text', 'standard', 'score', etc.
        """
        try:
            # Truncate query to avoid token limits (8K tokens = ~32K chars)
            truncated_query = query_text[:32000]
            
            # Generate embedding for query
            logger.info(f"Generating embedding for query ({len(truncated_query)} chars)")
            response = self.client.embeddings.create(
                model="text-embedding-3-small",
                input=truncated_query
            )
            query_embedding = response.data[0].embedding
            
            # Build filter if needed
            filter_dict = {}
            if filter_standard:
                filter_dict["standard"] = {"$eq": filter_standard}
            if filter_category:
                filter_dict["category"] = {"$eq": filter_category}
            
            # Query Pinecone
            logger.info(f"Querying Pinecone for top {top_k} chunks")
            results = self.index.query(
                vector=query_embedding,
                top_k=top_k,
                include_metadata=True,
                filter=filter_dict if filter_dict else None
            )
            
            # Extract and format chunks
            chunks = []
            for match in results.matches:
                chunks.append({
                    'text': match.metadata.get('text', ''),
                    'standard': match.metadata.get('standard', 'Unknown'),
                    'category': match.metadata.get('category', ''),
                    'score': match.score,
                    'chunk_index': match.metadata.get('chunk_index', 0),
                    'file_name': match.metadata.get('file_name', '')
                })
            
            logger.info(f"Retrieved {len(chunks)} relevant chunks from standards")
            
            # Log which standards were matched
            standard_counts = {}
            for chunk in chunks:
                std = chunk['standard']
                standard_counts[std] = standard_counts.get(std, 0) + 1
            
            logger.info(f"Chunks by standard: {standard_counts}")
            
            return chunks
            
        except Exception as e:
            logger.error(f"Error retrieving from Pinecone: {str(e)}")
            # Return empty list as fallback - analysis will continue without RAG
            return []
    
    def test_retrieval(self, query: str, top_k: int = 3):
        """Test retrieval with a simple query"""
        chunks = self.retrieve_relevant_chunks(query, top_k=top_k)
        
        print(f"\n=== RAG Test Query: '{query}' ===\n")
        for i, chunk in enumerate(chunks, 1):
            print(f"{i}. [{chunk['standard']}] (score: {chunk['score']:.3f})")
            print(f"   {chunk['text'][:200]}...\n")
        
        return chunks

