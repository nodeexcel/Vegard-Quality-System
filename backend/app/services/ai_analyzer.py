from openai import OpenAI
from typing import Dict, List
import json
import logging
from app.config import settings
from app.schemas import AnalysisResult, ComponentBase, FindingBase
from app.services.system_prompt import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# Initialize OpenAI client (lazy initialization to avoid import-time errors)
_client = None

def get_openai_client():
    """Get or create OpenAI client instance"""
    global _client
    if _client is None:
        _client = OpenAI(api_key=settings.OPENAI_API_KEY)
    return _client

def estimate_tokens(text: str) -> int:
    """
    Estimate token count for text.
    Rough approximation: 1 token ≈ 4 characters for Norwegian text.
    """
    return len(text) // 4

def truncate_text_smart(text: str, max_tokens: int = 5000) -> str:
    """
    Truncate text intelligently to fit within token limit.
    Keeps the beginning and end of the text, removing middle sections.
    """
    # Estimate how many characters we can use
    max_chars = max_tokens * 4
    
    if len(text) <= max_chars:
        return text
    
    # Keep first 60% and last 40% of available space
    first_part_chars = int(max_chars * 0.6)
    last_part_chars = int(max_chars * 0.4)
    
    first_part = text[:first_part_chars]
    last_part = text[-last_part_chars:]
    
    truncated = f"{first_part}\n\n[... midtdel av rapporten utelatt for å spare tokens ...]\n\n{last_part}"
    
    logger.info(f"Truncated text from {len(text)} to {len(truncated)} characters (estimated {estimate_tokens(truncated)} tokens)")
    return truncated

