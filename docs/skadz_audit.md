# SkadzAlgorithm Audit (spec Step 0)

Audit of the existing project at `D:\Nedlastninger\hobby\app\SKADZALGORITM` (package: `champ_rec`), performed before any implementation of the new LoL Draft Engine (`docs/sepc.md`). This is a read-only audit — nothing in SKADZALGORITM was modified. All statements below are based on reading the actual source and data files, not on filenames; anything I could not verify from the code is explicitly marked **uncertain**.

## 0. What the project is, in one paragraph

`champ_rec` is a single-user Tkinter desktop app that recommends a **champion** — never a champion+build, there is no items/runes/skill-order/build concept anywhere in the codebase — for each unfilled role during a live-ish draft. It scores candidates with a hand-weighted linear combination of scraped Lolalytics synergy/counter win-rate statistics, converted to log-odds, with an optional minimax lookahead over possible enemy responses. It optionally auto-fills the draft from the local League Client (LCU). There is no database, no trained model, no knowledge base, no OTP data, and no patch-keying.

---

## 1. Architecture and module layout

```
champ_rec/
  main.py                     # entry point: loads 3 CSVs, launches Tkinter GUI
  core/
    enums.py                  # Role, Strategy enums
    champion_resolver.py      # champion identity resolution across ID/key/name forms
    role_guess.py             # Hungarian-algorithm role assignment + role-probability lookup
    score.py                  # OLDER scoring implementation (pandas .loc based)
    recommend.py              # CURRENT scoring implementation (dict-lookup based)
    lcu_client.py              # read-only League Client (LCU) HTTP client
    lcu_champ_select.py       # parses LCU champ-select session into ally/enemy/bans
    repo/
      matchup_repository.py   # wraps matchups_shrunk.csv, cached fast lookups
      priors_repository.py    # wraps champion_priors.csv
    services/
      recommend_service.py    # orchestrates role_guess + recommend.py for the UI
      draft_score_service.py  # orchestrates score.py for the overall draft-score labels
  ui/
    app.py                    # main Tkinter window (ChampionPickerGUI), 728 lines
    autocompleteEntryPopup.py # custom autocomplete Entry+Listbox widget
    components/
      draft_score/            # controller + Tk view-adapter for draft-score labels
      recommend/               # controller + view for the per-role suggestion lists
  scripts/                    # ingestion pipeline (see §2)
  data/                       # CSVs + champion icons (see §2)
```

### Data/control flow

`main.py` → `MatchupRepository.from_csv("data/matchups_shrunk.csv")` + `PriorsRepository.from_csv("data/champion_priors.csv")` → `ChampionPickerGUI(matchup_repo, priors_repo)`, which additionally loads `data/champions.csv` (via `ChampionResolver`) and `data/champion_icons/*.png` directly inside `ui/app.py`. **`matchups_shrunk.csv` is the only statistics file ever read at runtime** — `matchups.csv` and `reduced_matchups.csv` are intermediate build artifacts of the ingestion pipeline, not read by the app.

