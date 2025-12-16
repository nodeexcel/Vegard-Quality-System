"""
Rule-output mapping for Validert system.
Controls where findings appear in output and what blocks score ≥96.
"""
import json
from typing import Dict, List, Any, Optional
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

def load_rule_mapping() -> Dict[str, Any]:
    """Load rule-output mapping from JSON file"""
    try:
        # Try to load from the provided file path
        mapping_file = Path(__file__).parent.parent.parent.parent / "rule_output_maping.json.txt"
        
        if not mapping_file.exists():
            # Fallback: try in current directory
            mapping_file = Path("rule_output_maping.json.txt")
        
        if not mapping_file.exists():
            logger.warning("rule_output_maping.json.txt not found, using default mapping")
            return get_default_mapping()
        
        with open(mapping_file, 'r', encoding='utf-8') as f:
            content = f.read()
            # Extract JSON from text file
            start = content.find('{')
            end = content.rfind('}') + 1
            if start >= 0 and end > start:
                mapping = json.loads(content[start:end])
                logger.info(f"Loaded rule mapping: {len(mapping.get('rule_mappings', []))} mappings")
                return mapping
            else:
                logger.error("Could not find JSON in rule_output_maping.json.txt")
                return get_default_mapping()
    except Exception as e:
        logger.error(f"Error loading rule mapping: {str(e)}")
        return get_default_mapping()

def get_default_mapping() -> Dict[str, Any]:
    """Default rule-output mapping if file cannot be loaded"""
    return {
        "version": "1.0",
        "output_sections": {
            "SUMMARY": "Kort sammendrag",
            "SCORE": "Trygghetsscore",
            "SPERRE_LIST": "Forhold som hindrer trygghetsscore ≥96",
            "IMPROVEMENT_LIST": "Hva må forbedres før rapporten kan godkjennes",
            "LEGAL_ASSESSMENT": "Hvordan stiller denne i en rettsak?"
        },
        "rule_mappings": [
            {
                "rule_id": "ARKAT_MISSING",
                "output": {
                    "sections": ["SPERRE_LIST", "IMPROVEMENT_LIST"],
                    "improvement_category": "SPERRE ≥96",
                    "requires_improvement_item": True,
                    "blocks_score_96": True,
                    "include_in_legal_assessment": True
                }
            },
            {
                "rule_id": "ARKAT_GENERIC",
                "output": {
                    "sections": ["SPERRE_LIST", "IMPROVEMENT_LIST"],
                    "improvement_category": "SPERRE ≥96",
                    "requires_improvement_item": True,
                    "blocks_score_96": True,
                    "include_in_legal_assessment": True
                }
            },
            {
                "rule_id": "RECOMMENDED_ACTION_PROJECTING",
                "output": {
                    "sections": ["SPERRE_LIST", "IMPROVEMENT_LIST"],
                    "improvement_category": "SPERRE ≥96",
                    "requires_improvement_item": True,
                    "blocks_score_96": True,
                    "include_in_legal_assessment": True
                }
            },
            {
                "rule_id": "TGIU_MISUSE",
                "output": {
                    "sections": ["SPERRE_LIST", "IMPROVEMENT_LIST"],
                    "improvement_category": "SPERRE ≥96",
                    "requires_improvement_item": True,
                    "blocks_score_96": True,
                    "include_in_legal_assessment": True
                }
            },
            {
                "rule_id": "MISSING_MANDATORY_INSPECTION",
                "output": {
                    "sections": ["SPERRE_LIST", "IMPROVEMENT_LIST", "LEGAL_ASSESSMENT"],
                    "improvement_category": "SPERRE ≥96",
                    "requires_improvement_item": True,
                    "blocks_score_96": True,
                    "include_in_legal_assessment": True
                }
            },
            {
                "rule_id": "CONTRADICTION",
                "output": {
                    "sections": ["SPERRE_LIST", "IMPROVEMENT_LIST"],
                    "improvement_category": "SPERRE ≥96",
                    "requires_improvement_item": True,
                    "blocks_score_96": True,
                    "include_in_legal_assessment": True
                }
            },
            {
                "rule_id": "WRONG_TG_LEVEL",
                "output": {
                    "sections": ["SPERRE_LIST", "IMPROVEMENT_LIST"],
                    "improvement_category": "SPERRE ≥96",
                    "requires_improvement_item": True,
                    "blocks_score_96": True,
                    "include_in_legal_assessment": True
                }
            },
            {
                "rule_id": "UNCLEAR_LANGUAGE",
                "output": {
                    "sections": ["IMPROVEMENT_LIST"],
                    "improvement_category": "Vesentlig avvik",
                    "requires_improvement_item": True,
                    "blocks_score_96": False,
                    "include_in_legal_assessment": False
                }
            },
            {
                "rule_id": "IMPORTANT_INFO_IN_REMARKS",
                "output": {
                    "sections": ["IMPROVEMENT_LIST"],
                    "improvement_category": "Vesentlig avvik",
                    "requires_improvement_item": True,
                    "blocks_score_96": False,
                    "include_in_legal_assessment": False
                }
            },
            {
                "rule_id": "INSUFFICIENT_DOCUMENTATION",
                "output": {
                    "sections": ["IMPROVEMENT_LIST"],
                    "improvement_category": "Vesentlig avvik",
                    "requires_improvement_item": True,
                    "blocks_score_96": False,
                    "include_in_legal_assessment": False
                }
            },
            {
                "rule_id": "MINOR_IMPROVEMENT",
                "output": {
                    "sections": ["IMPROVEMENT_LIST"],
                    "improvement_category": "Mindre forbedring",
                    "requires_improvement_item": True,
                    "blocks_score_96": False,
                    "include_in_legal_assessment": False
                }
            }
        ]
    }

