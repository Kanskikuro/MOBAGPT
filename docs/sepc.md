# Project Specification: League of Legends Draft & Build Recommendation Engine

## Objective

Build a recommendation engine that recommends **champion and build together as a single unit** during draft. A champion's build (runes + itemization) can change its functional identity — the same champion can be an engage threat as an AD assassin and a disengage/poke pick as an AP mage — so recommending a champion without its build context is meaningless. The atomic recommendation unit is a **(champion, build archetype)** pair.

The core philosophy: **train the model to understand League mechanics, not to memorize win rates.** League has too many variables for raw win rate to be a reliable signal in isolation — a good draft played badly still produces a loss. Statistical data (win rates, matchup deltas, build statistics) is used **only for training and score calibration**, never as the direct basis for a recommendation at inference time. OTP builds serve as a discovery signal, since one-tricks are usually the pioneers of new builds; general statistics serve as the supervision signal for training.

---

## Step 0: Audit SKADZALGORITM (required before writing any code)

An existing project named **SKADZALGORITM** exists in the same parent directory and contains substantial prior work on champion recommendation.

Before implementing anything, produce a written audit report (`docs/skadz_audit.md`) covering:

* Overall architecture and module layout
* Data sources used, data formats, and any existing datasets that can be reused
* Existing feature engineering (composition features, matchup features, etc.)
* Existing scoring/recommendation logic and its known limitations
* A concrete list of components to **reuse**, components to **adapt**, and components to **rebuild**, each with a one-line justification

Do not begin implementation until this audit exists. The new repository should improve upon SKADZALGORITM, not recreate it, and it should not silently duplicate functionality that already works.

---

## Phased Delivery

### Phase 1 — Data pipelines and databases

Build the complete data foundation before any modeling. Deliverables:

* **Knowledge database** populated: champions, abilities, items, runes, mechanics, semantic tags, numeric ratings
* **Statistical database** populated for the current patch: win/pick/ban rates, matchup statistics, synergy/counter matrices, rune/item/skill-order statistics, build paths, game-duration splits
* **OTP database** populated from OneTricks.gg: high-ELO one-trick builds, item order, rune pages, skill order, starting items, match histories, situational choices
* **Build archetype extraction:** for every champion, cluster observed builds (from statistical + OTP data) into named archetypes (e.g. "AD assassin", "AP mage", "bruiser", "tank"), each linked to the semantic tags and functional profile that the archetype gives the champion. This is what makes (champion, build) the recommendation unit possible.
* Every pipeline is re-runnable per patch with one command, idempotent, and logs sample sizes
* **Done when:** all three databases are populated for the current patch, build archetypes exist for every viable champion+role, and a data-quality report (`docs/data_report.md`) documents coverage, gaps, and sample sizes

### Phase 2 — Recommendation model

Design and train the model that consumes the knowledge database as its input representation and the statistical database as its training signal. Deliverables:

* Joint (champion, build archetype) recommendation for a draft state
* Stateless re-scoring on every draft event, and a build re-evaluation mode for locked champions that reports when a previously recommended build no longer holds (see model section)
* Rune page and full itemization output derived from the recommended archetype, with situational swaps
* Explanation generation traceable to the model's actual reasoning features
* Evaluation harness comparing against the statistical baseline
* **Done when:** the system takes a draft state as JSON and returns ranked (champion, build) recommendations with runes, items, and explanations, and the evaluation report exists

Later goals (live champ-select integration, in-game item recommendations, draft simulation, pick/ban optimization, RL) remain out of scope but must not be precluded by the architecture.

---

## Recommendation Model (Phase 2) — Concrete Formulation

**Input:** a draft state object. `target_role` is optional — the system must be able to draft for **every lane**. If `target_role` is provided, recommendations are produced for that role only; if omitted, recommendations are produced for all unfilled ally roles.

