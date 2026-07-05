"""Deterministic rule tables for knowledge/archetypes/ build-archetype
extraction (docs/sepc.md Component 1). Kept separate from
config/taxonomy.py, which defines the tag/rating *vocabulary* itself - these
tables are archetype-*derivation* rules built on top of that vocabulary.
"""

from __future__ import annotations

# Item.stats keys that signal AD vs. AP itemization - the only place damage
# type is computed anywhere in this project; config.taxonomy.SEMANTIC_TAGS
# has no AD/AP tag of its own.
AD_STAT_KEYS: frozenset[str] = frozenset({"FlatPhysicalDamageMod", "PercentPhysicalDamageMod"})
AP_STAT_KEYS: frozenset[str] = frozenset({"FlatMagicDamageMod", "PercentMagicDamageMod"})

# Maps each config.taxonomy.RATING_NAMES entry to the SEMANTIC_TAGS whose
# presence-fraction drives its delta. A compact 10-entry table rather than a
# dense matrix, since 7 of the 10 rating names already share an exact name
# with a semantic tag; a leading "-" means the tag contributes negatively.
RATING_TAG_MAP: dict[str, tuple[str, ...]] = {
    "engage": ("engage",),
    "disengage": ("disengage",),
    "frontline": ("tank",),
    "peel": ("peel",),
    "wave_clear": ("wave_clear",),
    "burst": ("burst",),
    "sustained_dps": ("sustained_dps",),
    "mobility": ("mobility",),
    "cc_score": ("cc_heavy",),
    "scaling_curve": ("scaling", "-early_game"),
}

# BuildArchetype.name: keyed by the cluster's single highest-fraction tag
# (only tags listed here are eligible - config.taxonomy.SEMANTIC_TAGS has
# several tags with no natural archetype-name reading, e.g. "reset"). "{damage}"
# is substituted with "AD"/"AP"/"" (empty when damage_label is None).
ARCHETYPE_NAME_BY_TAG: dict[str, str] = {
    "tank": "Tank",
    "engage": "{damage} Engage",
    "disengage": "{damage} Disengage",
    "peel": "{damage} Peel",
    "burst": "{damage} Burst",
    "sustained_dps": "{damage} DPS",
    "wave_clear": "{damage} Wave Clear",
    "poke": "{damage} Poke",
    "split_push": "{damage} Split Push",
    "duelist": "{damage} Duelist",
    "utility": "Utility",
    "healing": "Utility",
}

DEFAULT_ARCHETYPE_NAME = "{damage} Generalist"
