"""
Improvement rules loader and processor for Validert system.
Loads and processes improvement_rules.json to evaluate deviations and score blockers.
"""
import json
import os
from typing import Dict, List, Any, Optional
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

# Load improvement rules from JSON file
def load_improvement_rules() -> Dict[str, Any]:
    """Load improvement rules from JSON file"""
    try:
        # Try to load from the provided file path
        rules_file = Path(__file__).parent.parent.parent.parent / "improvment_rules.json.txt"
        
        if not rules_file.exists():
            # Fallback: try in current directory
            rules_file = Path("improvment_rules.json.txt")
        
        if not rules_file.exists():
            logger.warning("improvement_rules.json.txt not found, using default rules")
            return get_default_rules()
        
        with open(rules_file, 'r', encoding='utf-8') as f:
            content = f.read()
            # Extract JSON from text file (remove header/footer if present)
            start = content.find('{')
            end = content.rfind('}') + 1
            if start >= 0 and end > start:
                rules = json.loads(content[start:end])
                logger.info(f"Loaded improvement rules: {len(rules.get('rules', []))} rules")
                return rules
            else:
                logger.error("Could not find JSON in improvement_rules.json.txt")
                return get_default_rules()
    except Exception as e:
        logger.error(f"Error loading improvement rules: {str(e)}")
        return get_default_rules()

def get_default_rules() -> Dict[str, Any]:
    """Default improvement rules if file cannot be loaded"""
    return {
        "version": "1.0",
        "global_rules": {
            "one_deviation_one_item": True,
            "improvement_list_always_required": True,
            "no_generic_text_allowed": True,
            "full_document_required": True
        },
        "categories": {
            "SPERRE_96": {
                "label": "SPERRE ≥96",
                "description": "Avvik som automatisk hindrer trygghetsscore ≥96"
            },
            "VESENTLIG": {
                "label": "Vesentlig avvik",
                "description": "Avvik som trekker betydelig ned trygghetsscore"
            },
            "MINDRE": {
                "label": "Mindre forbedring",
                "description": "Forbedringer som ikke alene hindrer ≥96"
            }
        },
        "rules": [
            {
                "id": "ARKAT_MISSING",
                "description": "Manglende én eller flere ARKAT-deler på TG2/TG3",
                "trigger": {"tg_level": ["TG2", "TG3"], "arkat_complete": False},
                "category": "SPERRE_96",
                "message": "TG2/TG3 mangler full ARKAT (Årsak, Risiko, Konsekvens eller Anbefalt tiltak)."
            },
            {
                "id": "ARKAT_GENERIC",
                "description": "ARKAT er generell og ikke objekttilpasset",
                "trigger": {"arkat_generic": True},
                "category": "SPERRE_96",
                "message": "ARKAT er formulert som generell standardtekst og ikke tilpasset det konkrete forholdet."
            },
            {
                "id": "RECOMMENDED_ACTION_PROJECTING",
                "description": "Anbefalt tiltak er prosjekterende",
                "trigger": {"recommended_action_projecting": True},
                "category": "SPERRE_96",
                "message": "Anbefalt tiltak fremstår prosjekterende og går utover takstmannens rolle."
            },
            {
                "id": "TGIU_MISUSE",
                "description": "Feil eller mangelfull bruk av TGIU",
                "trigger": {"tgiu_used": True, "tgiu_properly_justified": False},
                "category": "SPERRE_96",
                "message": "TGIU er brukt uten tilstrekkelig begrunnelse, risiko og konsekvens."
            },
            {
                "id": "MISSING_MANDATORY_INSPECTION",
                "description": "Manglende forskriftsmessig undersøkelse",
                "trigger": {"mandatory_inspection_missing": True},
                "category": "SPERRE_96",
                "message": "Bygningsdel som skulle vært undersøkt etter forskriften er ikke reelt undersøkt."
            },
            {
                "id": "CONTRADICTION",
                "description": "Motstridende beskrivelser i rapporten",
                "trigger": {"contradictory_statements": True},
                "category": "SPERRE_96",
                "message": "Rapporten inneholder motstridende eller selvmotsigende beskrivelser."
            },
            {
                "id": "WRONG_TG_LEVEL",
                "description": "Feil valg av tilstandsgrad",
                "trigger": {"tg_inconsistent_with_text": True},
                "category": "SPERRE_96",
                "message": "Valgt tilstandsgrad samsvarer ikke med beskrivelsen eller risikoen."
            },
            {
                "id": "UNCLEAR_LANGUAGE",
                "description": "Uklart eller tvetydig språk",
                "trigger": {"unclear_or_vague_language": True},
                "category": "VESENTLIG",
                "message": "Rapporten benytter uklart eller tvetydig språk som svekker kjøpers forståelse."
            },
            {
                "id": "IMPORTANT_INFO_IN_REMARKS",
                "description": "Viktig informasjon gjemt i merknader",
                "trigger": {"critical_info_in_remarks": True},
                "category": "VESENTLIG",
                "message": "Viktig risiko- eller tilstandsinformasjon er plassert i merknader uten tydelig fremheving."
            },
            {
                "id": "INSUFFICIENT_DOCUMENTATION",
                "description": "Manglende eller svak dokumentasjon",
                "trigger": {"documentation_insufficient": True},
                "category": "VESENTLIG",
                "message": "Manglende eller svak dokumentasjon (bilder, målinger, beskrivelser)."
            },
            {
                "id": "MINOR_IMPROVEMENT",
                "description": "Mindre forbedringspunkt",
                "trigger": {"minor_issue_detected": True},
                "category": "MINDRE",
                "message": "Forholdet kan forbedres for økt klarhet og kvalitet, men er ikke alene kritisk."
            }
        ],
        "output_requirements": {
            "include_all_triggered_rules": True,
            "group_by_category": True,
            "reference_report_location": True,
            "explain_consequence_if_not_fixed": True
        }
    }

class ImprovementRulesProcessor:
    """Process improvement rules and evaluate triggers"""
    
    def __init__(self):
        self.rules_data = load_improvement_rules()
        self.rules = {rule["id"]: rule for rule in self.rules_data.get("rules", [])}
        self.categories = self.rules_data.get("categories", {})
    
    def get_rules_by_category(self, category: str) -> List[Dict]:
        """Get all rules in a specific category"""
        return [rule for rule in self.rules_data.get("rules", []) if rule.get("category") == category]
    
    def get_blocker_rules(self) -> List[Dict]:
        """Get all rules that block score ≥96"""
        return self.get_rules_by_category("SPERRE_96")
    
    def get_rule(self, rule_id: str) -> Optional[Dict]:
        """Get a specific rule by ID"""
        return self.rules.get(rule_id)
    
    def get_category_label(self, category_key: str) -> str:
        """Get the display label for a category"""
        category = self.categories.get(category_key, {})
        return category.get("label", category_key)

# Global instance
_improvement_rules_processor = None

def get_improvement_rules_processor() -> ImprovementRulesProcessor:
    """Get the global improvement rules processor instance"""
    global _improvement_rules_processor
    if _improvement_rules_processor is None:
        _improvement_rules_processor = ImprovementRulesProcessor()
    return _improvement_rules_processor

