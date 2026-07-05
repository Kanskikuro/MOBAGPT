"""Fixed vocabulary for the knowledge/ semantic tag + numeric rating
pipeline (docs/sepc.md Component 1). Extraction and validation code must
import these rather than hard-code tag/rating strings (CLAUDE.md hard rule).

`RATING_NAMES` is lifted directly from docs/sepc.md's Model v0 feature-vector
list (engage, disengage, frontline, peel, scaling curve, wave clear, burst,
sustained DPS, mobility, CC score) - not a guess, since that's the actual
consumer of these values.
"""

from __future__ import annotations

SEMANTIC_TAGS: frozenset[str] = frozenset({
    "burst", "sustained_dps", "tank", "engage", "disengage", "peel",
    "sustain", "wave_clear", "poke", "execute", "reset", "mobility",
    "anti_heal", "anti_tank", "cc_heavy", "split_push", "pick_potential",
    "scaling", "early_game", "teamfight", "duelist", "utility", "shield",
    "healing",
})

# All 0-10. "scaling_curve": 0 = hyper-early/snowball, 10 = hyper-late-game.
RATING_NAMES: tuple[str, ...] = (
    "engage", "disengage", "frontline", "peel", "wave_clear", "burst",
    "sustained_dps", "mobility", "cc_score", "scaling_curve",
)

RATING_MIN = 0.0
RATING_MAX = 10.0
