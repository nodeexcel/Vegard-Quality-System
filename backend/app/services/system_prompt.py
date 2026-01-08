"""
System prompt for AI analysis of Norwegian building condition reports.
Loaded from the current validated baseline files.
"""

from app.services.validert_files import get_system_prompt

SYSTEM_PROMPT = get_system_prompt()