class AIAnalyzer:
    """Analyze building condition reports using OpenAI GPT"""
    
    @staticmethod
    def analyze_report(text: str, report_system: str = None, building_year: int = None):
        """
        Analyze a building condition report using OpenAI GPT
        
        Args:
            text: Extracted text from PDF
            report_system: Optional report system identifier
            building_year: Optional building year
            
        Returns:
            AnalysisResult with scores, components, and findings
        """
        try:
            # Build context for the analysis
            context_info = ""
            if building_year:
                context_info += f"\nByggeår: {building_year}\n"
            if report_system:
                context_info += f"Rapportsystem: {report_system}\n"
            
            # Estimate tokens needed for system prompt and response
            system_tokens = estimate_tokens(SYSTEM_PROMPT)
            response_tokens = 4000  # max_tokens for response
            context_tokens = estimate_tokens(context_info)
            buffer_tokens = 500  # safety buffer
            
            # Calculate available tokens for the report text
            # Using gpt-4-turbo-preview which has 128k context, but we'll be conservative
            # For safety, limit to ~6000 tokens for text to ensure we stay well under limits
            available_tokens = 6000 - system_tokens - response_tokens - context_tokens - buffer_tokens
            
            # ===== RAG RETRIEVAL (NEW) =====
            # Retrieve relevant chunks from standards if RAG is available
            rag_context = ""
            if settings.PINECONE_API_KEY:
                try:
                    # Import appropriate retriever based on configuration
                    if settings.USE_AWS_BEDROCK:
                        from app.services.bedrock_rag_retriever import BedrockRAGRetriever
                        retriever = BedrockRAGRetriever()
                    else:
                        from app.services.rag_retriever import RAGRetriever
                        retriever = RAGRetriever()
                    
                    relevant_chunks = retriever.retrieve_relevant_chunks(text, top_k=5)
                    
                    if relevant_chunks:
                        # Build context from retrieved chunks
                        rag_sections = []
                        for chunk in relevant_chunks:
                            rag_sections.append(
                                f"[{chunk['standard']}]\n{chunk['text']}"
                            )
                        rag_context = "\n\n---\n\n".join(rag_sections)
                        logger.info(f"Using RAG context from {len(relevant_chunks)} chunks")
                except Exception as e:
                    logger.warning(f"RAG retrieval failed, continuing without: {str(e)}")
            else:
                logger.info("RAG not available or not configured, using system prompt only")
            # ===== END RAG RETRIEVAL =====
            
            # Truncate text if needed
            text_tokens = estimate_tokens(text)
            if text_tokens > available_tokens:
                logger.warning(f"Text too long ({text_tokens} tokens), truncating to fit within limit")
                truncated_text = truncate_text_smart(text, available_tokens)
            else:
                truncated_text = text
            
            # Enhanced user prompt with RAG context
            if rag_context:
                user_prompt = f"""
{context_info}

===== RELEVANTE SEKSJONER FRA STANDARDER OG FORSKRIFTER =====

{rag_context}

===== TILSTANDSRAPPORT SOM SKAL ANALYSERES =====

Analyser følgende norske tilstandsrapport opp mot de relevante standardseksjonene ovenfor:

Rapporttekst:
{truncated_text}

Produser KUN gyldig JSON i det spesifiserte formatet. Ingen tekst utenfor JSON.
"""
            else:
                user_prompt = f"""
{context_info}

Analyser følgende norske tilstandsrapport:

Rapporttekst:
{truncated_text}

Produser KUN gyldig JSON i det spesifiserte formatet. Ingen tekst utenfor JSON.
"""
            
            # Use Bedrock or OpenAI based on configuration
            if settings.USE_AWS_BEDROCK:
                # Use AWS Bedrock Claude
                logger.info("Using AWS Bedrock Claude for analysis")
                from app.services.bedrock_ai import BedrockAI
                bedrock = BedrockAI(region=settings.AWS_REGION)
                analysis_data = bedrock.analyze_report_with_claude(
                    report_text=truncated_text,
                    rag_context=rag_context,
                    context_info=context_info
                )
                # analysis_data is already parsed JSON from Bedrock
                # Skip the JSON extraction step below
            else:
                # Use OpenAI GPT-4
                logger.info("Using OpenAI GPT-4 for analysis")
                client = get_openai_client()
                model = "gpt-4-turbo-preview"
                
                try:
                    response = client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": user_prompt}
                        ],
                        temperature=0.3,
                        max_tokens=4000
                    )
                except Exception as e:
                    if "gpt-4-turbo-preview" in str(e) or "model" in str(e).lower():
                        logger.info("Falling back to gpt-4o model")
                        model = "gpt-4o"
                        response = client.chat.completions.create(
                            model=model,
                            messages=[
                                {"role": "system", "content": SYSTEM_PROMPT},
                                {"role": "user", "content": user_prompt}
                            ],
                            temperature=0.3,
                            max_tokens=4000
                        )
                    else:
                        raise
                
                # Parse the response
                response_text = response.choices[0].message.content.strip()
                
                # Try to extract JSON from the response
                json_start = response_text.find("{")
                json_end = response_text.rfind("}") + 1
                
                if json_start != -1 and json_end > json_start:
                    json_text = response_text[json_start:json_end]
                    analysis_data = json.loads(json_text)
                else:
                    raise ValueError("Could not find JSON in AI response")
            
            # Now analysis_data is set from either Bedrock or OpenAI
            
            # Map new structure to existing schema
            scores = analysis_data.get("scores", {})
            total_score = scores.get("total_score", 0)
            
            # Extract findings from new structure
            findings_list = analysis_data.get("findings", [])
            findings = []
            for find in findings_list:
                # Map severity: error->critical, warning->high, info->medium/low
                severity_map = {
                    "error": "critical",
                    "warning": "high", 
                    "info": "medium"
                }
                severity = severity_map.get(find.get("severity", "info"), "medium")
                
                # Build description with konsekvens/årsak if available
                description = find.get("risk", find.get("problem", ""))
                arsak = find.get("arsak", "")
                konsekvens = find.get("konsekvens", "")
                
                # If TG2 or TG3 related finding, include årsak/konsekvens in description
                if arsak or konsekvens:
                    description_parts = [description]
                    if arsak:
                        description_parts.append(f"\n\nÅrsak: {arsak}")
                    if konsekvens:
                        description_parts.append(f"\n\nKonsekvens: {konsekvens}")
                    description = "\n".join(description_parts)
                
                # Get recommended text (for TG2/TG3 improvements)
                recommended_text = find.get("recommended_text", find.get("suggested_fix", ""))
                
                findings.append(FindingBase(
                    finding_type=find.get("standard", "quality_issue"),
                    severity=severity,
                    title=find.get("problem", find.get("component_name", "Finding")),
                    description=description,
                    suggestion=recommended_text or find.get("suggested_fix"),
                    standard_reference=find.get("reference", find.get("standard", ""))
                ))
            
            # Extract components from findings if available
            components = []
            component_map = {}
            for find in findings_list:
                comp_type = find.get("component_type", "general")
                comp_name = find.get("component_name", "Unknown")
                key = f"{comp_type}_{comp_name}"
                if key not in component_map:
                    component_map[key] = {
                        "component_type": comp_type,
                        "name": comp_name,
                        "condition": None,
                        "description": "",
                        "score": None
                    }
            
            components = [ComponentBase(**comp) for comp in component_map.values()]
            
            # Get summary from overall_assessment
            overall_assessment = analysis_data.get("overall_assessment", {})
            summary = overall_assessment.get("summary", overall_assessment.get("short_verdict", ""))
            
            # Get recommendations (handle both old string format and new object format)
            improvement = analysis_data.get("improvement_suggestions", {})
            recommendations = []
            for_takstmann = improvement.get("for_takstmann", [])
            for_report_text = improvement.get("for_report_text", [])
            
            # Convert to list of strings for backward compatibility
            for item in for_takstmann + for_report_text:
                if isinstance(item, dict):
                    # New format: extract issue or recommended_text
                    recommendations.append(item.get("issue", item.get("recommended_text", "")))
                else:
                    # Old format: just a string
                    recommendations.append(item)
            
            # Calculate scores (map from new structure)
            # Normalize to 0-100 scale
            # Quality: language_clarity (0-10) + legal_safety (0-10) = max 20
            quality_raw = scores.get("language_clarity_score", 0) + scores.get("legal_safety_score", 0)
            quality_score = (quality_raw / 20) * 100  # Normalize to 0-100
            
            # Completeness: ns3600 (0-20) + ns3940 (0-10) = max 30
            completeness_raw = scores.get("ns3600_score", 0) + scores.get("ns3940_score", 0)
            completeness_score = (completeness_raw / 30) * 100  # Normalize to 0-100
            
            # Compliance: forskrift (0-40) + tek (0-10) = max 50
            compliance_raw = scores.get("forskrift_score", 0) + scores.get("tek_score", 0)
            compliance_score = (compliance_raw / 50) * 100  # Normalize to 0-100
            
            result = AnalysisResult(
                overall_score=total_score,
                quality_score=min(quality_score, 100),
                completeness_score=min(completeness_score, 100),
                compliance_score=min(compliance_score, 100),
                components=components,
                findings=findings,
                summary=summary,
                recommendations=recommendations
            )
            
            logger.info(f"Successfully analyzed report. Overall score: {result.overall_score}")
            return result, analysis_data  # Return both mapped result and full analysis
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse AI response as JSON: {str(e)}")
            logger.error(f"Response was: {response_text[:500]}")
            raise Exception("Failed to parse AI analysis response")
        except Exception as e:
            logger.error(f"Error analyzing report with AI: {str(e)}")
            raise Exception(f"AI analysis failed: {str(e)}")

