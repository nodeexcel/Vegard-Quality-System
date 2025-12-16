"""
Output formatter for Validert system.
Formats analysis results according to the fixed output structure defined in output_structure.txt
"""
from typing import Dict, List, Any, Optional
from datetime import datetime
import logging
from app.services.improvement_rules import get_improvement_rules_processor
from app.services.rule_output_mapping import get_rule_output_mapper

logger = logging.getLogger(__name__)

class OutputFormatter:
    """Format analysis results according to Validert output structure"""
    
    def __init__(self):
        self.rules_processor = get_improvement_rules_processor()
        self.rule_mapper = get_rule_output_mapper()
    
    def format_analysis_output(
        self,
        analysis_data: Dict[str, Any],
        pdf_metadata: Optional[Dict[str, Any]] = None,
        triggered_rules: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Format analysis output according to Validert output structure
        
        Args:
            analysis_data: Raw analysis data from AI
            pdf_metadata: Metadata about PDF (pages, appendices, etc.)
            triggered_rules: List of rule IDs that were triggered
            
        Returns:
            Formatted output according to output_structure.txt
        """
        # Extract metadata
        metadata = self._extract_metadata(analysis_data, pdf_metadata)
        
        # Check if full document analysis was completed
        full_document_analysis = metadata.get("full_document_analysis", False)
        
        # If full document not analyzed, return only metadata
        if not full_document_analysis:
            logger.warning("Full document analysis not completed - returning metadata only")
            return {
                "metadata": metadata,
                "error": "Full dokumentanalyse ikke gjennomført. Ingen trygghetsscore eller forbedringsliste kan genereres."
            }
        
        # Extract executive summary
        executive_summary = self._extract_executive_summary(analysis_data)
        
        # Process triggered rules and determine blockers
        blockers = self._get_blockers(triggered_rules or [])
        blocks_score_96 = len(blockers) > 0
        
        # Calculate trygghetsscore (only after all rules evaluated)
        trygghetsscore = self._calculate_trygghetsscore(analysis_data, blocks_score_96)
        
        # Build sperrer list
        sperrer_list = self._build_sperrer_list(blockers)
        
        # Build forbedringsliste
        forbedringsliste = self._build_forbedringsliste(analysis_data, triggered_rules or [])
        
        # Extract faglige kommentarer
        faglige_kommentarer = self._extract_faglige_kommentarer(analysis_data)
        
        # Build rettssaksvurdering
        rettssaksvurdering = self._build_rettssaksvurdering(analysis_data, triggered_rules or [])
        
        # Build avsluttende veiledning
        avsluttende_veiledning = self._build_avsluttende_veiledning(blockers)
        
        return {
            "metadata": metadata,
            "executive_summary": executive_summary,
            "trygghetsscore": trygghetsscore,
            "sperrer_96": sperrer_list,
            "forbedringsliste": forbedringsliste,
            "faglige_kommentarer": faglige_kommentarer,
            "rettssaksvurdering": rettssaksvurdering,
            "avsluttende_veiledning": avsluttende_veiledning,
            "bekreftelse_analyseomfang": "Denne analysen er basert på gjennomgang av hele tilstandsrapporten, inkludert alle sider, vedlegg og bildemateriale."
        }
    
    def _extract_metadata(self, analysis_data: Dict, pdf_metadata: Optional[Dict]) -> Dict[str, Any]:
        """Extract metadata section"""
        if pdf_metadata:
            return {
                "pages_analyzed": pdf_metadata.get("total_pages", 0),
                "appendices_analyzed": pdf_metadata.get("appendices_estimated", 0),
                "full_document_analysis": pdf_metadata.get("full_document_available", False),
                "analysis_id": analysis_data.get("analysis_id", ""),
                "timestamp": datetime.utcnow().isoformat()
            }
        
        # Try to extract from analysis_data
        metadata_section = analysis_data.get("metadata", {})
        return {
            "pages_analyzed": metadata_section.get("pages_analyzed", 0),
            "appendices_analyzed": metadata_section.get("appendices_analyzed", 0),
            "full_document_analysis": metadata_section.get("full_document_analysis", True),
            "analysis_id": metadata_section.get("analysis_id", ""),
            "timestamp": metadata_section.get("timestamp", datetime.utcnow().isoformat())
        }
    
    def _extract_executive_summary(self, analysis_data: Dict) -> str:
        """Extract executive summary (2-5 short sentences)"""
        # Try to get from analysis_data
        if "executive_summary" in analysis_data:
            return analysis_data["executive_summary"]
        
        # Fallback: generate from overall_assessment
        overall = analysis_data.get("overall_assessment", {})
        summary = overall.get("summary", "")
        if summary:
            # Limit to 2-5 sentences
            sentences = summary.split('.')
            return '. '.join(sentences[:5]).strip()
        
        return "Rapporten er analysert. Se detaljer nedenfor."
    
    def _get_blockers(self, triggered_rules: List[str]) -> List[str]:
        """Get list of rules that block score ≥96"""
        blockers = []
        for rule_id in triggered_rules:
            if self.rule_mapper.should_block_score_96(rule_id):
                rule = self.rules_processor.get_rule(rule_id)
                if rule:
                    blockers.append(rule.get("message", rule_id))
        return blockers
    
    def _calculate_trygghetsscore(self, analysis_data: Dict, blocks_score_96: bool) -> Dict[str, Any]:
        """
        Calculate trygghetsscore (ONLY after all rules evaluated)
        If blocks_score_96 is True, score cannot be ≥96
        """
        # Get score from analysis_data - NEW FORMAT: trygghetsscore field first (from new system prompt)
        total_score = 0
        
        # First, try the new format: trygghetsscore.score (from new system prompt)
        trygghetsscore_data = analysis_data.get("trygghetsscore", {})
        if isinstance(trygghetsscore_data, dict):
            total_score = trygghetsscore_data.get("score", 0)
            if total_score > 0:
                logger.info(f"Found score in trygghetsscore field: {total_score}")
        
        # If not found, try old format: scores.total_score
        if total_score == 0:
            scores = analysis_data.get("scores", {})
            total_score = scores.get("total_score", 0)
            if total_score > 0:
                logger.info(f"Found score in scores.total_score: {total_score}")
        
        # If still 0, try to calculate from sub-scores
        if total_score == 0:
            scores = analysis_data.get("scores", {})
            sub_scores = [
                scores.get("forskrift_score", 0),
                scores.get("ns3600_score", 0),
                scores.get("ns3940_score", 0),
                scores.get("tek_score", 0),
                scores.get("language_clarity_score", 0),
                scores.get("legal_safety_score", 0)
            ]
            if any(sub_scores):
                # Sum all sub-scores (they should add up to 100)
                total_score = sum(sub_scores)
                logger.info(f"Calculated score from sub-scores: {total_score}")
        
        # If still 0, use a default calculation based on findings
        if total_score == 0:
            findings = analysis_data.get("findings", [])
            if findings:
                # Base score calculation: start at 50, deduct for errors
                total_score = 50
                for finding in findings:
                    severity = finding.get("severity", "info")
                    if severity == "error":
                        total_score -= 10
                    elif severity == "warning":
                        total_score -= 5
                total_score = max(0, min(100, total_score))
                logger.info(f"Calculated score from findings: {total_score}")
        
        # Apply blocker: if any blocker exists, cap score at 95
        if blocks_score_96 and total_score >= 96:
            total_score = 95
            logger.info("Score capped at 95 due to blockers")
        
        # If we got the score from trygghetsscore field, use its explanation
        if isinstance(trygghetsscore_data, dict) and trygghetsscore_data.get("score", 0) > 0:
            explanation = trygghetsscore_data.get("explanation", f"Trygghetsscore på {total_score}/100 uttrykker takstmannens rapportkvalitet og ansvarseksponering.")
            factors_positive = trygghetsscore_data.get("factors_positive", "Ingen spesifikke positive faktorer identifisert")
            factors_negative = trygghetsscore_data.get("factors_negative", "Ingen spesifikke negative faktorer identifisert")
        else:
            # Generate explanation
            explanation = f"Trygghetsscore på {total_score}/100 uttrykker takstmannens rapportkvalitet og ansvarseksponering."
            
            # Determine factors
            factors_positive = []
            factors_negative = []
            
            # Analyze findings to determine factors
            findings = analysis_data.get("findings", [])
            for finding in findings:
                if finding.get("severity") in ["error", "warning"]:
                    factors_negative.append(finding.get("problem", ""))
                elif finding.get("severity") == "info":
                    factors_positive.append(finding.get("problem", ""))
            
            factors_positive = "; ".join(factors_positive[:3]) if factors_positive else "Ingen spesifikke positive faktorer identifisert"
            factors_negative = "; ".join(factors_negative[:3]) if factors_negative else "Ingen spesifikke negative faktorer identifisert"
        
        return {
            "score": total_score,
            "explanation": explanation,
            "factors_positive": factors_positive,
            "factors_negative": factors_negative
        }
    
    def _build_sperrer_list(self, blockers: List[str]) -> List[str]:
        """Build list of blockers (only if any exist)"""
        return blockers if blockers else []
    
    def _build_forbedringsliste(self, analysis_data: Dict, triggered_rules: List[str]) -> List[Dict[str, Any]]:
        """Build complete improvement list"""
        # NEW FORMAT: Check if AI already returned forbedringsliste
        if "forbedringsliste" in analysis_data:
            ai_forbedringsliste = analysis_data.get("forbedringsliste", [])
            if ai_forbedringsliste and isinstance(ai_forbedringsliste, list) and len(ai_forbedringsliste) > 0:
                logger.info(f"Using AI-provided forbedringsliste: {len(ai_forbedringsliste)} items")
                return ai_forbedringsliste
        
        # If not, build from triggered rules
        forbedringsliste = []
        
        # Process triggered rules
        for idx, rule_id in enumerate(triggered_rules, 1):
            rule = self.rules_processor.get_rule(rule_id)
            if not rule:
                continue
            
            # Get improvement category from mapping
            improvement_category = self.rule_mapper.get_improvement_category(rule_id)
            
            # Create forbedringspunkt from rule
            forbedringspunkt = {
                "nummer": len(forbedringsliste) + 1,
                "kategori": improvement_category,
                "hva_er_feil": rule.get("message", rule.get("description", "")),
                "hvor_i_rapporten": "Se rapporttekst",  # Will be filled from findings if available
                "hvorfor_problem": rule.get("description", ""),
                "hva_må_endres": f"Rett {rule.get('message', '')}",
                "konsekvens_ikke_rettet": self._get_consequence(rule_id, improvement_category)
            }
            
            # Try to find related findings to get more context
            related_findings = [
                f for f in analysis_data.get("findings", [])
                if rule_id.lower() in f.get("problem", "").lower() or 
                   rule_id.lower() in f.get("description", "").lower()
            ]
            
            if related_findings:
                finding = related_findings[0]
                forbedringspunkt["hva_er_feil"] = finding.get("problem", forbedringspunkt["hva_er_feil"])
                forbedringspunkt["hvor_i_rapporten"] = (finding.get("component_name", "") + " / " + finding.get("component_type", "")).strip(" / ")
                forbedringspunkt["hva_må_endres"] = finding.get("suggested_fix", finding.get("recommended_text", forbedringspunkt["hva_må_endres"]))
            
            forbedringsliste.append(forbedringspunkt)
        
        # Also process improvement_suggestions from analysis_data (old format)
        improvement_suggestions = analysis_data.get("improvement_suggestions", {})
        for takstmann_item in improvement_suggestions.get("for_takstmann", []):
            if isinstance(takstmann_item, dict):
                forbedringspunkt = {
                    "nummer": len(forbedringsliste) + 1,
                    "kategori": "Vesentlig avvik",
                    "hva_er_feil": takstmann_item.get("issue", ""),
                    "hvor_i_rapporten": "Se rapporttekst",
                    "hvorfor_problem": "Forbedring kreves for økt kvalitet",
                    "hva_må_endres": takstmann_item.get("recommended_text", ""),
                    "konsekvens_ikke_rettet": "Økt reklamasjonsrisiko"
                }
                forbedringsliste.append(forbedringspunkt)
        
        return forbedringsliste
    
    def _get_consequence(self, rule_id: str, category: str) -> str:
        """Get consequence text based on rule and category"""
        if "SPERRE" in category:
            return "Sperre mot trygghetsscore ≥96"
        elif "Vesentlig" in category:
            return "Økt reklamasjonsrisiko"
        else:
            return "Rettssakssårbarhet"
    
    def _extract_faglige_kommentarer(self, analysis_data: Dict) -> Optional[str]:
        """Extract faglige kommentarer (optional)"""
        # Try to get from analysis_data
        if "faglige_kommentarer" in analysis_data:
            return analysis_data["faglige_kommentarer"]
        
        # Fallback: generate from template_assessment
        template = analysis_data.get("template_assessment", {})
        if template.get("issues"):
            return "; ".join(template.get("issues", []))
        
        return None
    
    def _build_rettssaksvurdering(self, analysis_data: Dict, triggered_rules: List[str]) -> Dict[str, Any]:
        """Build rettssaksvurdering section"""
        courtroom = analysis_data.get("courtroom_assessment", {})
        legal_risk = analysis_data.get("legal_risk", {})
        
        # Determine ansvarseksponering
        risk_level = legal_risk.get("risk_level", "moderat")
        if risk_level == "lav":
            ansvarseksponering = "lav"
        elif risk_level == "høy":
            ansvarseksponering = "høy"
        else:
            ansvarseksponering = "moderat"
        
        # Check if any blockers affect legal assessment
        blockers_in_legal = [
            rule_id for rule_id in triggered_rules
            if self.rule_mapper.should_include_in_legal_assessment(rule_id)
        ]
        
        if blockers_in_legal:
            ansvarseksponering = "høy"
        
        return {
            "title": "Hvordan stiller denne i en rettsak?",
            "sterke_sider": courtroom.get("for_takstmann", "") or "Ingen spesifikke sterke sider identifisert",
            "svake_sider": courtroom.get("against_takstmann", "") or "Se forbedringsliste",
            "angrepspunkter": courtroom.get("for_kjøper", "") or legal_risk.get("typical_claim_risks", []),
            "ansvarseksponering": ansvarseksponering,
            "samlet_vurdering": courtroom.get("assessment", "") or legal_risk.get("explanation", "")
        }
    
    def _build_avsluttende_veiledning(self, blockers: List[str]) -> str:
        """Build avsluttende veiledning"""
        if blockers:
            return "Når punktene merket [SPERRE ≥96] er rettet, anbefales ny opplasting for ny vurdering."
        return "Rapporten er analysert. Se forbedringsliste for detaljer."