```json
{
  "patch": "16.13",
  "target_role": "top",
  "ally_picks": [{"champion": "Vi", "role": "jungle"}],
  "enemy_picks": [{"champion": "Zed", "role": "mid"}, {"champion": "Draven", "role": "bot"}],
  "bans": ["Aatrox", "Ksante", "Yone", "Hwei", "Kalista"],
  "pick_position": 4
}
```

**Output:** ranked (champion, build) recommendations, grouped per role:

```json
{
  "recommendations": {
    "top": [
      {
        "champion": "Ornn",
        "build_archetype": "tank",
        "confidence": 0.78,
        "runes": { "...": "..." },
        "core_items": ["..."],
        "skill_order": ["..."],
        "explanation": "Your team lacks frontline and engage; the enemy has two immobile carries vulnerable to Ornn's ultimate. Draven/Zed is a mixed damage profile, so this build itemizes hybrid resists rather than a standard armor stack.",
        "situational": [{"item": "...", "when": "..."}]
      }
    ]
  }
}
```

The model itself is role-agnostic: `f(draft_state, role, candidate) → score`. Drafting for all lanes is the same model queried once per unfilled role, with role-conditioned candidate sets and role-specific matchup features (the lane opponent differs per role). There is one model, not five per-role models — role is an input, which lets flex picks (champions viable in multiple roles) be scored honestly in each role they can fill.

### Candidate space

Candidates are (champion, build archetype) pairs, not champions. The same champion appears multiple times in the candidate set with different functional profiles, and flex champions appear in the candidate set of every role they can fill. Filters: bans, picked champions, and role viability for the role being scored.

### Draft evolution and build adaptation

A recommendation is a snapshot, not a commitment. The draft changes after every pick, and a build that was correct three picks ago can be invalidated by what the enemy locked since — the AP build planned into a squishy comp stops working when the enemy stacks MR, the assassin variant stops working when they add layered peel. The system must adapt, and it does so through two properties:

1. **Stateless re-scoring on every draft event.** The model holds no draft session state; every new pick or ban produces a new draft state object, and the system is simply re-queried. Recommendations for unfilled roles are recomputed from scratch each time. This is not an extra feature — it falls out of the `f(draft_state, role, candidate) → score` formulation — but the spec makes it explicit so nothing in the implementation caches or assumes a frozen draft.

2. **Build re-evaluation for locked champions.** Once an ally champion is locked, its *build* remains an open decision until the game starts. The API supports a second query mode: given the current draft state and a locked (champion, role), re-rank that champion's build archetypes against the updated draft.

```json
{
  "mode": "reevaluate_build",
  "locked": {"champion": "Katarina", "role": "mid"},
  "previous_recommendation": {"build_archetype": "ap_burst"},
  "...": "same draft state fields as above"
}
```

The response re-ranks all of the champion's archetypes and, when `previous_recommendation` is supplied, explicitly reports whether it still holds:

```json
{
  "build_recommendations": ["..."],
  "previous_build_status": "invalidated",
  "reason": "Enemy locked two MR-stacking frontliners since this build was recommended; the AP burst profile now lacks a target it can threaten. The hybrid on-hit archetype scores higher against this final composition."
}
```

`previous_build_status` is one of `holds` / `weakened` / `invalidated`, determined by thresholds on the score gap between the previous archetype and the new best archetype (thresholds live in config like everything else). No new model is needed for any of this — it is the same scorer restricted to one champion's archetype candidates — but the invalidation reporting must exist as a first-class output, because "your plan stopped working and here is why" is exactly the kind of reasoning this project is about.

This also feeds back into pick recommendations: because candidates are (champion, build) pairs, a champion whose *best* archetype gets invalidated by a new enemy pick automatically drops in the pick ranking on the next re-score if its remaining archetypes score worse. Adaptation of picks and adaptation of builds are the same mechanism.

### Representation: knowledge in, statistics out of the loop

The model's **input features come exclusively from the knowledge database**: champion ability properties, semantic tags, numeric ratings, and the functional profile of the build archetype (the stats, passives, and tags its items and runes provide), combined into draft-level features (damage profile split, CC, engage, disengage, peel, frontline, scaling curves, wave clear, objective control).

