"""
AWS Bedrock AI Service - Alternative to OpenAI for embeddings and LLM
"""
import boto3
import json
import logging
import re
import time
from typing import Dict, List, Optional
from botocore.config import Config
from botocore.exceptions import ClientError
from app.services.system_prompt import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)

class BedrockAI:
    """AWS Bedrock client for embeddings and LLM inference"""
    
    def __init__(self, region: str = "us-east-1"):
        # Configure boto3 with retry logic and connection pooling
        config = Config(
            region_name=region,
            retries={
                'max_attempts': 5,
                'mode': 'adaptive'  # Adaptive retry mode for better throttling handling
            },
            max_pool_connections=10,  # Limit concurrent connections
            connect_timeout=10,
            read_timeout=300  # 5 minutes for large PDF analysis
        )
        
        self.bedrock_runtime = boto3.client(
            service_name='bedrock-runtime',
            region_name=region,
            config=config
        )
        self.bedrock = boto3.client(
            service_name='bedrock',
            region_name=region,
            config=config
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
            
            # Use retry logic to handle throttling
            response_body = self._invoke_model_with_retry(
                model_id='amazon.titan-embed-text-v2:0',
                body=body
            )
            embedding = response_body.get('embedding', [])
            
            return embedding
            
        except Exception as e:
            logger.error(f"Bedrock embedding error: {str(e)}")
            raise
    
    def _invoke_model_with_retry(self, model_id: str, body: str, max_retries: int = 5) -> Dict:
        """
        Invoke Bedrock model with exponential backoff retry logic
        
        Args:
            model_id: Bedrock model ID
            body: Request body (JSON string)
            max_retries: Maximum number of retries
        
        Returns:
            Response body as dict
        """
        for attempt in range(max_retries):
            try:
                response = self.bedrock_runtime.invoke_model(
                    modelId=model_id,
                    body=body,
                    contentType='application/json',
                    accept='application/json'
                )
                return json.loads(response['body'].read())
                
            except ClientError as e:
                error_code = e.response.get('Error', {}).get('Code', '')
                
                # Check if it's a throttling error
                if error_code in ['ThrottlingException', 'ServiceUnavailableException', 'TooManyRequestsException']:
                    if attempt < max_retries - 1:
                        # Exponential backoff: 1s, 2s, 4s, 8s, 16s
                        wait_time = 2 ** attempt
                        logger.warning(f"Bedrock throttling (attempt {attempt + 1}/{max_retries}), waiting {wait_time}s...")
                        time.sleep(wait_time)
                        continue
                    else:
                        logger.error(f"Bedrock throttling - max retries reached")
                        raise Exception("AWS Bedrock is currently overloaded. Please try again in a few minutes.")
                else:
                    # Non-throttling error, raise immediately
                    raise
            except Exception as e:
                logger.error(f"Bedrock invocation error: {str(e)}")
                raise
        
        raise Exception("Failed to invoke Bedrock model after all retries")
    
    def analyze_report_with_claude(self, user_prompt: str) -> Dict:
        """
        Analyze report using Claude via AWS Bedrock
        
        Args:
            user_prompt: Fully composed user prompt string
        
        Returns:
            Analysis result as dict
        """
        try:
            # Use Claude Sonnet 4 (latest model)
            # Model ID: anthropic.claude-sonnet-4-20250514-v1:0

            def _build_body(prompt: str) -> str:
                return json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 8000,  # Increased for larger JSON response with new structure
                    "temperature": 0.0,
                    "top_p": 1.0,
                    "system": SYSTEM_PROMPT,
                    "messages": [
                        {
                            "role": "user",
                            "content": prompt
                        }
                    ]
                })
            
            # Use Claude Sonnet 4 via EU inference profile (approved and working)
            logger.info("Invoking Claude Sonnet 4 via Bedrock EU inference profile with retry logic")
            
            # Use retry logic to handle throttling
            response_body = self._invoke_model_with_retry(
                model_id='eu.anthropic.claude-sonnet-4-20250514-v1:0',
                body=_build_body(user_prompt)
            )
            stop_reason = response_body.get("stop_reason") or response_body.get("stopReason")
            if stop_reason == "max_tokens":
                logger.warning("Bedrock response truncated (stop_reason=max_tokens). Retrying with compact output request.")
                compact_prompt = (
                    user_prompt
                    + "\n\nIMPORTANT: Previous response was truncated. Return a shorter, compact JSON. "
                      "Limit findings to max 15 and improvements to max 10. "
                      "Use at most 1 evidence snippet per issue. "
                      "Omit optional fields when not needed."
                )
                response_body = self._invoke_model_with_retry(
                    model_id='eu.anthropic.claude-sonnet-4-20250514-v1:0',
                    body=_build_body(compact_prompt)
                )
            
            # Extract text from Claude response
            content = response_body.get('content', [])
            if content and len(content) > 0:
                response_text = "".join(
                    block.get("text", "")
                    for block in content
                    if isinstance(block, dict) and block.get("text")
                )
            else:
                raise ValueError("No content in Bedrock response")
            
            # Parse JSON from response (robust to code fences / trailing commas)
            json_text = _extract_json_block(response_text) or _strip_opening_code_fence(response_text) or response_text
            analysis_data = _parse_json_loose(json_text)
            if analysis_data is None:
                raise ValueError("Could not parse JSON in AI response")
            return analysis_data
            
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


def _extract_json_block(text: str) -> Optional[str]:
    if not text:
        return None
    fence_match = _CODE_FENCE_RE.search(text)
    if fence_match:
        return fence_match.group(1).strip()

    # Find first balanced JSON object in the text.
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for idx in range(start, len(text)):
        ch = text[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == "\"":
                in_string = False
            continue
        if ch == "\"":
            in_string = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:idx + 1].strip()
    return None


def _strip_opening_code_fence(text: str) -> Optional[str]:
    if not text:
        return None
    stripped = text.lstrip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) > 1:
            return "\n".join(lines[1:]).strip()
    return None


def _parse_json_loose(text: str) -> Optional[Dict]:
    if not text:
        return None
    candidates = [text]
    cleaned = re.sub(r",\s*(\}|\])", r"\1", text)
    if cleaned != text:
        candidates.append(cleaned)
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    logger.error("Failed to parse AI JSON response. Snippet: %s", text[:500])
    return None