class RuleOutputMapper:
    """Map triggered rules to output sections"""
    
    def __init__(self):
        self.mapping_data = load_rule_mapping()
        self.rule_mappings = {
            mapping["rule_id"]: mapping["output"]
            for mapping in self.mapping_data.get("rule_mappings", [])
        }
    
    def get_output_config(self, rule_id: str) -> Optional[Dict[str, Any]]:
        """Get output configuration for a rule"""
        return self.rule_mappings.get(rule_id)
    
    def should_block_score_96(self, rule_id: str) -> bool:
        """Check if a rule blocks score ≥96"""
        config = self.get_output_config(rule_id)
        return config.get("blocks_score_96", False) if config else False
    
    def get_improvement_category(self, rule_id: str) -> str:
        """Get improvement category for a rule"""
        config = self.get_output_config(rule_id)
        return config.get("improvement_category", "Mindre forbedring") if config else "Mindre forbedring"
    
    def get_output_sections(self, rule_id: str) -> List[str]:
        """Get output sections where a rule should appear"""
        config = self.get_output_config(rule_id)
        return config.get("sections", ["IMPROVEMENT_LIST"]) if config else ["IMPROVEMENT_LIST"]
    
    def should_include_in_legal_assessment(self, rule_id: str) -> bool:
        """Check if a rule should be included in legal assessment"""
        config = self.get_output_config(rule_id)
        return config.get("include_in_legal_assessment", False) if config else False
    
    def requires_improvement_item(self, rule_id: str) -> bool:
        """Check if a rule requires an improvement item"""
        config = self.get_output_config(rule_id)
        return config.get("requires_improvement_item", True) if config else True

# Global instance
_rule_output_mapper = None

def get_rule_output_mapper() -> RuleOutputMapper:
    """Get the global rule output mapper instance"""
    global _rule_output_mapper
    if _rule_output_mapper is None:
        _rule_output_mapper = RuleOutputMapper()
    return _rule_output_mapper