Win rates, pick rates, and matchup statistics are **not input features**. They are used in two places only:

1. **Training labels / supervision:** the model is trained to predict outcome-derived targets (matchup-adjusted performance of a (champion, build) in a composition context) computed from the statistical database.
2. **Score calibration:** mapping raw model outputs to calibrated confidence scores.

This forces the model to learn *why* a pick+build works from mechanics, so it can generalize to compositions and build variations it has never seen in aggregate statistics — which pure win-rate lookup cannot do.

### Model architecture

The task is **pointwise learning-to-rank**: a model `f(draft_state, candidate) → score` trained by regression against matchup-adjusted performance targets, with candidates sorted by score at inference. Two model versions, built in order; v0 is not optional.

**Model v0 — gradient-boosted trees (LightGBM) on engineered features.**
The first model is a GBDT over a flat feature vector, because it trains in minutes, needs little tuning, handles heterogeneous features natively, and gives feature importances for free (which feed explanation generation directly). Feature vector per (draft_state, candidate) pair:

* **Candidate features:** the (champion, build) functional profile from the knowledge DB — numeric ratings (engage, disengage, frontline, peel, scaling curve, wave clear, burst, sustained DPS, mobility, CC score) after applying the archetype's deltas, damage-type split, range class, resource type
* **Team-need features:** for each rating, the gap between a target profile and the current ally team's aggregate (e.g. `ally_frontline_deficit`, `ally_engage_deficit`, `ally_ap_ratio`)
* **Opposition features:** enemy aggregate profile crossed with candidate strengths (e.g. `enemy_dive_score × candidate_peel`, `enemy_healing × candidate_antiheal`, `enemy_armor_stack × candidate_pen`), lane-opponent mechanical interaction features (range differential, all-in vs. sustain profile, gank setup vs. escape)
* **Context features:** pick position, number of remaining enemy picks (information uncertainty), patch-normalized scaling context (expected game length proxy from both compositions' scaling curves)

All features derive from the knowledge DB only. v0 is the model that must beat the statistical baseline before anything fancier is justified; if it cannot, the problem is in the features or targets, and a neural model will not fix that.

**Model v1 — two-tower neural ranker with set attention.**
Built only after v0 works, to remove v0's main limitation: hand-crossed interaction features.

* **Candidate tower:** embeds a (champion, build) pair — learned champion embedding initialized from knowledge-DB feature vectors, concatenated with the archetype's functional-profile vector, passed through an MLP to a candidate vector
* **Context tower:** encodes the draft state with permutation-invariant set attention (a small transformer encoder, no positional encoding within teams): ally picks and enemy picks as two token sets built from the same champion+build embedding space, plus learned tokens for role slots still unfilled (so partial information is represented, not zero-padded), plus scalar context features (pick position, patch embedding)
* **Scoring head:** cross-attention from the candidate vector over the context tokens, then an MLP producing the scalar score. Cross-attention weights double as attribution for explanations (which enemy/ally picks drove the score)
* **Training:** regression on the same aggregate targets as v0 (Huber loss), sample-size weighting, dropout on context tokens as augmentation (simulating earlier draft states from later ones — one real draft yields several partial-information training examples)
* **Calibration:** isotonic regression on a held-out split maps raw scores to the reported confidence

**Shared training details (both versions):**

* **Target:** matchup- and composition-conditioned performance rate from the statistical DB, shrunk toward the global mean by sample size (empirical-Bayes shrinkage) so thin cells do not produce extreme targets
* **Splits:** split by patch (train on older, validate on newer) and hold out entire (champion, build) combinations for the generalization probes — never random row-level splits, which leak
* **Model selection:** NDCG@10 on the validation split, with the generalization-probe NDCG reported alongside; a v1 that beats v0 on standard drafts but loses on probes has memorized, not learned, and is rejected

**Explicit non-choices for now:** no LLM in the scoring loop (only optional explanation polish), no RL, no per-player personalization (champion-pool filtering can be added later as a trivial post-filter on the ranked output — the model does not need to know about it). Revisit after v1 is evaluated.

Win/loss is a noisy label: good drafts are lost through execution, bad drafts are won through skill gaps. Mitigations, all mandatory:

* Train on **aggregated targets** (matchup- and composition-conditioned rates over many games), never single-game outcomes
* Weight every training sample by its underlying sample size
* Restrict training data to high-ELO games where execution variance is lower
* Where rank data is available, control for rating differential between teams

### Role of OTP data

OTP builds are the **discovery and pioneering signal**: they surface build archetypes and situational deviations before they appear in aggregate statistics with meaningful sample sizes. Use OTP data to (a) seed and validate the build-archetype clustering, (b) label situational triggers ("this OTP swaps to item X into heavy healing"), and (c) provide high-quality low-sample builds that the model evaluates through its mechanical understanding rather than through their (still-thin) statistics. OTP data complements statistics; it never overrides them blindly, and it is weighted by the player's sample size and consistency.

### Explanation generation

Explanations are generated from the model's actual feature attributions (which mechanical features drove the ranking), rendered via templates with an optional LLM-polish pass. An explanation must be traceable to computed feature contributions; never generate a rationale the model did not use.

---

## Evaluation

Evaluation is inherently difficult here: perfect ground truth would require observing identical drafts with identical builds, which is too rare to rely on. The evaluation strategy is therefore layered, from mandatory to opportunistic:

1. **Statistical baseline (mandatory, the primary benchmark):** for the given role and matchup, recommend the (champion, build) with the highest matchup-adjusted win rate from the statistical database. The model must be compared against this baseline on every evaluation run. Metrics on held-out high-ELO drafts with the target pick masked: Top-K agreement (does the actually-picked champion, in won games, appear in the top K), MRR, NDCG@10.
2. **Generalization probes (mandatory):** hold out entire (champion, build archetype) combinations or matchups from training and test whether the model ranks them sensibly from mechanics alone. This is the test the baseline cannot pass by construction, and it is the direct measure of whether the model learned mechanics rather than statistics.
3. **Matched-game analysis (opportunistic, not required):** where near-identical draft+build games can be found in the match corpus, compare outcomes for recommended vs. non-recommended picks. Report it when sample sizes allow; do not block on it.
4. **Explanation faithfulness (qualitative):** spot-check that explanations match the model's feature attributions.

Produce `docs/evaluation.md` on every run. If the model does not beat the baseline on standard drafts but wins on generalization probes, report both plainly — that pattern is itself informative. An unmeasured claim of superiority is not acceptable.

---

## Component 1: Knowledge Database

Static game knowledge. Changes only on major patches.

* Champions: base stats, growth stats, roles, damage type, range, resource, difficulty, tags
* Abilities: description, damage, scaling, cooldown, range, cost, CC, shielding, healing, passive effects
* Items: cost, stats, components, build path, passives, actives
* Runes: description, tree, effects, cooldowns, scaling
* Mechanics reference: armor/magic pen math, damage formulas, tenacity, crit, ability haste, lifesteal/omnivamp
* **Build archetypes:** per champion+role, each archetype with its constituent items/runes and the functional profile (tags + numeric deltas) it confers

**Semantic tags** (burst, DPS, tank, engage, disengage, peel, sustain, wave clear, poke, execute, reset, mobility, anti-heal, anti-tank, etc.) are derived by a defined pipeline: LLM extraction from ability/item/rune text into a fixed tag taxonomy, followed by a manually reviewed override file (`data/tag_overrides.yaml`) that always wins. Tags must be reproducible from the pipeline, not hand-entered ad hoc. Crucially, tags apply at the **(champion, build)** level where the build changes the profile — the archetype's item/rune tags modify the champion's effective tags.

Numeric per-champion and per-archetype ratings (engage 0–10, frontline 0–10, scaling curve, etc.) follow the same pipeline + override pattern.

---

## Component 2: Statistical Database

Patch-dependent, refreshed each patch: win/pick/ban rates by role, lane matchup statistics, synergy and counter matrices, rune/item/skill-order win rates, build paths, game-duration splits.

**Usage boundary (enforced in code):** the model inference path has no read access to this database. Only the training pipeline, calibration step, and evaluation harness may query it. This boundary is what guarantees the model recommends from mechanics.

**Patch policy:** every statistical row is keyed by patch. When sample size for a matchup is below a threshold, fall back to an exponentially decayed blend of recent patches and record the effective sample size. Data older than N patches (configurable, default 3) is excluded from training targets but retained for patch-comparison analysis.

---

## Component 3: OTP Database

High-ELO one-trick data from OneTricks.gg: item order, rune pages, skill order, starting items, situational choices, match history, weighted by player sample size and win rate. Feeds build-archetype discovery and situational-trigger labeling as described in the model section.

---

## Data Sources (in priority order)

1. **Riot Games API** — raw match data, timelines, rank data. Official, legal, and the source for training-target computation and held-out evaluation drafts. Register for a development key; design ingestion around its rate limits from day one.
2. **Data Dragon / Community Dragon** — static champion, item, and rune data in clean JSON, plus assets. Primary source for the knowledge DB.
3. **League wiki (leagueoflegends.fandom.com)** — supplementary ability details and formulas that Data Dragon omits (exact scalings, hidden mechanics). Parse defensively; wiki markup is inconsistent.
4. **Lolalytics** — aggregated matchup/build statistics. No public API; check ToS and robots.txt before any automated collection, prefer computing equivalent aggregates from Riot API match data yourself, and treat any scraper as a fragile last resort behind an interface so it can be swapped out.
5. **OneTricks.gg** — same ToS caveat; isolate behind its own ingestion module.

Every external source sits behind its own ingestion module with a common interface, so a source can be replaced without touching the model.

---

## Database

Relational, SQLite initially, normalized schema designed for later PostgreSQL migration.

Tables (minimum): `patches`, `champions`, `champion_stats`, `champion_tags`, `champion_ratings`, `champion_abilities`, `items`, `item_tags`, `item_effects`, `runes`, `rune_tags`, `build_archetypes`, `archetype_items`, `archetype_runes`, `archetype_tags`, `matchup_statistics`, `champion_synergy`, `champion_counters`, `otp_builds`, `otp_players`, `matches`, `evaluation_runs`.

All statistical tables carry `patch_id` and `sample_size` columns. Migrations are versioned (e.g. Alembic or plain numbered SQL files).

---

## Repository Structure & Engineering Standards

```
/ingestion      # one module per data source, common interface
/db             # schema, migrations, query layer
/knowledge      # tag/rating pipeline, build archetype extraction, overrides
/features       # draft-state + (champion, build) feature extraction
/training       # target computation from statistical DB, training loop, calibration
/model          # inference: knowledge features only
/explain        # explanation generation from feature attributions
/eval           # baseline, metrics, generalization probes, evaluation harness
/api            # thin interface: draft state JSON in, recommendations out
/docs           # skadz_audit.md, data_report.md, evaluation.md, architecture.md
```

* Python 3.11+, type hints throughout, `pyproject.toml`, pytest for anything with logic in it
* Config (thresholds, patch policy, training hyperparameters) in one place, never hard-coded
* The statistics-only-in-training boundary is enforced structurally: `/model` has no import path to the statistical DB query layer
* Clear separation between ingestion, storage, feature engineering, training, inference, and presentation

---

## Design Philosophy (now enforced by architecture)

Do **not** recommend:

> Champion X has a 53% win rate.

Instead:

> Champion X on its AP mage build is recommended because your team lacks disengage, the enemy is a hard-engage dive composition, and this build turns X from an engage assassin into a zone-control mage that punishes their commitment.

The difference is not phrasing — it is that the second recommendation comes from a model that reasons over mechanics (what the champion's abilities and this build's items actually do in this composition), trained on statistics but not looking them up. Win rate is the teacher, not the answer key at exam time.