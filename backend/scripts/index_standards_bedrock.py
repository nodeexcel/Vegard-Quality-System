"""
Index Norwegian standards using AWS Bedrock embeddings
"""
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.bedrock_rag_indexer import BedrockRAGIndexer
from app.config import settings
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def main():
    """Index all standards documents using Bedrock"""
    
    if not settings.PINECONE_API_KEY:
        logger.error("PINECONE_API_KEY not set!")
        return
    
    print("\n=== Indexing Standards with AWS Bedrock ===\n")
    
    try:
        indexer = BedrockRAGIndexer()
        print("✅ Bedrock RAG indexer initialized\n")
    except Exception as e:
        logger.error(f"Failed to initialize: {str(e)}")
        return
    
    base_path = "data/standards"
    standards = [
        {
            "path": f"{base_path}/NS 3600 2018.pdf",
            "name": "NS 3600:2018",
            "category": "technical_analysis"
        },
        {
            "path": f"{base_path}/NS 3940- Ny 2023.pdf",
            "name": "NS 3940:2023",
            "category": "area_calculations"
        },
        {
            "path": f"{base_path}/forskrift_til_avhendingslova.pdf",
            "name": "Forskrift til avhendingslova",
            "category": "legal_requirement"
        },
        {
            "path": f"{base_path}/prop-44-l-2018-2019.pdf",
            "name": "Prop. 44 L",
            "category": "legal_interpretation"
        },
        {
            "path": f"{base_path}/NS3600_2025.pdf",
            "name": "NS 3600:2025",
            "category": "technical_analysis"
        },
        {
            "path": f"{base_path}/Avhl_TryggereBolighandel_NY.pdf",
            "name": "Tryggere bolighandel (Avhl.)",
            "category": "legal_interpretation"
        }
    ]
    
    total_chunks = 0
    for i, std in enumerate(standards, 1):
        print(f"[{i}/{len(standards)}] Indexing {std['name']} with Bedrock...")
        
        if not os.path.exists(std['path']):
            logger.error(f"File not found: {std['path']}")
            continue
        
        try:
            num_chunks = indexer.index_document(
                file_path=std['path'],
                metadata={
                    'standard': std['name'],
                    'category': std['category']
                }
            )
            total_chunks += num_chunks
            print(f"✅ {std['name']} indexed ({num_chunks} chunks)\n")
        except Exception as e:
            logger.error(f"❌ Failed to index {std['name']}: {str(e)}\n")
            continue
    
    print(f"\n✅ Indexed {total_chunks} chunks with Bedrock embeddings")
    print("Using index: validert-standards-bedrock (1024 dimensions)")

if __name__ == "__main__":
    main()
