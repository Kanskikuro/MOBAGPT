"""Champion name normalization, adapted from SKADZALGORITM's
champ_rec/core/champion_resolver.py (audit: docs/skadz_audit.md, rated
'Reuse'). Other ingestion sources (Lolalytics, OneTricks.gg) will need to
resolve champion identity the same way, so this lives at ingestion level
rather than inside ingestion/data_dragon specifically.
"""

from __future__ import annotations


def normalize_filename(display_name: str) -> str:
    """
    "Lee Sin" -> "lee_sin"
    "Dr. Mundo" -> "dr_mundo"
    "Nunu & Willump" -> "nunu_willump"
    "Kai'Sa" -> "kaisa"
    """
    return (
        str(display_name)
        .lower()
        .strip()
        .replace("'", "")
        .replace(".", "")
        .replace(" & ", " ")
        .replace("&", "")
        .replace(" ", "_")
        .replace("-", "_")
    )