Every keystroke in any champion entry box calls `ChampionPickerGUI.combined_callback()` (`ui/app.py:549`), which re-runs role guessing, the overall draft score, and the full per-role recommendation list from scratch — there is no incremental/cached scoring across draft states (relevant precedent for the new spec's "stateless re-scoring" requirement, which this already does, just not efficiently).

### Module-by-module

- **`core/enums.py`** — `Role` (top/jungle/middle/bottom/support) and `Strategy` (Maximize/Minimax/Hybrid) string enums. Trivial, correct.
- **`core/champion_resolver.py`** (345 lines) — Resolves champion identity across four representations: Data Dragon numeric champion ID, Riot internal key (`MonkeyKing`, `DrMundo`, `Kaisa`), normalized filename (`dr_mundo`, `lee_sin`), and display name (`Wukong`, `Dr. Mundo`, `Kai'Sa`). Builds a lookup table from `champions.csv` and produces ordered icon-filename candidates (`icon_name_candidates`) for the UI's icon loader. Self-contained, no other module dependency besides pandas.
- **`core/role_guess.py`** (285 lines) — Two things: (a) `build_priors_lookup`/`get_role_probabilities_for_champion` normalize `champion_priors.csv` rows (handles both 0–1 and 0–100 scales, renormalizes if a row sums above 100%, falls back to uniform if a champion is missing); (b) `guess_enemy_roles` builds a champion×role cost matrix (`cost = -log(prior_probability)`) and solves it with `scipy.optimize.linear_sum_assignment` (Hungarian algorithm) to assign a set of known champions to distinct roles optimally. This is used both to guess enemy roles from an unordered enemy champion list and (in `ui/app.py:_apply_lcu_state`) to assign ally/enemy champions coming from the LCU (which also doesn't report role slots reliably).
- **`core/score.py`** (486 lines) — An older scoring implementation operating on a `pandas` DataFrame indexed by `(champ1, role1, type, champ2, role2)` via `.loc`. Provides `calculate_team_log_odds` (full-team synergy+counter log-odds sum), `calculate_overall_win_rates` (converts to ally/enemy win probabilities via a logistic sigmoid), `calculate_candidate_pick_score` (single-candidate contribution only, no minimax), and `predict_enemy_picks_by_synergy` (predicts likely future enemy picks from synergy with the already-picked enemy team — **defined but never called from `ui/` or `core/services/`**, i.e. dead code as far as I can find; confirmed by grepping the repo for its name, only found in its own definition and no imports elsewhere in `core/` or `ui/`). Only reachable production path: `MatchupRepository.update_overall_scores` (`core/repo/matchup_repository.py:294`) → `DraftScoreService.estimate` (`core/services/draft_score_service.py:28`) → the "Ally/Enemy Draft Score" header labels in the UI.
- **`core/recommend.py`** (416 lines) — The current, actually-used-for-recommendations scoring implementation, operating on `MatchupRepository`'s fast dict lookups instead of a pandas-indexed DataFrame. `score_candidate_pick`/`score_enemy_candidate_pick` compute one candidate's ally-synergy + enemy-counter contribution; `should_use_minimax_for_role` + `get_champion_scores_for_role` add three selectable pick strategies:
  - `Maximize`: score candidate only (no lookahead).
  - `MinimaxAllRoles`: also subtract the single worst-case enemy response found by scanning every candidate for every still-unfilled enemy role.
  - `Hybrid` (default): use `Maximize` when the enemy has already locked the mirroring role, `MinimaxAllRoles` otherwise.
  This is reachable via `RecommendService.recommend` (`core/services/recommend_service.py:51`), the actual path that populates the "Suggested Picks" panel.
  **`score.py` and `recommend.py` implement conceptually the same math (weighted sums of synergy/counter log-odds) but are two independent, non-shared implementations that have already drifted** — `score.py` has no minimax term and a different enemy-synergy weight default (0.2 vs `recommend.py`'s services layer not exposing one at all — it's baked into `score_enemy_candidate_pick`'s default of 0.4). A change to the scoring formula requires editing two places, with no shared test asserting parity.
- **`core/repo/matchup_repository.py`** (404 lines) — Loads `matchups_shrunk.csv` into a DataFrame; normalizes role/type text; builds and caches (a) a `.loc`-indexed DataFrame per adjustment "method" (`indexed()`), and (b) a plain dict fast-lookup per method (`fast_lookup()`, `fast_delta_lookup()`) keyed on the full 5-tuple, invalidated whenever the method or in-place recalculation changes. Exposes a "method" selector (`Bayesian`/`ADVI`/`Hierarchical`, see finding in §4) via `METHOD_TO_LOG_COL = {"bayesian": "log_odds_bayes", "advi": "log_odds_advi", "hierarchical": "log_odds_hierarchical"}`. Also has `recalculate_matchups(m_value)` to redo Bayesian shrinkage in-place with a different prior strength, and `save()` to write the current DataFrame back to CSV — **neither is called anywhere in `main.py`, `ui/`, or `scripts/`**, i.e. more dead/unused-in-practice code paths (uncertain whether these were used interactively in a notebook or REPL at some point; no evidence either way in the repo).
- **`core/repo/priors_repository.py`** (56 lines) — Thin validated wrapper over `champion_priors.csv`; coerces role columns to numeric, clips negatives to 0.
- **`core/services/recommend_service.py`** (154 lines) — `TeamState`/`RecommendResult` dataclasses; `RecommendService.recommend()` normalizes ally/enemy team dicts, guesses enemy roles (from explicit role slots if given, else via Hungarian assignment), computes per-champion role-probability breakdowns for the blue hint labels, excludes already-picked/banned champions, and calls `get_champion_scores_for_role` once per role, keeping the top 5 by the selected metric (win-rate-derived score or raw delta).
- **`core/services/draft_score_service.py`** (51 lines) — `DraftScoreService.estimate()` calls `MatchupRepository.update_overall_scores`, which internally calls `guess_enemy_roles` then `core.score.calculate_overall_win_rates`. `DraftScorePresenter.to_label_text` formats the two percentages.
- **`core/lcu_client.py`** (149 lines) — Reads the League Client's `lockfile` (tries a hardcoded `C:\Riot Games\...` / `D:\Riot Games\...` path, then walks `C:\Riot Games` and `D:\Riot Games` looking for a file literally named `lockfile`) to get the local HTTPS port/password, then does read-only GETs against the LCU's local REST API with basic auth (`riot:<password>`) and a self-signed-cert-tolerant `requests.Session`. No writes to the client, 1-second request timeout, treats 401/403 as "connection lost" and re-authenticates lazily on next call.
- **`core/lcu_champ_select.py`** (111 lines) — Calls `GET /lol-champ-select/v1/session`, walks `myTeam`/`theirTeam`/`bans`/`actions` to build ally/enemy/banned champion-ID lists (including in-progress pick/ban actions, not just finalized ones), then resolves IDs to names via `ChampionResolver`. Clean, self-contained, no dependency on the stats/scoring code at all.
- **`ui/app.py`** (728 lines) — The Tkinter main window. Builds ally/enemy entry rows (`AutocompleteEntryPopup`), a settings row (metric Delta/WinRate, pick strategy, adjustment method, auto-LCU-sync toggle), per-role "Suggested Picks" panels with champion icons + score/delta labels, and a background LCU-poll loop (`after(1500, ...)`) that auto-fills the draft when `auto_lcu_sync` is on. `_add_recommendation_row` displays `score_pct = log_odds_to_probability(total_log_odds)` and `Δ: {total_delta:.3f}` per suggestion — the UI-visible number is a converted probability, not a raw stat.
- **`ui/autocompleteEntryPopup.py`** (292 lines) — Self-built autocomplete combobox (prefix-match then substring-match suggestions, popup `Toplevel` + `Listbox`, arrow-key/tab/enter navigation). Generic Tkinter widget, no domain logic.
- **`ui/components/draft_score/`, `ui/components/recommend/`** — Thin MVC-ish controller/view-adapter wrappers that translate between Tkinter widget state and the `core/services` dataclasses (`TeamInput`/`TeamState` in, `DraftScoreEstimate`/`RecommendResult` out).

---

## 2. Data sources, formats, and datasets

### Ingestion pipeline (`scripts/`, orchestrated by `scripts/script.py`)

1. **`download_champions_and_icons.py`** — fetches the latest Data Dragon version (`ddragon.leagueoflegends.com/api/versions.json`), then the champion list (`.../champion.json`) and per-champion icon PNGs, writing `data/champions.csv` and `data/champion_icons/*.png`. Only downloads icons that don't already exist locally (`force=False`).
2. **`download_champion_links.py`** — fetches the champion list from CommunityDragon (`champion-summary.json`), builds a Lolalytics build-page URL per champion×lane using `BASE_URL = "https://lolalytics.com/lol/{champ_name}/build/?lane={lane}&patch=30"` (`scripts/config.py:8`), validates/whitelists URL shape, and writes `data/champion_links.txt`.
3. **`download_champion_priors.py`** — Selenium (headless Chrome via `webdriver_manager`) scrapes Lolalytics tier-list pages per lane (`.../tierlist/?lane={lane}&patch=30`) via hardcoded XPath, scrolling to load all rows, and records each champion's **pick rate in that lane** (not win rate, not a Riot-reported role split) → `data/champion_priors.csv`.
4. **`download_champion_matchups.py`** — Selenium, 3 worker threads each with its own browser, visits every URL in `champion_links.txt`, clicks the "strong counter" / "weak counter" / "good synergy" / "bad synergy" tabs on each champion's Lolalytics build page, and scrapes `champ1, role1, type, champ2, role2, win_rate, delta, sample_size` rows into `data/matchups.csv` (appended incrementally, one champion's rows written per lock acquisition). Randomized user agents and delays to reduce scraping detection; explicit rate-limit-conscious design (`PAGE_DELAY_RANGE`, `TAB_CLICK_DELAY_RANGE`) but **no evidence in the code of a ToS/robots.txt check**, which the new spec explicitly requires before automated collection from Lolalytics.
5. **`process_dataset.py`** — validates schema, normalizes text columns, drops invalid rows (`sample_size <= 0`, `win_rate` outside `(0, 100)`, unknown `type` values), **symmetrizes `Synergy` rows** (A-top+B-jungle-synergy and B-jungle+A-top-synergy are the same fact, canonicalized and sample-size-weighted-averaged together; `Counter` rows are kept directional), then applies Bayesian shrinkage of `win_rate` and `delta` toward a global mean (`50.0` / `0.0`) with prior strength `m=200`, and derives `log_odds_bayes = logit(win_rate_shrunk_bayes)`. Writes `data/reduced_matchups.csv` (intermediate) and `data/matchups_shrunk.csv` (final, loaded at runtime).

`scripts/script.py` runs all five in order and deletes the previous `champion_priors.csv`/`matchups.csv`/`reduced_matchups.csv`/`matchups_shrunk.csv` first, i.e. **it is a full rebuild, not an incremental/idempotent per-patch pipeline** — re-running it does not append or diff against a previous patch's data, it replaces it, and nothing is retained to compare across patches (the new spec explicitly requires idempotent, per-patch-re-runnable pipelines with retained patch history).

### Data files present (verified by reading headers, counting rows, and checking file mtimes — not assumed)

| File | Rows (excl. header) | Columns | Last modified |
|---|---|---|---|
| `data/champions.csv` | 173 | `champion_id, display_name, sanitized_name, alias` | 2026-07-03 16:02 |
| `data/champion_priors.csv` | 173 | `champion_name, top, jungle, middle, bottom, support` (lane pick-rate %, e.g. `aatrox,77.29,19.78,2.01,0.0,0.0`) | 2026-07-03 16:02 |
| `data/matchups.csv` (raw scraped) | 291,847 | `champ1, role1, type, champ2, role2, win_rate, delta, sample_size` | 2026-07-03 20:41 |
| `data/reduced_matchups.csv` (deduped/symmetrized) | 239,358 | same as above | 2026-07-03 20:42 |
| `data/matchups_shrunk.csv` (final, runtime) | 239,358 | above + `win_rate_shrunk_bayes, log_odds_bayes, delta_shrunk_bayes` | 2026-07-03 20:42 |
| `data/champion_icons/*.png` | 174 icon files | — | mixed |
| `data/champion_links.txt` | one Lolalytics URL per champion×lane | text | — |

All five data files are same-day-fresh relative to "today" (2026-07-04), i.e. this looks like a genuinely current-patch scrape. **However, no file or line of code anywhere records which actual League of Legends patch (e.g. "25.14") this data is from.** There is no `patch` column in any CSV. The only patch-like signal is the literal query-string constant `patch=30` baked into `BASE_URL`/`TIER_LIST_URLS` in `scripts/config.py` and `scripts/download_champion_priors.py` — this is almost certainly a Lolalytics-internal patch-index parameter (Lolalytics numbers patches sequentially rather than using Riot's version string), but **I could not determine the actual patch string it maps to from the code alone, so I report this as uncertain rather than guessing.** This is a direct, concrete gap against the new spec's "every statistical row keyed by `patch_id`" requirement (spec Component 2) — today's dataset has zero patch provenance.

The `type` column contains cross-lane rows that look unusual on inspection, e.g. `Aatrox,bottom,Counter,Ornn,top,85.71,41.12,7` — Aatrox recorded with `role1=bottom` countering top-lane Ornn. This is very likely Lolalytics reporting rare/off-meta lane occurrences with tiny sample sizes (here `sample_size=7`) rather than a scraping bug, but it illustrates that the raw dataset includes low-confidence, low-sample-size rows that only get pulled toward the global mean by `m=200` Bayesian shrinkage in `process_dataset.py`, not filtered out — nothing in the pipeline reports or surfaces sample-size coverage (no equivalent of the new spec's `docs/data_report.md`).

### Datasets worth reusing

- **None of the CSVs are directly reusable as-is for the new project**, because none are patch-keyed and none derive from the new spec's preferred source (Riot API match data) — they're Lolalytics-scraped aggregates of unknown recency-per-row and unknown exact "delta" definition (**uncertain** — `delta` is taken verbatim from Lolalytics' DOM with no formula given in-repo; likely a win-rate-delta-vs-baseline metric Lolalytics itself computes, but this is not confirmed by any code in the repository).
- `data/champions.csv` + `data/champion_icons/` are a reusable, current Data Dragon snapshot and directly overlap with the new spec's knowledge-DB champion source — but trivial to regenerate fresh rather than importing this exact file, since `download_champions_and_icons.py`'s logic (see §5) is what's actually worth carrying forward, not the snapshot itself.
- **No OTP/OneTricks.gg data exists anywhere in this project.** Component 3 of the new spec has zero prior art here.

### Stray files found (not part of the current app, flagged for completeness, left untouched)

Three top-level zip files sit next to `champ_rec/`: `champ_rec.zip`, `lol.zip`, `matchups_shrunk.zip`, all dated January–February 2025. Contents (`champ_rec.py`, `process.py`, `dataset.py`, older `matchups_shrunk.csv` snapshots ~12–13 MB each) show this used to be a single-file script before being restructured into the current `champ_rec/` package. SKADZALGORITM has no `.git` directory (confirmed: `git -C SKADZALGORITM log` fails with "not a git repository"), so these zips are the only history available, and they predate the current architecture enough that they're historical reference only, not reusable code.

---

## 3. Existing feature engineering

There is **no mechanical/knowledge-based feature engineering at all** — no ability data, no item data, no numeric ratings, no semantic tags, nothing resembling the new spec's knowledge database. Every "feature" the app uses is one of:

1. **Pairwise statistical aggregates** — `win_rate`, `delta`, `sample_size` for a `(champ, role, relation_type, champ, role)` tuple, where `relation_type` is `Synergy` (symmetric, ally-pair) or `Counter` (directional, opposing-pair). These are scraped, not computed from any model.
2. **Lane pick-rate as a role-probability proxy** — `champion_priors.csv`, a champion's scraped pick-rate percentage per lane, renormalized at load time (`role_guess.build_priors_lookup`) to behave like a probability distribution over roles. This is a real but weak proxy: a champion's pick rate in a lane reflects population-level pick tendency, not the champion's true positional viability or a Riot-reported role split.
3. **Team/composition aggregates computed only at scoring time, never persisted** — the "team log-odds" is a weighted sum over the pairwise terms above (all ally-pair synergies, all ally-vs-enemy counters, all enemy-pair synergies), recomputed fresh on every keystroke. There's no composition-level vector (damage-type split, engage/peel/frontline, etc.) of any kind — the new spec's entire draft-level feature vocabulary (engage, disengage, frontline, wave clear, scaling curve, etc.) has no analogue here.
4. **Hungarian-algorithm role assignment** (`role_guess.guess_enemy_roles`) — the one genuinely reusable piece of "feature engineering," in the sense of turning an unordered champion list into a role-labeled one via optimal bipartite matching on `-log(pick_rate_prior)` costs. This is a real algorithmic technique, self-contained, with no dependency on the stats-in-model boundary the new spec cares about.

There is no concept of a champion's *build* changing its feature profile — the central premise of the new spec (recommendation unit = `(champion, build_archetype)`) simply does not exist as a representable concept in this codebase's data model.

---

## 4. Existing scoring/recommendation logic and its concrete limitations

**Logic summary:** For each unfilled ally role, every eligible champion (filtered to those with any matchup data for that role, minus bans/picks) is scored as `ally_synergy_weight × Σ(synergy log-odds with current allies) + counter_weight × Σ(±counter log-odds vs. current/guessed enemies)`, optionally minus a minimax term (worst-case enemy response scored the same way) depending on the selected `Maximize` / `MinimaxAllRoles` / `Hybrid` strategy. The resulting log-odds is converted to a 0–100% "Score" via a logistic sigmoid for display, alongside the raw shrunk `delta`. Enemy roles are inferred via the Hungarian assignment described above when not explicitly known. Everything recomputes from scratch on every draft-state change (a property the new spec also wants, just achieved here by brute-force rather than by careful design).

**Concrete limitations, each grounded in the code:**

1. **The recommendation unit is champion alone, never (champion, build).** No items, runes, or skill-order data exist anywhere in the schema or code. This is the single largest gap relative to the new spec, whose entire premise is that champion-without-build recommendations are close to meaningless.
2. **Two independent, already-drifted scoring implementations exist** — `core/score.py` (used only for the overall ally/enemy draft-score header labels, via `DraftScoreService`) and `core/recommend.py` (used for the actual per-role suggestion list, via `RecommendService`). Both compute weighted sums of the same underlying pairwise log-odds, but `score.py` has no minimax/lookahead term at all, while `recommend.py` adds `Maximize`/`MinimaxAllRoles`/`Hybrid`. A change to the core weighting scheme requires editing both, with nothing enforcing parity, and `predict_enemy_picks_by_synergy` in `score.py` is defined but never called anywhere (dead code).
3. **The `ADVI`/`Hierarchical` adjustment-method options in the UI are not functionally implemented.** `ui/app.py:218-226` offers a `Bayesian`/`ADVI`/`Hierarchical` combobox that flows into `MatchupRepository`'s `method` parameter. `METHOD_TO_LOG_COL` (`core/repo/matchup_repository.py:11`) maps `"advi" → "log_odds_advi"` and `"hierarchical" → "log_odds_hierarchical"`, but **no script in the repository ever computes or writes those columns** — `process_dataset.py` only ever produces `log_odds_bayes`. I confirmed this by grepping the entire `champ_rec` tree for `advi`/`hierarchical`/`pymc`: the only hits are `matchup_repository.py` itself and the dependency lists in `pyproject.toml`/`requirements.txt`/`uv.lock` (`pymc`, `pyro-ppl`, `pyro-api`, `torch`, `arviz`, `pytensor` are all installed — a substantial Bayesian-modeling dependency stack — but nothing in `scripts/` or `core/` imports or uses any of them). Because `_with_log_odds` (`matchup_repository.py:323`) falls back to recomputing log-odds directly from raw, **unshrunk** `win_rate` whenever the requested method's column is missing, selecting `ADVI` or `Hierarchical` in the running app does not error — it silently serves different, unshrunk numbers under a label implying a more sophisticated (and currently nonexistent) statistical method. This is a real, user-facing correctness gap, not a hypothetical one.
4. **Scoring weights are hard-coded Python defaults, scattered across three files**, not centralized configuration: `ally_synergy_weight=0.4`, `counter_weight=1.0` (`core/recommend.py:126-127`), `enemy_synergy_weight=0.2` vs. `0.4` used inconsistently between `core/score.py:87` and `core/recommend.py:185`, `predicted_counter_weight=0.6` (`core/score.py:90`), `m=200` shrinkage prior strength (`scripts/process_dataset.py:218`). This directly conflicts with the new spec's "config values live in `config/`, never hard-coded" rule, and makes any weight retuning a multi-file code change.
5. **The recommendation signal is raw scraped win-rate, used directly, with no train/inference separation, calibration, or evaluation.** `win_rate`/`delta` from Lolalytics flow straight into the displayed score after only Bayesian shrinkage — there is no held-out evaluation, no baseline comparison, no generalization probe, nothing resembling the new spec's mandatory evaluation harness (spec §Evaluation). This is exactly the "champion X has a 53% win rate" pattern the new spec's design philosophy explicitly rejects.
6. **No patch-keying anywhere** (detailed in §2) — every statistical row is patch-implicit, not patch-keyed, so there is no way to reproduce, compare across patches, or apply the new spec's patch-decay/fallback policy.
7. **No database** — all data is flat CSV, loaded fully into memory (`pandas.read_csv`) on every app start; `matchups_shrunk.csv` alone is ~26 MB / 239k rows loaded whole. Works fine at this scale but has no schema versioning, no migrations, and no query layer beyond in-memory dict/DataFrame indexing.
8. **No OTP/OneTricks.gg pipeline at all** — Component 3 of the new spec has no prior art here.
9. **Ingestion has no common interface.** Each of the five scripts in `scripts/` is a bespoke procedural module with its own `main()`; there's no shared base class or contract (the new spec requires every ingestion module to implement a common interface in `ingestion/base.py`). Re-running the pipeline (`scripts/script.py`) deletes and fully rebuilds rather than incrementally updating or retaining prior-patch data. Scraping is Selenium-driven against Lolalytics' live DOM via hardcoded XPath selectors (explicitly acknowledged as fragile in the project's own README: "If Lolalytics changes its layout, selectors ... may need to be updated"), with randomized user agents/delays as the only anti-detection measure and no evidence of a ToS/robots.txt check in the code.
10. **Minor data-quality gaps**: very-low-`sample_size` rows (e.g. `sample_size=7`) and apparent off-role rows are included pre-shrinkage with no reporting of coverage/sample-size distribution (no `data_report.md`-equivalent), so a user of the current app has no way to know how thin the underlying sample was for any given recommendation beyond eyeballing the raw CSV.

---

## 5. Reuse / Adapt / Rebuild

Mapped to the new spec's repository structure: `ingestion/`, `db/`, `knowledge/`, `features/`, `training/`, `model/`, `explain/`, `eval/`, `api/`.

| Component (SkadzAlgorithm) | Decision | Target in new structure | Justification |
|---|---|---|---|
| `core/role_guess.py` — Hungarian-algorithm role assignment | **Reuse** | `features/` (or a shared `ingestion`/`knowledge` utility) | Self-contained, correct use of `scipy.optimize.linear_sum_assignment`; solves a real recurring problem (assign an unordered champion list to distinct roles) with no dependency on the statistics-in-model boundary, so it's safe to use anywhere. |
| `core/champion_resolver.py` — name/ID/key/filename resolution | **Reuse** | `ingestion/` (shared identity-resolution module) | The new project will immediately re-hit champion-identity matching across Data Dragon, CommunityDragon, the wiki, Lolalytics, and OneTricks.gg; this module already solves the display-name/riot-key/normalized-filename/numeric-ID cross-mapping problem cleanly. |
| `scripts/download_champions_and_icons.py` — Data Dragon champion+icon fetch | **Reuse** (as a starting point) | `ingestion/data_dragon` | Correct version discovery, clean JSON parsing, and a sensible icon-key fallback chain; Data Dragon is the new spec's primary knowledge-DB source, so this is the right shape to extend (add ability/item/rune fetches alongside champions). |
| `core/lcu_client.py` + `core/lcu_champ_select.py` — read-only LCU polling | **Reuse** (verbatim, held for later) | out of scope for Phase 1/2 (`api/` later) | The new spec explicitly places live champ-select integration out of current scope but requires the architecture not preclude it; this is a working, self-contained, read-only implementation worth preserving unchanged for that later phase rather than rebuilding it then. |
| `scripts/process_dataset.py` — dedupe/symmetrize/Bayesian-shrink pattern | **Adapt** | `training/` (target computation) | The shrinkage-toward-global-mean-by-sample-size idea matches the new spec's required empirical-Bayes shrinkage almost exactly, but the source data must change (Riot API match data, not scraped Lolalytics aggregates, per the spec's source-priority order) and every row must gain a `patch_id`. |
| `scripts/download_champion_links.py` + `download_champion_matchups.py` — threaded Lolalytics scraping | **Adapt** | `ingestion/lolalytics` (isolated, fragile-fallback module) | The spec explicitly wants Lolalytics "isolated behind its own ingestion module" as a last resort behind an interface, with a ToS/robots.txt check first; the threaded-worker/rate-limiting shape here is reusable, but needs a common ingestion interface, patch capture, and a ToS check added before reuse. |
| `core/repo/matchup_repository.py` — cached fast dict lookup over pairwise stats | **Adapt** | statistical DB query layer (training/eval-only) | The precomputed `(champ,role,type,champ,role) → value` dict pattern is an efficient, reusable lookup shape, but it must move to a SQLite-backed query layer sitting behind the spec's hard model/statistics import boundary — today `core/` (the "model" layer) imports it directly, which is the boundary violation the new spec is designed to prevent. |
| `core/score.py` / `core/recommend.py` — weighted sum of pairwise synergy/counter terms | **Adapt** | conceptual precedent for `features/` (v0 hand-crossed interaction terms) | The "ally-need × candidate-strength" and "enemy-strength × candidate-counter" crossing pattern is structurally similar to the new spec's v0 feature vector (e.g. `enemy_dive_score × candidate_peel`), but must be recomputed from knowledge-DB mechanical features rather than from matchup win-rate lookups, and weights must move to `config/` instead of function defaults. |
| `core/repo/priors_repository.py` — lane pick-rate as role prior | **Adapt** | `features/` role-viability filter | Useful as a weak candidate-pool filter (role viability), but pick-rate is a population-behavior proxy, not a true positional-viability signal; better sourced from Riot API positional match data where available, with this as a fallback. |
| Recommendation unit = champion only | **Rebuild** | `model/` + `features/` (candidate space) | Nothing to reuse — the new spec's foundational premise, `(champion, build_archetype)`, has no representable analogue in this codebase's data model at all. |
| Knowledge database (champions/abilities/items/runes/mechanics/tags/ratings) | **Rebuild** | `db/` + `knowledge/` | Does not exist in any form; must be built per spec Component 1 from scratch. |
| Statistical database as patch-keyed, sample-size-tracked relational store | **Rebuild** | `db/` | Today it's flat, unpatched CSVs (`data/matchups_shrunk.csv` etc.); the new spec requires a normalized, patch-keyed SQLite schema. |
| OTP database | **Rebuild** | `db/` + `ingestion/onetricks` | No prior art exists; build from scratch per spec Component 3. |
| The model (fixed hand-weighted linear log-odds formula) | **Rebuild** | `model/` + `training/` | The spec requires a trained GBDT (v0) then a two-tower neural ranker (v1) over knowledge-DB-only features, with statistics used solely for training targets/calibration — structurally incompatible with today's "look up scraped win rate, weight it by hand" approach. |
| Explanation generation | **Rebuild** | `explain/` | Today's UI only shows a numeric score percentage and a raw delta value; there is no feature-attribution or natural-language explanation of any kind. |
| Evaluation harness | **Rebuild** | `eval/` | Does not exist; the spec requires baseline comparison, generalization probes, and Top-K/MRR/NDCG metrics, none of which this project has. |
| Stats-out-of-inference-loop architectural boundary | **Rebuild** | enforced between `model/` and `db/`/training layer | Today's `core/` (the de facto "model" layer) imports directly from the CSV-backed `MatchupRepository` — the opposite of the spec's required boundary ("`/model` has no import path to the statistical DB query layer"). This must be structurally enforced from day one in the new repo, not retrofitted. |

---

## 6. Bottom line

SkadzAlgorithm is a working, reasonably well-organized single-champion draft-assistant built entirely on scraped win-rate statistics, with a few genuinely reusable pieces (Hungarian role assignment, champion-identity resolution, Data Dragon fetch logic, the read-only LCU client) and no prior art at all for the things that make the new spec's project different from it: build archetypes, a knowledge database, patch-keyed statistics, a trained model, explanations, and an evaluation harness. The new project should reuse the small set of self-contained utilities identified above, adapt the ingestion and shrinkage *patterns* (not the code) with patch-keying and a Riot-API-first source priority, and rebuild everything else from the ground up.
