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
_BEDROCK_THROTTLED_UNTIL_TS = 0.0

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
    
    def _invoke_model_with_retry(
        self,
        model_id: str,
        body: str,
        max_retries: int = 5,
        honor_global_cooldown: bool = True,
    ) -> Dict:
        """
        Invoke Bedrock model with exponential backoff retry logic
        
        Args:
            model_id: Bedrock model ID
            body: Request body (JSON string)
            max_retries: Maximum number of retries
        
        Returns:
            Response body as dict
        """
        global _BEDROCK_THROTTLED_UNTIL_TS
        now = time.time()
        if honor_global_cooldown and now < _BEDROCK_THROTTLED_UNTIL_TS:
            cooldown_left = int(_BEDROCK_THROTTLED_UNTIL_TS - now)
            raise Exception(f"AWS Bedrock is currently overloaded (cooldown active for {cooldown_left}s).")

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
                        # Avoid repeatedly hammering Bedrock in tight loops (e.g. per-point ARKAT calls).
                        if honor_global_cooldown:
                            _BEDROCK_THROTTLED_UNTIL_TS = time.time() + 60
                        raise Exception("AWS Bedrock is currently overloaded. Please try again in a few minutes.")
                else:
                    # Non-throttling error, raise immediately
                    raise
            except Exception as e:
                logger.error(f"Bedrock invocation error: {str(e)}")
                raise
        
        raise Exception("Failed to invoke Bedrock model after all retries")
    
    def analyze_report_with_claude(self, user_prompt: str, return_meta: bool = False):
        """
        Analyze report using Claude via AWS Bedrock
        
        Args:
            user_prompt: Fully composed user prompt string
        
        Returns:
            Analysis result as dict, optionally with meta if return_meta=True
        """
        try:
            # Use Claude Sonnet 4 (latest model)
            # Model ID: anthropic.claude-sonnet-4-20250514-v1:0

            def _build_body(prompt: str) -> str:
                return json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 16384,  # Allow full analysis JSON for large reports (30+ pages, many findings)
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
            
            def _invoke_with_prompt(prompt: str) -> Dict:
                logger.info("Invoking Claude Sonnet 4 via Bedrock EU inference profile with retry logic")
                response = self._invoke_model_with_retry(
                    model_id='eu.anthropic.claude-sonnet-4-20250514-v1:0',
                    body=_build_body(prompt),
                    # Main report call must not be blocked by cooldown raised by
                    # lower-priority helper calls.
                    honor_global_cooldown=False,
                )
                stop_reason_local = response.get("stop_reason") or response.get("stopReason")
                return response, stop_reason_local

            response_body, stop_reason = _invoke_with_prompt(user_prompt)
            truncated = stop_reason == "max_tokens"
            if truncated:
                logger.warning("Bedrock response truncated (stop_reason=max_tokens).")
            
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
                logger.warning("Initial JSON parse failed; retrying with JSON-only prompt.")
                compact_prompt = (
                    user_prompt
                    + "\n\nIMPORTANT: Return ONLY a valid JSON object. No prose, no markdown. "
                      "Do not omit findings or deductions. "
                      "Omit optional fields only when empty."
                )
                response_body, stop_reason = _invoke_with_prompt(compact_prompt)
                truncated = truncated or (stop_reason == "max_tokens")
                content = response_body.get('content', [])
                if content and len(content) > 0:
                    response_text = "".join(
                        block.get("text", "")
                        for block in content
                        if isinstance(block, dict) and block.get("text")
                    )
                json_text = _extract_json_block(response_text) or _strip_opening_code_fence(response_text) or response_text
                analysis_data = _parse_json_loose(json_text)
                if analysis_data is None:
                    raise ValueError("Could not parse JSON in AI response")
            if return_meta:
                meta = {
                    "model_name": "eu.anthropic.claude-sonnet-4-20250514-v1:0",
                    "stop_reason": stop_reason,
                    "truncated": truncated,
                    "response_chars": len(response_text or ""),
                }
                return analysis_data, meta
            return analysis_data
            
        except Exception as e:
            logger.error(f"Bedrock analysis error: {str(e)}")
            raise

    def generate_json_with_claude(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        max_retries: int = 5,
        retry_json_prompt: bool = True,
        return_meta: bool = False,
    ):
        """Run a smaller JSON-only Claude call with a caller-provided system prompt."""
        try:
            def _invoke(prompt: str):
                body = json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": max(256, int(max_tokens)),
                    "temperature": 0.0,
                    "top_p": 1.0,
                    "system": system_prompt,
                    "messages": [
                        {
                            "role": "user",
                            "content": prompt,
                        }
                    ],
                })
                response = self._invoke_model_with_retry(
                    model_id='eu.anthropic.claude-sonnet-4-20250514-v1:0',
                    body=body,
                    max_retries=max_retries,
                )
                stop_reason_local = response.get("stop_reason") or response.get("stopReason")
                content = response.get("content", [])
                if content and len(content) > 0:
                    response_text_local = "".join(
                        block.get("text", "")
                        for block in content
                        if isinstance(block, dict) and block.get("text")
                    )
                else:
                    raise ValueError("No content in Bedrock response")
                json_text_local = _extract_json_block(response_text_local) or _strip_opening_code_fence(response_text_local) or response_text_local
                parsed_local = _parse_json_loose(json_text_local)
                return parsed_local, response_text_local, stop_reason_local

            parsed, response_text, stop_reason = _invoke(user_prompt)
            if parsed is None and retry_json_prompt:
                retry_prompt = (
                    user_prompt
                    + "\n\nIMPORTANT: Return ONLY a valid JSON object. No markdown, no bullets, no prose."
                )
                parsed, response_text, stop_reason = _invoke(retry_prompt)
            if parsed is None:
                logger.warning(
                    "Bedrock JSON generation returned non-JSON output; falling back to raw text. Snippet: %s",
                    (response_text or "")[:500],
                )
                parsed = {"_raw_text": response_text or ""}
            if return_meta:
                return parsed, {
                    "model_name": "eu.anthropic.claude-sonnet-4-20250514-v1:0",
                    "stop_reason": stop_reason,
                    "truncated": stop_reason == "max_tokens",
                    "response_chars": len(response_text or ""),
                }
            return parsed
        except Exception as e:
            logger.error(f"Bedrock JSON generation error: {str(e)}")
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
    logger.warning("Failed to parse AI JSON response as JSON. Snippet: %s", text[:500])
    return None
