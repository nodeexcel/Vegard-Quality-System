#!/usr/bin/env python3
"""
Export the building-part whitelist used for segment validation.
Run from backend: python -m scripts.export_whitelist

Output: sorted list of all whitelisted names (and blocklist) for client review.
"""
import json
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.validert_files import get_building_part_whitelist


def main():
    wl = get_building_part_whitelist()
    names = sorted(wl.get("names") or set())
    blocklist = sorted(wl.get("blocklist") or set())

    print("=" * 60)
    print("BUILDING-PART WHITELIST (Segment Validation)")
    print("=" * 60)
    print()
    print("BLOCKLIST (excluded even if title matches):")
    print("-" * 40)
    for b in blocklist:
        print(f"  {b}")
    print()
    print("WHITELIST NAMES (segment title must match one of these):")
    print("-" * 40)
    for n in names:
        print(f"  {n}")
    print()
    print(f"Total whitelist entries: {len(names)}")
    print(f"Total blocklist entries: {len(blocklist)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
