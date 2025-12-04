"""
One-time script to index Norwegian standards documents into Pinecone
Run this after setting up Pinecone API key in .env
"""
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.rag_indexer import RAGIndexer
from app.config import settings
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def main():
    """Index all standards documents"""
    
    # Check if Pinecone is configured
    if not settings.PINECONE_API_KEY:
        logger.error("PINECONE_API_KEY not set in .env file!")
        logger.error("Please add: PINECONE_API_KEY=your-api-key")
        return
    
    print("\n=== Indexing Norwegian Standards into Pinecone ===\n")
    
    # Initialize indexer
    try:
        indexer = RAGIndexer()
        print("✅ Pinecone indexer initialized\n")
    except Exception as e:
        logger.error(f"Failed to initialize indexer: {str(e)}")
        return
    
    # Standards to index
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
        }
    ]
    
    # Index each document
    total_chunks = 0
    for i, std in enumerate(standards, 1):
        print(f"[{i}/{len(standards)}] Indexing {std['name']}...")
        
        # Check if file exists
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
            print(f"✅ {std['name']} indexed successfully ({num_chunks} chunks)\n")
        except Exception as e:
            logger.error(f"❌ Failed to index {std['name']}: {str(e)}\n")
            continue
    
    # Get final index stats
    print("\n=== Indexing Complete ===\n")
    try:
        stats = indexer.get_index_stats()
        print(f"Total vectors in index: {stats.total_vector_count}")
        print(f"Index dimension: {stats.dimension}")
        print(f"Total namespaces: {len(stats.namespaces)}")
    except Exception as e:
        logger.warning(f"Could not get index stats: {str(e)}")
    
    print(f"\n✅ Successfully indexed {total_chunks} chunks from {len(standards)} standards")
    print("\nNext step: Test retrieval with scripts/test_rag.py")

if __name__ == "__main__":
    main()

