from openai import OpenAI
from typing import Dict, List, Optional, Tuple
import json
import logging
from app.config import settings
from app.schemas import AnalysisResult, ComponentBase, FindingBase
from app.services.system_prompt import SYSTEM_PROMPT
from app.services.improvement_rules import get_improvement_rules_processor
from app.services.rule_output_mapping import get_rule_output_mapper
from app.services.output_formatter import OutputFormatter
from app.services.pdf_extractor import PDFExtractor

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
    NOTE: For Validert, we should try to process full document, but if too large, 
    we need to indicate this in metadata.
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
    
    truncated = f"{first_part}\n\n[... midtdel av rapporten utelatt for å spare tokens - FULL DOKUMENTANALYSE IKKE MULIG ...]\n\n{last_part}"
    
    logger.warning(f"Text truncated from {len(text)} to {len(truncated)} characters (estimated {estimate_tokens(truncated)} tokens)")
    return truncated

def evaluate_triggered_rules(analysis_data: Dict) -> List[str]:
    """
    Evaluate which improvement rules are triggered based on analysis data.
    This is a simplified evaluation - in production, this would be more sophisticated.
    """
    triggered_rules = []
    rules_processor = get_improvement_rules_processor()
    
    findings = analysis_data.get("findings", [])
    
    # Check for ARKAT issues
    for finding in findings:
        problem = finding.get("problem", "").lower()
        description = finding.get("description", "").lower()
        
        # ARKAT_MISSING - check if TG2/TG3 without full ARKAT
        if any(tg in problem or tg in description for tg in ["tg2", "tg3"]):
            if not all(keyword in description for keyword in ["årsak", "konsekvens", "risiko"]):
                if "ARKAT_MISSING" not in triggered_rules:
                    triggered_rules.append("ARKAT_MISSING")
        
        # ARKAT_GENERIC - check for generic text
        generic_phrases = ["som forventet", "normal slitasje", "alder og tilstand"]
        if any(phrase in description for phrase in generic_phrases):
            if "ARKAT_GENERIC" not in triggered_rules:
                triggered_rules.append("ARKAT_GENERIC")
        
        # TGIU_MISUSE
        if "tgiu" in problem or "tgiu" in description:
            if "begrunnelse" not in description and "risiko" not in description:
                if "TGIU_MISUSE" not in triggered_rules:
                    triggered_rules.append("TGIU_MISUSE")
        
        # CONTRADICTION
        if "motstrid" in problem or "motsigelse" in problem:
            if "CONTRADICTION" not in triggered_rules:
                triggered_rules.append("CONTRADICTION")
        
        # UNCLEAR_LANGUAGE
        unclear_phrases = ["kan ikke utelukkes", "bør følges med", "antatt ok"]
        if any(phrase in description for phrase in unclear_phrases):
            if "UNCLEAR_LANGUAGE" not in triggered_rules:
                triggered_rules.append("UNCLEAR_LANGUAGE")
    
    # Check for missing mandatory inspections
    report_text_lower = json.dumps(analysis_data).lower()
    mandatory_items = ["våtrom", "krypkjeller", "rom under terreng", "ventilasjon"]
    missing_count = sum(1 for item in mandatory_items if item not in report_text_lower)
    if missing_count > 2:  # If more than 2 are missing
        if "MISSING_MANDATORY_INSPECTION" not in triggered_rules:
            triggered_rules.append("MISSING_MANDATORY_INSPECTION")
    
    return triggered_rules

