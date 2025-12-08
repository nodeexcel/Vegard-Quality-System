"""
Bedrock RAG Retriever - Retrieve relevant chunks using Bedrock embeddings
"""
import boto3
import json
from pinecone import Pinecone
from typing import List, Dict, Optional
import logging
from app.config import settings

logger = logging.getLogger(__name__)

class BedrockRAGRetriever:
    """Retrieve relevant chunks using AWS Bedrock embeddings"""
    
    def __init__(self):
        # Initialize Bedrock
        self.bedrock_runtime = boto3.client(
            service_name='bedrock-runtime',
            region_name=settings.AWS_REGION
        )
        
        # Initialize Pinecone
        self.pc = Pinecone(api_key=settings.PINECONE_API_KEY)
        self.index = self.pc.Index("validert-standards-bedrock")
    
    def generate_embedding(self, text: str) -> List[float]:
        """Generate embedding using Bedrock Titan"""
        try:
            body = json.dumps({
                "inputText": text[:8000]
            })
            
            response = self.bedrock_runtime.invoke_model(
                modelId='amazon.titan-embed-text-v2:0',
                body=body
            )
            
            result = json.loads(response['body'].read())
            return result.get('embedding', [])
            
        except Exception as e:
            logger.error(f"Bedrock embedding error: {str(e)}")
            raise
    
    def retrieve_relevant_chunks(
        self,
        query_text: str,
        top_k: int = 5,
        filter_standard: Optional[str] = None
    ) -> List[Dict]:
        """Retrieve relevant chunks using Bedrock embeddings"""
        try:
            # Truncate query
            truncated_query = query_text[:32000]
            
            # Generate embedding with Bedrock
            logger.info(f"Generating Bedrock embedding for query")
            query_embedding = self.generate_embedding(truncated_query)
            
            # Build filter
            filter_dict = {}
            if filter_standard:
                filter_dict["standard"] = {"$eq": filter_standard}
            
            # Query Pinecone
            logger.info(f"Querying Pinecone for top {top_k} chunks")
            results = self.index.query(
                vector=query_embedding,
                top_k=top_k,
                include_metadata=True,
                filter=filter_dict if filter_dict else None
            )
            
            # Extract chunks
            chunks = []
            for match in results.matches:
                chunks.append({
                    'text': match.metadata.get('text', ''),
                    'standard': match.metadata.get('standard', 'Unknown'),
                    'category': match.metadata.get('category', ''),
                    'score': match.score,
                    'chunk_index': match.metadata.get('chunk_index', 0)
                })
            
            logger.info(f"Retrieved {len(chunks)} relevant chunks from standards")
            
            # Log standard distribution
            standard_counts = {}
            for chunk in chunks:
                std = chunk['standard']
                standard_counts[std] = standard_counts.get(std, 0) + 1
            logger.info(f"Chunks by standard: {standard_counts}")
            
            return chunks
            
        except Exception as e:
            logger.error(f"Error retrieving with Bedrock: {str(e)}")
            return []

