"""
Bedrock RAG Indexer - Index standards using AWS Bedrock embeddings
"""
import boto3
import json
from pinecone import Pinecone, ServerlessSpec
from langchain_text_splitters import RecursiveCharacterTextSplitter
from app.services.pdf_extractor import PDFExtractor
from app.config import settings
import logging
from typing import Dict, List
import time

logger = logging.getLogger(__name__)

class BedrockRAGIndexer:
    """Index standards documents using AWS Bedrock embeddings"""
    
    def __init__(self):
        # Initialize Bedrock
        self.bedrock_runtime = boto3.client(
            service_name='bedrock-runtime',
            region_name=settings.AWS_REGION
        )
        
        # Initialize Pinecone
        self.pc = Pinecone(api_key=settings.PINECONE_API_KEY)
        # Use separate index for Bedrock (1024 dimensions vs OpenAI 1536)
        self.index_name = "validert-standards-bedrock"
        
        # Create or connect to index
        if self.index_name not in [idx.name for idx in self.pc.list_indexes()]:
            logger.info(f"Creating new Pinecone index for Bedrock: {self.index_name}")
            self.pc.create_index(
                name=self.index_name,
                dimension=1024,  # Titan embeddings v2 dimension
                metric='cosine',
                spec=ServerlessSpec(
                    cloud='aws',
                    region=settings.PINECONE_ENVIRONMENT
                )
            )
            time.sleep(10)
        
        self.index = self.pc.Index(self.index_name)
        
        # Text splitter
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=200,
            length_function=len,
            separators=["\n\n", "\n", ". ", " ", ""]
        )
    
    def generate_embedding(self, text: str) -> List[float]:
        """Generate embedding using Bedrock Titan"""
        try:
            body = json.dumps({
                "inputText": text[:8000]
            })
            
            response = self.bedrock_runtime.invoke_model(
                modelId='amazon.titan-embed-text-v2:0',
                body=body,
                contentType='application/json',
                accept='application/json'
            )
            
            response_body = json.loads(response['body'].read())
            return response_body.get('embedding', [])
            
        except Exception as e:
            logger.error(f"Bedrock embedding error: {str(e)}")
            raise
    
    def index_document(self, file_path: str, metadata: Dict):
        """Index a standards document using Bedrock embeddings"""
        try:
            logger.info(f"Starting indexing of {metadata['standard']} with Bedrock")
            
            # Extract text
            if file_path.endswith('.pdf'):
                extractor = PDFExtractor()
                with open(file_path, 'rb') as f:
                    text = extractor.extract_text(f)
            else:
                with open(file_path, 'r', encoding='utf-8') as f:
                    text = f.read()
            
            logger.info(f"Extracted {len(text)} characters")
            
            # Split into chunks
            chunks = self.text_splitter.split_text(text)
            logger.info(f"Split into {len(chunks)} chunks")
            
            # Process in batches
            batch_size = 25  # Bedrock has stricter rate limits
            total_uploaded = 0
            
            for i in range(0, len(chunks), batch_size):
                batch_chunks = chunks[i:i+batch_size]
                
                # Generate embeddings using Bedrock
                vectors = []
                for j, chunk in enumerate(batch_chunks):
                    chunk_index = i + j
                    
                    # Generate embedding
                    embedding = self.generate_embedding(chunk)
                    
                    vector_id = f"{metadata['standard'].replace(' ', '_').replace(':', '_')}-chunk-{chunk_index}"
                    vectors.append({
                        'id': vector_id,
                        'values': embedding,
                        'metadata': {
                            'text': chunk,
                            'standard': metadata['standard'],
                            'category': metadata.get('category', 'unknown'),
                            'chunk_index': chunk_index,
                            'embedding_model': 'bedrock-titan-v2'
                        }
                    })
                    
                    # Small delay between embeddings
                    time.sleep(0.1)
                
                # Upload batch to Pinecone
                self.index.upsert(vectors=vectors)
                total_uploaded += len(vectors)
                logger.info(f"Uploaded batch {i//batch_size + 1} ({total_uploaded}/{len(chunks)} chunks)")
                
                time.sleep(1)  # Delay between batches
            
            logger.info(f"✅ Indexed {metadata['standard']} with Bedrock ({total_uploaded} chunks)")
            return total_uploaded
            
        except Exception as e:
            logger.error(f"Error indexing with Bedrock: {str(e)}")
            raise