class AIAnalyzer:
    """Analyze building condition reports using Validert master system"""
    
    def __init__(self):
        self.rules_processor = get_improvement_rules_processor()
        self.rule_mapper = get_rule_output_mapper()
        self.output_formatter = OutputFormatter()
    
    @staticmethod
    def analyze_report(text: str, report_system: str = None, building_year: int = None, pdf_metadata: Optional[Dict] = None):
        """
        Analyze a building condition report using Validert master system.
        
        CRITICAL FLOW:
        1. Check if full document is analyzed
        2. Evaluate ALL improvement rules
        3. Calculate trygghetsscore ONLY after all rules evaluated
        4. Format output according to output_structure.txt
        
        Args:
            text: Extracted text from PDF (should include all pages, appendices, images)
            report_system: Optional report system identifier
            building_year: Optional building year
            pdf_metadata: Optional PDF metadata (pages, appendices, etc.)
            
        Returns:
            Tuple of (AnalysisResult, formatted_output_dict)
        """
        try:
            # Initialize components
            analyzer = AIAnalyzer()
            
            # Build context for the analysis
            context_info = ""
            if building_year:
                context_info += f"\nByggeår: {building_year}\n"
            if report_system:
                context_info += f"Rapportsystem: {report_system}\n"
            
            # Check if we have PDF metadata, if not try to extract from text
            if pdf_metadata is None:
                # Try to extract metadata from text header if present
                if "[PDF METADATA]" in text:
                    # Extract metadata from text
                    metadata_section = text.split("[PDF METADATA]")[1].split("[START RAPPORTTEKST]")[0]
                    total_pages = 0
                    if "Totalt antall sider:" in metadata_section:
                        try:
                            total_pages = int(metadata_section.split("Totalt antall sider:")[1].split("\n")[0].strip())
                        except:
                            pass
                    pdf_metadata = {
                        "total_pages": total_pages,
                        "pages_with_text": total_pages,
                        "images_detected": 0,
                        "full_document_available": True
                    }
                else:
                    # Default: assume full document if no metadata
                    pdf_metadata = {
                        "total_pages": 0,
                        "pages_with_text": 0,
                        "images_detected": 0,
                        "full_document_available": True  # Default to True if we can't determine
                    }
            
            # Estimate tokens needed for system prompt and response
            system_tokens = estimate_tokens(SYSTEM_PROMPT)
            response_tokens = 8000  # Increased for larger JSON response
            context_tokens = estimate_tokens(context_info)
            buffer_tokens = 1000  # Safety buffer
            
            # Calculate available tokens for the report text
            # Use larger context window if available
            if settings.USE_AWS_BEDROCK:
                # Claude can handle much larger context
                available_tokens = 100000 - system_tokens - response_tokens - context_tokens - buffer_tokens
            else:
                # GPT-4 Turbo has 128k context
                available_tokens = 100000 - system_tokens - response_tokens - context_tokens - buffer_tokens
            
            # ===== RAG RETRIEVAL =====
            rag_context = ""
            if settings.PINECONE_API_KEY:
                try:
                    if settings.USE_AWS_BEDROCK:
                        from app.services.bedrock_rag_retriever import BedrockRAGRetriever
                        retriever = BedrockRAGRetriever()
                    else:
                        from app.services.rag_retriever import RAGRetriever
                        retriever = RAGRetriever()
                    
                    relevant_chunks = retriever.retrieve_relevant_chunks(text, top_k=5)
                    
                    if relevant_chunks:
                        rag_sections = []
                        for chunk in relevant_chunks:
                            rag_sections.append(f"[{chunk['standard']}]\n{chunk['text']}")
                        rag_context = "\n\n---\n\n".join(rag_sections)
                        logger.info(f"Using RAG context from {len(relevant_chunks)} chunks")
                except Exception as e:
                    logger.warning(f"RAG retrieval failed, continuing without: {str(e)}")
            
            # Check if text needs truncation
            text_tokens = estimate_tokens(text)
            text_was_truncated = False
            if text_tokens > available_tokens:
                logger.warning(f"Text too long ({text_tokens} tokens), truncating to fit within limit")
                original_text = text
                text = truncate_text_smart(text, available_tokens)
                text_was_truncated = True
                # Update metadata to reflect truncation
                pdf_metadata["full_document_available"] = False
            
            # Build user prompt
            if rag_context:
                user_prompt = f"""
{context_info}

===== RELEVANTE SEKSJONER FRA STANDARDER OG FORSKRIFTER =====

{rag_context}

===== TILSTANDSRAPPORT SOM SKAL ANALYSERES =====

Analyser følgende norske tilstandsrapport opp mot de relevante standardseksjonene ovenfor.

VIKTIG: Du må analysere HELE dokumentet. Alle sider, vedlegg og bilder må vurderes.

Rapporttekst:
{text}

Produser KUN gyldig JSON i det spesifiserte formatet fra system prompten. Ingen tekst utenfor JSON.
"""
            else:
                user_prompt = f"""
{context_info}

Analyser følgende norske tilstandsrapport.

VIKTIG: Du må analysere HELE dokumentet. Alle sider, vedlegg og bilder må vurderes.

Rapporttekst:
{text}

Produser KUN gyldig JSON i det spesifiserte formatet fra system prompten. Ingen tekst utenfor JSON.
"""
            
            # Call AI (Bedrock or OpenAI)
            if settings.USE_AWS_BEDROCK:
                logger.info("Using AWS Bedrock Claude for analysis")
                from app.services.bedrock_ai import BedrockAI
                bedrock = BedrockAI(region=settings.AWS_REGION)
                analysis_data = bedrock.analyze_report_with_claude(
                    report_text=text,
                    rag_context=rag_context,
                    context_info=context_info
                )
            else:
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
                        max_tokens=8000  # Increased for larger JSON
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
                            max_tokens=8000
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
            
            # STEP 1: Check full document analysis from metadata in response
            metadata = analysis_data.get("metadata", {})
            full_document_analysis = metadata.get("full_document_analysis", True)
            
            # If text was truncated, mark as incomplete
            if text_was_truncated:
                full_document_analysis = False
                metadata["full_document_analysis"] = False
                metadata["truncation_warning"] = "Rapporttekst ble trunkert - full dokumentanalyse ikke mulig"
            
            # Update metadata with PDF info
            if pdf_metadata:
                metadata["pages_analyzed"] = pdf_metadata.get("total_pages", 0)
                metadata["appendices_analyzed"] = pdf_metadata.get("appendices_estimated", 0)
                metadata["images_detected"] = pdf_metadata.get("images_detected", 0)
            
            analysis_data["metadata"] = metadata
            
            # STEP 2: Evaluate ALL improvement rules
            triggered_rules = evaluate_triggered_rules(analysis_data)
            logger.info(f"Triggered {len(triggered_rules)} improvement rules: {triggered_rules}")
            
            # STEP 3: Format output according to output_structure.txt
            formatted_output = analyzer.output_formatter.format_analysis_output(
                analysis_data=analysis_data,
                pdf_metadata=pdf_metadata,
                triggered_rules=triggered_rules
            )
            
            # If full document not analyzed, return early with formatted output
            if not full_document_analysis:
                logger.warning("Full document analysis not completed - returning metadata only")
                # Still return a minimal AnalysisResult for backward compatibility
                result = AnalysisResult(
                    overall_score=0.0,
                    quality_score=0.0,
                    completeness_score=0.0,
                    compliance_score=0.0,
                    components=[],
                    findings=[],
                    summary="Full dokumentanalyse ikke gjennomført",
                    recommendations=[]
                )
                return result, formatted_output
            
            # STEP 4: Calculate trygghetsscore (ONLY after all rules evaluated)
            trygghetsscore_data = formatted_output.get("trygghetsscore", {})
            overall_score = trygghetsscore_data.get("score", 0)
            
            # If score is still 0, try to get it directly from analysis_data (new format)
            if overall_score == 0:
                ai_trygghetsscore = analysis_data.get("trygghetsscore", {})
                if isinstance(ai_trygghetsscore, dict):
                    overall_score = ai_trygghetsscore.get("score", 0)
                    if overall_score > 0:
                        logger.info(f"Extracted score from analysis_data.trygghetsscore: {overall_score}")
            
            # If still 0, try old format
            if overall_score == 0:
                scores = analysis_data.get("scores", {})
                overall_score = scores.get("total_score", 0)
                if overall_score > 0:
                    logger.info(f"Extracted score from analysis_data.scores.total_score: {overall_score}")
            
            # Map to existing schema for backward compatibility
            scores = analysis_data.get("scores", {})
            quality_score = (scores.get("language_clarity_score", 0) + scores.get("legal_safety_score", 0)) / 20 * 100
            completeness_score = (scores.get("ns3600_score", 0) + scores.get("ns3940_score", 0)) / 30 * 100
            compliance_score = (scores.get("forskrift_score", 0) + scores.get("tek_score", 0)) / 50 * 100
            
            # Extract findings for backward compatibility
            # NEW FORMAT: Convert forbedringsliste to findings
            findings = []
            components = []
            
            # First, try new format: forbedringsliste
            forbedringsliste = formatted_output.get("forbedringsliste", [])
            if forbedringsliste:
                logger.info(f"Converting {len(forbedringsliste)} forbedringsliste items to findings")
                for item in forbedringsliste:
                    # Determine severity based on category
                    kategori = item.get("kategori", "")
                    if "SPERRE" in kategori:
                        severity = "critical"
                    elif "Vesentlig" in kategori:
                        severity = "high"
                    else:
                        severity = "medium"
                    
                    # Build description from forbedringsliste item
                    description_parts = []
                    if item.get("hva_er_feil"):
                        description_parts.append(f"Hva er feil: {item['hva_er_feil']}")
                    if item.get("hvorfor_problem"):
                        description_parts.append(f"\n\nHvorfor problem: {item['hvorfor_problem']}")
                    if item.get("konsekvens_ikke_rettet"):
                        description_parts.append(f"\n\nKonsekvens: {item['konsekvens_ikke_rettet']}")
                    
                    description = "\n".join(description_parts) if description_parts else item.get("hva_er_feil", "")
                    
                    findings.append(FindingBase(
                        finding_type="quality_issue",
                        severity=severity,
                        title=item.get("hva_er_feil", "Forbedringspunkt"),
                        description=description,
                        suggestion=item.get("hva_må_endres", ""),
                        standard_reference=item.get("hvor_i_rapporten", "")
                    ))
            
            # Also check old format: findings array (for backward compatibility)
            findings_list = analysis_data.get("findings", [])
            if findings_list and not forbedringsliste:
                logger.info(f"Using old format findings: {len(findings_list)} items")
            for find in findings_list:
                severity_map = {"error": "critical", "warning": "high", "info": "medium"}
                severity = severity_map.get(find.get("severity", "info"), "medium")
                
                description = find.get("risk", find.get("problem", ""))
                arsak = find.get("arsak", "")
                konsekvens = find.get("konsekvens", "")
                
                if arsak or konsekvens:
                    description_parts = [description]
                    if arsak:
                        description_parts.append(f"\n\nÅrsak: {arsak}")
                    if konsekvens:
                        description_parts.append(f"\n\nKonsekvens: {konsekvens}")
                    description = "\n".join(description_parts)
                
                findings.append(FindingBase(
                    finding_type=find.get("standard", "quality_issue"),
                    severity=severity,
                    title=find.get("problem", find.get("component_name", "Finding")),
                    description=description,
                        suggestion=find.get("recommended_text", find.get("suggested_fix", "")),
                    standard_reference=find.get("reference", find.get("standard", ""))
                ))
            
            # Extract components from findings (if available in old format)
            component_map = {}
            for find in findings_list:
                comp_type = find.get("component_type", "general")
                comp_name = find.get("component_name", "Unknown")
                if comp_type and comp_name:
                    key = f"{comp_type}_{comp_name}"
                    if key not in component_map:
                        component_map[key] = {
                            "component_type": comp_type,
                            "name": comp_name,
                            "condition": None,
                            "description": "",
                            "score": None
                        }
            
            # Also extract components from forbedringsliste (hvor_i_rapporten)
            for item in forbedringsliste:
                hvor = item.get("hvor_i_rapporten", "")
                if hvor and " / " in hvor:
                    parts = hvor.split(" / ")
                    if len(parts) >= 2:
                        comp_name = parts[0].strip()
                        comp_type = parts[1].strip()
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
            
            # Get summary
            executive_summary = formatted_output.get("executive_summary", "")
            overall_assessment = analysis_data.get("overall_assessment", {})
            summary = executive_summary or overall_assessment.get("summary", "")
            
            # Get recommendations from forbedringsliste
            recommendations = []
            forbedringsliste = formatted_output.get("forbedringsliste", [])
            for item in forbedringsliste:
                recommendations.append(item.get("hva_må_endres", ""))
            
            # Build AnalysisResult for backward compatibility
            result = AnalysisResult(
                overall_score=overall_score,
                quality_score=min(quality_score, 100),
                completeness_score=min(completeness_score, 100),
                compliance_score=min(compliance_score, 100),
                components=components,
                findings=findings,
                summary=summary,
                recommendations=recommendations
            )
            
            logger.info(f"Successfully analyzed report. Trygghetsscore: {overall_score}, Triggered rules: {len(triggered_rules)}")
            
            # Store formatted output in analysis_data for frontend
            analysis_data["formatted_output"] = formatted_output
            analysis_data["triggered_rules"] = triggered_rules
            
            return result, analysis_data
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse AI response as JSON: {str(e)}")
            raise Exception("Failed to parse AI analysis response")
        except Exception as e:
            logger.error(f"Error analyzing report with AI: {str(e)}", exc_info=True)
            raise Exception(f"AI analysis failed: {str(e)}")
