"""
AWS Bedrock AI Service - Alternative to OpenAI for embeddings and LLM
"""
import boto3
import json
import logging
from typing import Dict, List
from app.config import settings
from app.schemas import AnalysisResult, ComponentBase, FindingBase
from app.services.system_prompt import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

class BedrockAI:
    """AWS Bedrock client for embeddings and LLM inference"""
    
    def __init__(self, region: str = "us-east-1"):
        self.bedrock_runtime = boto3.client(
            service_name='bedrock-runtime',
            region_name=region
        )
        self.bedrock = boto3.client(
            service_name='bedrock',
            region_name=region
        )
    
    def generate_embedding(self, text: str) -> List[float]:
        """
        Generate embedding using AWS Bedrock Titan Embeddings
        
        Args:
            text: Text to embed
        
        Returns:
            Embedding vector (list of floats)
        """
        try:
            # Use Titan Embeddings V2
            # Model ID: amazon.titan-embed-text-v2:0
            # Dimensions: 1024 or 1536 (configurable)
            
            body = json.dumps({
                "inputText": text[:8000]  # Truncate to avoid limits
            })
            
            response = self.bedrock_runtime.invoke_model(
                modelId='amazon.titan-embed-text-v2:0',
                body=body,
                contentType='application/json',
                accept='application/json'
            )
            
            response_body = json.loads(response['body'].read())
            embedding = response_body.get('embedding', [])
            
            return embedding
            
        except Exception as e:
            logger.error(f"Bedrock embedding error: {str(e)}")
            raise
    
    def analyze_report_with_claude(
        self,
        report_text: str,
        rag_context: str = "",
        context_info: str = ""
    ) -> Dict:
        """
        Analyze report using Claude via AWS Bedrock
        
        Args:
            report_text: Extracted report text
            rag_context: Retrieved chunks from RAG
            context_info: Building year, report system, etc.
        
        Returns:
            Analysis result as dict
        """
        try:
            # Build prompt
            if rag_context:
                user_message = f"""
{context_info}

===== RELEVANTE SEKSJONER FRA STANDARDER OG FORSKRIFTER =====

{rag_context}

===== TILSTANDSRAPPORT SOM SKAL ANALYSERES =====

{report_text}

Analyser tilstandsrapporten opp mot de relevante standardseksjonene ovenfor.
Produser KUN gyldig JSON i det spesifiserte formatet. Ingen tekst utenfor JSON.
"""
            else:
                user_message = f"""
{context_info}

Analyser følgende norske tilstandsrapport:

{report_text}

Produser KUN gyldig JSON i det spesifiserte formatet. Ingen tekst utenfor JSON.
"""
            
            # Use Claude Sonnet 4 (latest model)
            # Model ID: anthropic.claude-sonnet-4-20250514-v1:0
            
            body = json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 4000,
                "temperature": 0.3,
                "system": SYSTEM_PROMPT,
                "messages": [
                    {
                        "role": "user",
                        "content": user_message
                    }
                ]
            })
            
            # Use Claude Sonnet 4 via EU inference profile (approved and working)
            logger.info("Invoking Claude Sonnet 4 via Bedrock EU inference profile")
            
            # Use EU inference profile for Claude Sonnet 4
            response = self.bedrock_runtime.invoke_model(
                modelId='eu.anthropic.claude-sonnet-4-20250514-v1:0',
                body=body,
                contentType='application/json',
                accept='application/json'
            )
            
            response_body = json.loads(response['body'].read())
            
            # Extract text from Claude response
            content = response_body.get('content', [])
            if content and len(content) > 0:
                response_text = content[0].get('text', '')
            else:
                raise ValueError("No content in Bedrock response")
            
            # Parse JSON from response
            json_start = response_text.find("{")
            json_end = response_text.rfind("}") + 1
            
            if json_start != -1 and json_end > json_start:
                json_text = response_text[json_start:json_end]
                analysis_data = json.loads(json_text)
                return analysis_data
            else:
                raise ValueError("Could not find JSON in AI response")
            
        except Exception as e:
            logger.error(f"Bedrock analysis error: {str(e)}")
            raise
    
    def list_available_models(self):
        """List available Bedrock models"""
        try:
            response = self.bedrock.list_foundation_models()
            models = response.get('modelSummaries', [])
            
            logger.info(f"Found {len(models)} Bedrock models")
            for model in models:
                logger.info(f"- {model['modelId']}: {model.get('modelName', 'Unknown')}")
            
            return models
            
        except Exception as e:
            logger.error(f"Error listing Bedrock models: {str(e)}")
            return []

