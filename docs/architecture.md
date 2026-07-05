# Architecture

What's actually implemented so far, as of patch `16.13.1`. For the full
product vision and phased delivery plan, see [docs/sepc.md](sepc.md); this
document covers Phase 1's knowledge-DB work, the statistical DB's
match/matchup-pairing slice, and the OTP DB, and how they're built.

## Database

One SQLite file (`data/knowledge.db`, gitignored — regenerate it by running
migrations + ingestion), one normalized schema. `docs/sepc.md`'s "Database"
section lists knowledge, statistical, and OTP tables together as a single
schema rather than separate physical databases; the knowledge tables, the
match-capture, pairing, final-build, itemization-counter,
purchase-order/build-path, and skill-order statistical tables (`matches`,
`match_participants`, `matchup_statistics`, `champion_synergy`,
`champion_counters`, `item_statistics`, `rune_statistics`,
`item_counter_statistics`, `match_timelines`, `build_path_statistics`,
`skill_order_statistics`), and Component 3's OTP tables (`otp_players`,
`otp_builds`) all exist today — the rest of Component 2 (stat-shard perk
stats, game-duration splits) is still to come. Schema is managed by
Alembic (`migrations/`), not `Base.metadata.create_all()` — every schema
change is a migration.

The CLAUDE.md hard rule that `/model` never imports the statistical-DB query
layer will be enforced by which modules import which query-layer code once
both exist, not by physical file separation (there's no `/model` yet).

### Tables populated today

| Table | Populated by | Notes |
|---|---|---|
| `patches` | `data_dragon` | one row per ingested Data Dragon version |
| `champions` | `data_dragon` | numeric id, riot key, display name, normalized name, title |
| `champion_stats` | `data_dragon` | base/per-level stats + Riot's own 1-10 attack/defense/magic/difficulty ratings |
| `champion_tags` | `data_dragon` (`source='data_dragon'`), `knowledge` (`source='llm'`/`'override'`) | Riot's coarse tags plus the semantic taxonomy (burst, engage, tank, ...) from the knowledge/ pipeline, see below |
| `champion_abilities` | `data_dragon` | passive + spells; **rows are unstable across re-runs** (delete-then-reinsert, see below) |
| `champion_ability_details` | `wiki` (`source='wiki'`) and `manual` (`source='manual'`) | exact scalings, hidden mechanics, tips — see below |
| `items` | `data_dragon` | includes `stats` (flat stat mods), `depth`/`builds_from`/`builds_into` (build path) |
| `item_tags` | `data_dragon` (`source='data_dragon'`), `knowledge` (`source='llm'`/`'override'`) | Riot's coarse tags plus the semantic taxonomy from the knowledge/ pipeline |
| `runes` | `data_dragon` | |
| `rune_tags` | `knowledge` (`source='llm'`/`'override'`) | semantic taxonomy only — Data Dragon has no native rune tags |
| `champion_ratings` | `knowledge` (`source='llm'`/`'override'`) | numeric 0-10 ratings (engage, frontline, scaling_curve, ...), see below |
| `matches` | `riot_api` | raw Match-V5 summary, keyed on Riot's `match_id` (immutable once played) |
| `match_participants` | `riot_api` | one row per champion per match — `champion_id`/`team_position`/`win`, not player identity |
| `matchup_statistics` | `riot_api` | per `(patch, champion, role)` win/pick/ban rate, recomputed in full on every run |
| `champion_synergy` | `riot_api` | symmetric ally-pair win rate per `(patch, champion+role, champion+role)`, recomputed in full on every run |
| `champion_counters` | `riot_api` | directional enemy-pair win rate per `(patch, champion+role, enemy champion+role)`, recomputed in full on every run |
| `item_statistics` | `riot_api` | per `(patch, champion+role, item)` win/pick rate from final item builds, recomputed in full on every run |
| `rune_statistics` | `riot_api` | per `(patch, champion+role, rune)` win/pick rate from selected runes, recomputed in full on every run |
| `item_counter_statistics` | `riot_api` | per `(patch, champion+role, item, enemy champion+role)` "itemization counter" win/pick rate, recomputed in full on every run |
| `match_timelines` | `riot_api` | raw Match-V5 timeline (frame-by-frame event log), keyed on `match_id` like `matches` |
| `build_path_statistics` | `riot_api` | per `(patch, champion+role, purchase_order, item)` win/pick rate from completed-item purchase order, recomputed in full on every run |
| `skill_order_statistics` | `riot_api` | per `(patch, champion+role, level, skill_slot, level_up_type)` win/pick rate from ability leveling order, recomputed in full on every run |
| `otp_players` | `otp` | one row per identified one-trick main, keyed on `(puuid, primary_champion_id)`, not patch-scoped |
| `otp_builds` | `otp` | one row per (otp_player, sampled match) — raw-instance build capture, not a pre-aggregated `*_statistics` table |
| `build_archetypes` | `knowledge.archetypes` | per champion+role, a named archetype ("AD Burst", "Tank", ...); **no `patch_id` column** — full delete-then-reinsert per champion on every run, see below |
| `archetype_items` / `archetype_runes` | `knowledge.archetypes` | an archetype's representative items/runes, `build_order`/`is_keystone` cross-referenced from `build_path_statistics`/`rune_statistics` |
| `archetype_tags` | `knowledge.archetypes` | one row per `config.taxonomy.RATING_NAMES`, `delta` — the archetype's functional-profile shift Phase 2 applies on top of `champion_ratings` |

`matches`/`match_participants`/`matchup_statistics`/`champion_synergy`/
`champion_counters`/`item_statistics`/`rune_statistics`/
`item_counter_statistics`/`match_timelines`/`build_path_statistics`/
`skill_order_statistics`/`otp_players`/`otp_builds` have a working, tested
pipeline (`ingestion/riot_api`, `ingestion/otp`) but **zero live rows** in
this environment — no `RIOT_API_KEY` is configured here, and every test
monkeypatches the client layer rather than hitting the real API (same
pattern as `data_dragon`/`wiki`). Running
`python -m ingestion.run --source riot_api --patch <patch>` (or `--source
otp`) against real data requires your own key (register at the Riot
Developer Portal). `knowledge.archetypes` inherits the same gap
transitively — it clusters `match_participants`/`otp_builds`, so it also
produces zero rows here regardless of its own logic being fully tested
against synthetic fixtures.

### Tables that exist but are still empty

`item_effects` — structured passive/active effect breakdown, a separate
HTML-parsing task with no pipeline yet (see "Known gaps" below). Its schema
is defined now so downstream code can be written against a stable shape,
per the same pattern as everything else in this schema.

## Ingestion

### Common interface (`ingestion/base.py`)

Every source implements `IngestionSource`:

- `resolve_patch(patch: str | None) -> str` — turn a user-supplied patch (or
  `None`) into the concrete patch this run targets
- `fetch(patch) -> Any` — pull raw data from the external source; no DB access
- `load(session, patch, data) -> dict[str, int]` — upsert into the DB;
  **must be idempotent** (re-running for the same patch must not duplicate rows)

`run()` orchestrates all three and returns an `IngestionResult` (source,
patch, timing, per-entity counts, and `warnings` — non-fatal issues like a
missing page or an unrecognized template are appended to `self.warnings`
during `load()`/`fetch()`, not raised).

CLI: `python -m ingestion.run --source <name> --patch <patch>` (`ingestion/run.py`,
`SOURCES` dict maps name → class).

### `data_dragon`

Fetches champions (+ stats, tags, abilities), items (+ tags), and runes from
Riot's Data Dragon CDN. `resolve_patch(None)` resolves to the latest
available version; an explicit `"14.14"`-style patch resolves to the full
version string (`"14.14.1"`).

Idempotency: parent entities (`Champion`, `Item`, `Rune`) are upserted via
`session.merge()` on their natural (game) id. Child collections
(`ChampionTag`, `ChampionAbility`, `ItemTag`) have no natural unique key, so
they're deleted and re-inserted per parent on every run — scoped by
`source` where relevant (e.g. `DELETE FROM champion_tags WHERE
champion_id=X AND source='data_dragon'`), so a different source's rows for
the same champion are never touched. This is why `champion_abilities.id`
is **not** stable across runs, and why `champion_ability_details` keys off
the natural `(champion_id, slot)` pair instead of a foreign key to it.

Reused from the prior project (`docs/skadz_audit.md`): the Data Dragon
version-discovery pattern and the champion name-normalization approach
(`ingestion/data_dragon/identity.py`).

**Item scope:** Data Dragon's `item.json` covers every map/mode (ARAM,
Arena, Nexus Blitz, ...) and includes non-purchasable auto-granted
components — this project drafts and builds for Summoner's Rift only, so
`_is_summoners_rift_item()` filters to `maps["11"] and gold.purchasable`
before upserting, cutting the raw ~706 entries down to ~254. This was
checked against real data, not assumed: Ornn's 6 `<ornnBonus>`-tagged
Masterwork items were considered for a special-case exception, but as of
this patch they're tagged ARAM/Arena-only (`maps["11"] = False`) — the
"Living Forge" upgrade mechanic has been removed from Summoner's Rift
(matching the `Infinity Edge` item's own patch-history note about
"removed Living Forge items"), so no exception was needed; the plain
SR-only filter already excludes them correctly.

### `wiki`

**Why:** Data Dragon's ability `description` is a shallow marketing blurb.
`docs/sepc.md`'s Data Sources list already names the wiki for this
("supplementary ability details and formulas... exact scalings, hidden
mechanics. Parse defensively; wiki markup is inconsistent") — this source
supplements `champion_abilities` rather than replacing it.

**Access method:** leagueoflegends.fandom.com's plain page fetches
(`/wiki/...`) are blocked by a Cloudflare JS challenge, but its MediaWiki
API (`/api.php`) is not — that's the documented, sanctioned way to read wiki
content, not scraping evasion. `ingestion/wiki/client.py` wraps it with a
descriptive `User-Agent`, a politeness delay, and retry-with-backoff on
transient network errors (a bare read-timeout killed a full 173-champion
run once before this was added — see git history).

**Page structure** (discovered, not assumed):
- A champion's main wiki page (e.g. `Ahri`) is lore/bio only. Gameplay data
  lives at `{DisplayName}/LoL` (confirmed to work with punctuation too:
  `Kai'Sa/LoL`).
- A few champions have a different wiki base name than their Data Dragon
  `display_name` (e.g. `Nunu & Willump` → wiki page `Nunu/LoL`) —
  `ingestion/wiki/identity.py::_TITLE_OVERRIDES` holds confirmed mismatches
  (never guessed ones).
- The `/LoL` page's Abilities section is a handful of
  `{{Data {Champion}/{Slot}|Ability}}` transclusions. **Slots are discovered
  by regex, never hardcoded to `passive/Q/W/E/R`** — multi-form champions
  (Elise) have extra named slots (`Venomous Bite`, `Skittering Frenzy`,
  `Rappel`) with no Data Dragon counterpart.
- Each transclusion target is often a `#REDIRECT` to the real template
  (`Template:Data Ahri/Q` → `Template:Data Ahri/Orb of Deception`); the API's
  `redirects=1` follows it in one request.

**Parsing (`ingestion/wiki/wikitext.py`):** deliberately not a MediaWiki
template-evaluation engine.
- `parse_key_value_fields` splits a template into `|key = value` fields
  using the one-field-per-line convention this wiki actually uses — this
  sidesteps needing to parse the template's own dynamic-name preamble
  (`{{{{{1<noinclude>|Ability data</noinclude>}}}|...`), which is simply
  ignored as "before the first recognized field."
- `unwrap_decorators` recursively resolves a small, explicit set of known
  wrapper templates (`{{ap|X}}`, `{{tt|display|tooltip}}`, `{{as|text}}`,
  `{{fd|X}}`, ...) using a brace/bracket-depth-aware scanner (tracks `{{ }}`
  *and* `[[ ]]` nesting, so a wikilink's internal `|` in
  `[[Physical Damage|physical damage]]` isn't mistaken for a template
  parameter boundary). Anything not in the known set is left **completely
  untouched** and its name collected for `unknown_templates` — surfaced as
  a run-level warning, never guessed at or partially stripped.
- `notes` and `tips` are promoted to their own fields (`tips` specifically
  because it holds build-specific combos and situational advice, not just
  mechanical trivia); everything else lands in a JSON `fields` bucket.

**Deferred, on purpose:** the "Counters" table (which specific
items/abilities block, interrupt, or disable a given ability — e.g. a spell
shield blocking it, a knockup interrupting a channel) is rendered from a
`{{ct|...}}`/`{{ctable}}` template pair whose actual item/spell legend is
positional and hardcoded inside `Template:Ctable` itself, not in the
ability's own page. Decoding that legend is real, separate work — for now
`{{ct|...}}` is left verbatim like any other unrecognized template.

**Not used:** wiki.leagueoflegends.com (Riot's newer official wiki, hosted
by Weird Gloop). Its `robots.txt` has an explicit "AI bots" section that
blanket-disallows a named list of crawlers — including `ClaudeBot` and
`Claude-SearchBot` specifically. That's a deliberate statement this site
doesn't want AI-affiliated automated access, so this ingestion source
doesn't touch it, regardless of which endpoint or User-Agent. Champions not
yet on the Fandom wiki are handled via the manual source instead.

**Known limitation:** `resolve_patch(None)` returns the sentinel
`"unversioned"` and `fetch()` raises rather than guess — wiki content has no
"latest version" concept the way Data Dragon's CDN does. Always pass an
explicit `--patch` matching an already-ingested Data Dragon version.

**Current coverage:** 836 rows across 168 champions (of 173). 5 champions
have no Fandom page yet (very recent releases) and are logged as warnings,
not failures — the whole run doesn't abort on an individual page-not-found.

**Item enrichment** (`ingestion/wiki/items.py`) follows the same source and
uses the same parser, but the page structure is simpler than champions':
confirmed on `Infinity Edge`, `World Atlas`, and `Rabadon's Deathcap` that an
item has no lore/gameplay page split and no per-slot sub-templates — the
item's own page (its Data Dragon name, used directly as the wiki title) has
a single `{{Item info}}` template on it. This is what motivated the module
in the first place: Data Dragon's `description` is sometimes completely
empty for items with complex mechanics (e.g. `World Atlas`, the
support/quest item — its real behavior, charge timing and quest
thresholds, only exists in the wiki's `notes` field). Results are stored in
`item_wiki_details`, keyed directly on `item_id` (unlike
`champion_ability_details`, there's no per-slot concept, and `Item` rows
*are* stable across `data_dragon` re-runs, so no composite-key workaround is
needed here).

Only items already in the DB are enriched (i.e. already filtered to
Summoner's Rift — see `data_dragon`'s `is_summoners_rift_item`), reused
rather than re-filtered in `ingestion/wiki/source.py`.

MediaWiki titles are case-sensitive past the first character — Data
Dragon's `"Blade of The Ruined King"` doesn't match the wiki's
`"Blade of the Ruined King"`. `ingestion/wiki/items.py::_TITLE_OVERRIDES`
holds confirmed mismatches, same pattern as the champion identity overrides.

**Current coverage:** 234 of 254 SR items. 20 items have no Fandom page yet
(recent releases — the same wiki-lag pattern as champions; spot-checked
several to rule out more naming/capitalization bugs before concluding this).

### Manual source

For champions with no wiki page yet at all (Ambessa, Locke, Mel, Yunara,
Zaahen as of this writing). A human copies the rendered page content
themselves — `robots.txt` governs automated crawlers, not a person browsing
their own browser — and saves it to `data/manual_sources/<Champion>.md`
(gitignored: copied third-party wiki content, not something to
redistribute via this repo).

There's no automated parser for this raw copy-pasted text: it has no fixed
structure (navigation chrome, icon alt-text, and inconsistent table layout
mixed into rendered prose — very different from the clean wikitext template
the `wiki` source parses). Each file is read and structured by hand into
`ManualAbility` records, then loaded via
`ingestion/manual/loader.py::load_manual_abilities`, which upserts into the
same `champion_ability_details` table with `source='manual'` — so a row's
provenance is always explicit, and there's no collision risk with the
`wiki` source's rows for the same champion.

One upside of the manual path: since a human copies the *rendered* page,
the Counters table (deferred for the automated `wiki` source, see above)
comes along for free as plain resolved text — no `Template:Ctable` legend
needed.

**Currently loaded:** Ambessa (6 rows), Locke (5 rows), Mel (5 rows), Yunara
(7 rows — `Arc of Ruin` and `Untouchable Shadow`, the ultimate-upgraded forms
of her W/E, get their own named slots rather than overloading `W`/`E`, same
precedent as Ambessa's `Sundering Slam`), Zaahen (5 rows). All five
champions with no Fandom page as of this writing are now loaded.

### `riot_api`

Component 2 (statistical DB, `docs/sepc.md`)'s match-capture,
matchup-pairing, final-build, itemization-counter, and build-order slice.
Captures raw high-ELO ranked-solo matches and derives eight statistics end
to end: per `(patch, champion, role)` win/pick/ban rate
(`matchup_statistics`), symmetric ally-pair win rate (`champion_synergy`),
directional enemy-pair win rate (`champion_counters`), per-`(patch,
champion+role, item/rune)` final build win rate
(`item_statistics`/`rune_statistics`), per-`(patch, champion+role, item,
enemy champion+role)` "itemization counter" win rate
(`item_counter_statistics` — see below), and per-`(patch, champion+role,
purchase_order or level)` build-order win rate
(`build_path_statistics`/`skill_order_statistics`, from the Match-V5
*timeline* endpoint — see below). Deliberately scoped down from the full
Component 2 breadth — stat-shard perk stats and game-duration splits are
not built yet (see Known gaps below); this slice proves the pipeline shape
(auth, rate limiting, patch filtering, idempotent capture, derived
aggregation) on data already captured in `match_participants`/its
`raw_data`, the same incremental approach `data_dragon` → `wiki` took for
the knowledge DB.

**Seeding:** Challenger league only (`League-V4`), capped at
`RiotApiSettings.max_seed_summoners` (default 300) so a run's request volume
is predictable under a dev key's rate limit. A league entry's `puuid` is
used directly when present; `Summoner-V4` is only called as a fallback for
entries that lack it (`ingestion/riot_api/identity.py`), to avoid a second
API call per summoner when unnecessary.

**Rate limiting (`ingestion/riot_api/client.py`):** Riot enforces multiple
sliding windows on the API key simultaneously (a dev key: 20 req/1s *and*
100 req/2min) — a single fixed per-request delay, which is enough for the
`wiki` source's one relevant window, can't safely satisfy several windows at
once. `RateLimiter` tracks request timestamps per configured window and
blocks just long enough to stay under all of them before every request; 429
responses are retried honoring `Retry-After` when present.

**Patch filtering:** Riot's match API has no "current patch" concept —
`resolve_patch` requires an explicit `--patch` matching an already-ingested
`data_dragon` version, same as `wiki`. Match IDs are pulled from each seed
summoner's recent history regardless of patch, then each fetched match's
`info.gameVersion` is checked against the target patch's `major.minor`
prefix during `load()`; off-patch matches are counted and surfaced as a
warning rather than silently dropped.

**Idempotency:** match data is immutable once a game is played, so
`match_id` (Riot's own string id) is the natural key — `load()` skips any
match already present rather than needing data_dragon's
delete-then-reinsert pattern for its unstable child rows.

**`matchup_statistics`:** recomputed in full (deleted and reinserted) from
every `match_participants` row for the resolved patch on each run, not just
the newly-fetched batch — it's a derived aggregate, not a natural upsert.
`pick_rate`/`ban_rate` are expressed as a fraction of `games * 2` (two
team-slots per role per game). **Known limitation:** bans have no role
attribution in Riot's raw data (a ban happens before role assignment), so
ban counts are champion-level only; a champion that's frequently banned but
never picked in a given patch currently has no `matchup_statistics` row to
attach that ban count to at all — fixing this (e.g. a champion-level-only
row shape, or a separate bans table) is a follow-up, not solved by this
slice.

**`champion_synergy`/`champion_counters`:** also recomputed in full per
patch, from the same `match_participants` rows `matchup_statistics` uses,
grouped by match. Finding teammates vs. opponents within a match doesn't
need `raw_data`'s `teamId` field re-parsed — queue 420 (ranked solo/duo,
the only queue this source ingests) always splits into exactly two
opposing sides with `win` uniform within a side, so within one match, two
participants with the same `win` value are teammates and two with
different values are opponents. `champion_synergy` canonicalizes each ally
pair by `champion_id_a < champion_id_b` so a pair is stored once;
`champion_counters` stores both directions of an enemy pair explicitly
(e.g. both `Ornn vs. Vi` and `Vi vs. Ornn`) so training code never needs a
reverse-lookup rule. **Known limitation:** the same-side-by-`win` grouping
assumes exactly two opposing sides per match — correct for ranked
solo/duo, but would misclassify a non-two-team queue (e.g. Arena) if this
source is ever pointed at one; not a concern while `queue_id` stays fixed
to 420.

**`item_statistics`/`rune_statistics`:** final-build only — which items
ended up in the final item set (`item0`..`item5`) and which runes were
selected (both primary and secondary paths, 6 selections total), read
straight out of `match_participants.raw_data` (already fetched, no new API
calls). `item6` (the trinket slot) is deliberately excluded — near-uniform
per role, not informative for build comparison. Unlike
`matchup_statistics`'s `games` (total patch games, a constant across rows),
`games` here is the champion+role's own game count, so `pick_rate` reads as
"build rate given this champion+role" rather than "share of all role slots
patch-wide." **Known gap:** stat-shard perks (`statPerks.offense`/`flex`/
`defense`, e.g. Adaptive Force, Attack Speed) are not captured — Data
Dragon's rune tree (what populates the `runes` table) has no entries for
them at all, so there's no `Rune` row to key a `rune_statistics` row
against; a real fix needs its own small hardcoded shard-id table, not
solved by this slice.

**`item_counter_statistics`:** "itemization counters" — per `(patch,
champion+role, item, enemy_champion+enemy_role)` win rate, i.e. how a
specific final-build item performed for a champion+role specifically
against a specific enemy matchup, not just in aggregate.
`_recompute_item_counter_statistics` combines
`_recompute_champion_counters`'s opponent-pairing (same `win`-mismatch
rule, same two-opposing-sides assumption) with
`_recompute_item_statistics`'s final-build reading (`item0`..`item5`,
`item6`/trinket excluded) — no new data or API calls, purely a
cross-product of two things already captured in `match_participants`.
`games`/`pick_rate` are scoped to the matchup itself (this champion+role's
games against this *specific* enemy_champion+role, the same denominator
`ChampionCounters.games` uses), not the champion+role's total patch games,
so `pick_rate` reads as "build rate given this specific matchup" — the
counter-build signal a per-matchup item recommendation actually needs.
**Why this exists instead of scraping Lolalytics** (which surfaces a
similar "itemization vs. matchup" view): its `robots.txt` explicitly
disallows a named list of AI crawlers including `ClaudeBot` — the same
shape of restriction `wiki.leagueoflegends.com` has (see the `wiki`
section's "Not used" note above) — so per `docs/sepc.md`'s Data Sources
rule ("check ToS and robots.txt before any automated collection, prefer
computing equivalent aggregates from Riot API match data yourself"), this
is computed from already-captured Riot match data instead. Sample size per
matchup will be thinner than Lolalytics' aggregate-across-everyone numbers
(no live data ingested yet either way — see below), but it's derived
end-to-end from official data with full patch provenance.

**`build_path_statistics`/`skill_order_statistics`:** the Match-V5
*timeline* endpoint (`client.fetch_match_timeline`) is fetched once per
successfully-fetched match summary (skipped if the summary fetch itself
failed) and stored raw in `match_timelines`, same immutable-match-id
idempotency as `matches`. "Build path" is derived as **completed-item
order**, not raw purchase order: `_terminal_item_ids` filters to items
where `Item.builds_into` is empty and `item_tags` has no `Consumable`/
`Trinket` tag — checked against real data, since `Item.depth` alone is
unreliable (genuine components like Long Sword/Boots/Cloth Armor *and*
genuine standalone final items like Doran's items/jungle pets both have
`depth=NULL`, while 2024+ boot-enchant items mean pre-enchant tier-2 boots
now have a non-empty `builds_into` too). `_completed_item_order` walks a
match's pooled `ITEM_PURCHASED`/`ITEM_UNDO` events in timestamp order per
participant, capped at `RiotApiSettings.build_path_slots` (default 4).
`_skill_level_order` does the same for `SKILL_LEVEL_UP` events; the
champion level for the Nth level-up is `N` (Riot's raw event carries no
level field — one point is spent per level). Both join a timeline's
`participantId` back to `(champion_id, team_position, win)` via
`match.raw_data["info"]["participants"][participantId - 1]` — documented
Match-V5 behavior that `participantId` is 1-indexed into that same list —
rather than adding a new column to `MatchParticipant`. **Known
limitations:** only a straightforward "undo the last terminal purchase" is
netted out (undoing a sale, or an out-of-order undo, isn't modeled); a
sold item still counts toward its build-path slot (build path reflects
what was bought, not what remained at game end — `item_statistics` already
covers "final build").

**Deferred, on purpose (tracked in Known gaps):** stat-shard perk stats,
game-duration splits, multi-region crawling, and historical-patch backfill.

### `otp`

Component 3 (OTP DB, `docs/sepc.md`): identifies high-ELO one-trick mains
and captures their per-match builds. `docs/sepc.md`'s literal source for
this component is OneTricks.gg, under "the same ToS caveat" as Lolalytics
(check ToS/robots.txt, prefer deriving from Riot API match data instead).
OneTricks.gg's `robots.txt` returns **HTTP 429 on every fetch attempt**
(tried both `www.onetricks.gg` and bare `onetricks.gg`) — a harder block
than Lolalytics (which at least served a readable robots.txt with named-bot
disallow rules; see `item_counter_statistics` above for that precedent). So
`otp` derives the one-trick signal directly from the Riot API instead:
Champion-Mastery-V4 point concentration identifies one-trick mains (no
other tool in this codebase tracks "mastery" or "one-trick" status), then
the same Match-V5 match/timeline endpoints `riot_api` already uses capture
their recent builds.

**Seeding and identification:** reuses `riot_api`'s exact Challenger-seed
pool (`ingestion.riot_api.identity.seed_challenger_puuids`, promoted out of
`RiotApiSource` so both sources share it verbatim). For each seeded puuid,
`client.fetch_champion_masteries` (Champion-Mastery-V4, platform-routed)
returns every champion the player has mastery points on.
`_qualify_one_trick` computes the top champion's points and *concentration*
(top champion's points / total points across all champions); a player
qualifies as a one-trick on that champion only when **both**
`OtpSettings.min_mastery_points` and `OtpSettings.min_mastery_concentration`
clear — points alone would catch long-tenured players who've played
everything a lot, concentration alone would catch a fresh account with
only one champion played a handful of times. Qualifying candidates are
capped at `OtpSettings.max_one_tricks_per_run`, but every seeded puuid
still needs its own mastery call to check qualification in the first place
(Champion-Mastery-V4 has no "find me high-mastery players" query) — so a
full run's request volume is dominated by the 300-puuid seed pool, not
this cap; expect an hour-plus real run under a dev key's rate limit.

**Build capture:** for each qualifying (puuid, champion) pair, recent match
ids (`client.fetch_match_ids`, capped at
`OtpSettings.matches_per_one_trick`, deeper per-player than
`RiotApiSettings.matches_per_summoner` since OTP's point is depth on a
known individual, not breadth) are fetched along with each match's summary
and timeline. Unlike `riot_api`, a match whose timeline fetch fails is
dropped entirely rather than partially stored — every interesting `otp`
column (starting items, completed-item order, skill order) comes from the
timeline, so a match without one has nothing worth persisting. Starting
items are purchases before `OtpSettings.starting_items_cutoff_ms` (~90s,
covering the pre-minions-spawn opening buy) — a fixed-cutoff approximation
since Match-V5 has no first-recall event to key off instead; unlike
completed-item order, starting items keep components/consumables (a
starting buy is commonly a Doran's item plus potions/wards, neither of
which is "terminal"). Completed-item order, skill order, and terminal-item
filtering reuse `ingestion.riot_api.timeline` (promoted out of
`riot_api/source.py`'s former private helpers so both sources share the
same parsing logic verbatim, decoupled from the `MatchTimeline` ORM row
since `otp`'s fetched timelines are never persisted as `MatchTimeline` rows
— see below). The lane opponent (`enemy_champion_id`) uses the same
team_position + opposite-`win` pairing rule `champion_counters` uses.

**Schema:** `otp_players` is one row per `(puuid, primary_champion_id)`,
*not* patch-scoped — Champion Mastery is Riot's lifetime-cumulative stat,
so keying it per-patch would be semantically wrong. Re-running `otp` for a
new patch refreshes the existing row rather than duplicating it;
`win_rate`/`games_sampled` are recomputed from *every* `OtpBuild` row ever
stored for that player (`_refresh_player_aggregate`), not just the run's
new inserts, so a second run doesn't misleadingly collapse the sample to
just that run's delta. `otp_builds` is one row per `(otp_player, sampled
match)` — deliberately *not* pre-aggregated like `ItemStatistics`, since
build-archetype clustering and situational-trigger labeling (this
component's actual purpose per `docs/sepc.md`) need individual build
instances, not pre-aggregated stats; `otp_players`' win_rate/sample_size
already cover the player-level aggregate. `otp_builds.match_id` is
deliberately **not** a foreign key into `matches`: OTP's samples are
individually-targeted one-trick picks, not part of the Challenger-aggregate
sample `matches`/`matchup_statistics`/etc. represent — `riot_api`'s
`_recompute_*` functions scan every `Match` row for the resolved patch with
no source attribution, so sharing the table would silently bias those
aggregates toward one-tricks' atypical win rates. A match captured by both
sources (if a one-trick was also a Challenger seed) keeps two independent
`raw_data` copies — accepted, known duplication, cheap since match data is
immutable. `otp_builds.patch_id` is nullable, mirroring `Match.patch_id`
exactly (same "patch not yet ingested" warn-and-continue path, not a hard
failure).

**Deferred, on purpose:** build-archetype clustering itself (this slice
only captures the raw signal it needs), multi-region crawling, and
historical-patch backfill for OTP data specifically.

## `knowledge/` (semantic tag / numeric rating pipeline)

Populates the `'llm'`/`'override'` rows of `champion_tags`/`item_tags`/
`rune_tags` and all of `champion_ratings` (`docs/sepc.md` Component 1). This
is the last blocker on build archetype extraction: archetypes cluster
builds by the functional profile (tags + rating deltas) they confer, which
needs a champion baseline profile to modify in the first place.

Deliberately **not** built on `ingestion/base.py`'s `IngestionSource`: that
interface assumes one external raw payload per patch, whereas this pipeline
calls the LLM once per already-ingested DB row (reads from our own DB, not
an external source) and isn't patch-scoped — none of its four target tables
carry a `patch_id` column. `docs/sepc.md`'s repo-structure section itself
separates `/ingestion` (external data sources) from `/knowledge` (tag/rating
pipeline, build archetype extraction, overrides) as distinct concerns.

**Taxonomy (`config/taxonomy.py`):** `SEMANTIC_TAGS`, a fixed set (burst,
engage, tank, peel, wave_clear, ...), and `RATING_NAMES`, ten 0-10 ratings
lifted directly from `docs/sepc.md`'s Model v0 feature-vector list (engage,
disengage, frontline, peel, wave_clear, burst, sustained_dps, mobility,
cc_score, scaling_curve) rather than invented separately, since that's the
actual downstream consumer. Both are config per CLAUDE.md's hard rule —
extraction/validation code imports them rather than hard-coding tag/rating
strings.

**Extraction (`knowledge/client.py`, `knowledge/prompts.py`,
`knowledge/sourcetext.py`):** `sourcetext.py` assembles prompt input purely
from already-ingested DB rows (champion: name/title/Riot tags/base-stat
ratings/ability descriptions + wiki notes; item: name/description/plaintext/
stats/Riot tags; rune: path/name/short+long desc) — no network access.
`client.py` calls the Claude API (`LlmTaggingSettings.model`, Haiku by
default for cheap/fast bulk classification) with a tool-forced call whose
JSON schema constrains output to `SEMANTIC_TAGS`/`RATING_NAMES`; validation
still defends against a provider not respecting that schema (unknown tags
dropped, missing ratings defaulted to the 0-10 midpoint, out-of-range
ratings clamped), each producing a warning rather than raising — same
defensive-parsing philosophy as `ingestion/wiki`'s handling of inconsistent
wiki markup. `knowledge/client.py`'s low-level `_call_anthropic` is the one
function that talks to the network; tests monkeypatch it directly, same
pattern as `ingestion/wiki/client.py`'s `_get`.

**Overrides (`knowledge/overrides.py`, `data/tag_overrides.yaml`):** the
manually reviewed file that always wins, per spec. Checked into the repo
(unlike `data/manual_sources/`, which is gitignored third-party content) —
`data/*.db` and `data/manual_sources/` are the only `data/` gitignore
entries. Full-replacement semantics per field: specifying `tags` or
`ratings` for an entity completely replaces the LLM-derived value for that
field; an omitted field falls back to the LLM output. Unlike LLM output,
this file is hand-curated, so an invalid tag/rating name or out-of-range
value raises immediately at load time rather than being defensively
dropped.

**Loader/override-precedence wrinkle (`knowledge/loader.py`,
`knowledge/query.py`):** `champion_tags`/`item_tags`/`rune_tags` key
uniqueness on `(entity_id, tag, source)`, so `'llm'` and `'override'` rows
*coexist* — each upsert here deletes-then-reinserts only its own
`(entity_id, source)` rows (the exact convention `ingestion/data_dragon`
already uses for `source='data_dragon'`, but via SQLAlchemy's ORM-enabled
`delete()` rather than `Table.__table__.delete()`, so the session's
identity map stays in sync across the many delete/reinsert cycles one
`knowledge.run` invocation does). `champion_ratings` keys uniqueness on
`(champion_id, rating_name)` alone — no `source` in the constraint — so at
most one row can exist per rating name at all; an override there
genuinely *replaces* the `'llm'` row rather than coexisting with it. Since
tags can coexist across sources but ratings can't, only tags need a
read-time precedence resolver: `knowledge/query.py`'s
`effective_champion_tags`/`effective_item_tags`/`effective_rune_tags`
return `'override'` rows when present, else `'llm'` rows (ignoring
`champion_tags`'/`item_tags`' unrelated `'data_dragon'` rows entirely);
`effective_champion_ratings` just returns whatever row exists, since the
loader already guarantees there's only one. These `effective_*` functions
are `knowledge/archetypes`' entry point (see below) for a build's tag
profile, so the override-wins rule lives in one place.

**CLI:** `python -m knowledge.run [--only champions|items|runes] [--force]`.
Skips entities that already have a `source='llm'` row unless `--force`,
since each entity costs a real LLM call and re-running for coverage of
newly-ingested entities shouldn't re-pay for unchanged ones. No live rows
exist in this environment — needs an `ANTHROPIC_API_KEY`, same requirement
pattern as `RIOT_API_KEY`, and fails immediately with a clear error rather
than a silent no-op if it's missing.

**Explicitly out of scope:** `item_effects` (structured passive/active
effect breakdown — separate, more mechanical parsing task, not tied to the
tag/rating pipeline in the spec's wording). Build archetype extraction
itself is `knowledge/archetypes/`, immediately below.

## `knowledge/archetypes/` (build archetype extraction)

Populates `build_archetypes` + `archetype_items`/`archetype_runes`/
`archetype_tags` (docs/sepc.md Component 1) — the last empty part of the
knowledge DB, consuming `knowledge`'s tag/rating pipeline, the statistical
DB, and the OTP DB together to finally produce the `(champion, build
archetype)` unit the whole project is designed around.

**Observed builds (`builds.py`):** two sources, per the spec's "cluster
observed builds from statistical + OTP data":
- **Challenger-aggregate** — `match_participants` for the resolved patch,
  parsed via new `ingestion/riot_api/participants.py`'s `final_items`/
  `rune_selections` (promoted out of `ingestion/otp/source.py`'s former
  private `_final_items`/`_rune_selections`, the same "shared so both
  callers use it verbatim" precedent as `ingestion.riot_api.timeline`/
  `identity`). `item_statistics`/`rune_statistics` only store item-marginal/
  rune-marginal aggregates, not each game's joint item+rune set, so this
  reconstructs it fresh from the same `raw_data` those aggregates read.
- **OTP** — `otp_builds`, already-parsed lists. Weighted by the sampled
  player's sample size and win-rate consistency (docs/sepc.md's "Role of
  OTP data"): `weight = min(1.0, games_sampled / otp_weight_normalizer) *
  (0.5 + 0.5 * win_rate)`, vs. weight `1.0` for every aggregate build.

**Which champion+role pairs to attempt:** driven by `matchup_statistics`
rows for the resolved patch with `games >= ArchetypeSettings.
min_builds_per_champion_role` — reuses the existing viability signal rather
than inventing a new one, directly matching the spec's "every viable
champion+role" phrasing.

**`build_archetypes` has no `patch_id` column** — unlike the Component 2
statistical tables, it was never patch-scoped in the schema. So this
pipeline scopes its *input* gathering to one resolved patch (`--patch`,
same CLI convention as `ingestion.run`/`knowledge.run`) but writes an
evergreen "current best known archetypes" snapshot per champion, fully
delete-then-reinserted on every run — the same "rows are unstable across
re-runs" convention `ingestion/data_dragon` uses for `champion_abilities`.
Because of this, all of a champion's roles are extracted and combined
*before* the single per-champion upsert (`knowledge/archetypes/run.py`) —
upserting once per role would each time wipe out the previous role's
freshly-inserted archetypes for that champion.

**Feature representation and clustering (`profile.py`, `clustering.py`):**
each observed build becomes a tag-fraction vector over `config.taxonomy.
SEMANTIC_TAGS` (fraction of its items+runes, via `knowledge.query.
effective_item_tags`/`effective_rune_tags`, carrying each tag — 0 for
everything if `knowledge.run` hasn't tagged those items/runes yet, so this
degrades gracefully rather than crashing). Clustered per champion+role with
`scipy.cluster.hierarchy` (`linkage(method="average")` + `fcluster(...,
criterion="distance")`) — a distance threshold
(`ArchetypeSettings.distance_threshold`, unverified against real data, same
caveat as `OtpSettings`' mastery thresholds) rather than a fixed k, since
guessing a per-champion cluster count upfront doesn't make sense.
Clustering runs on **unweighted** vectors (structural similarity only); OTP
vs. aggregate weighting is applied afterward when summarizing each
resulting cluster — kept separate from the clustering geometry itself. A
cluster becomes a real archetype only above `ArchetypeSettings.
min_cluster_weight`; survivors are capped at `ArchetypeSettings.
max_archetypes_per_champion_role` (highest weight first).

**Per-archetype derivation (`extraction.py`) reuses already-computed
statistics instead of re-deriving them:**
- **Representative items/runes:** frequency-threshold over the cluster's
  builds (`core_item_frequency`/`situational_item_frequency`/
  `core_rune_frequency`). `ArchetypeItem.build_order`: each representative
  item's highest-`pick_rate` `purchase_order` from `build_path_statistics`
  for this champion+role/patch. `ArchetypeRune.is_keystone`: cross-
  referenced from `rune_statistics.is_keystone` for this champion+role/patch.
- **Damage-type label** (`naming.py`'s `damage_label`, "AD"/"AP"/`None`):
  the only place damage type is computed anywhere in this project — sums a
  small fixed set of AD vs. AP `Item.stats` keys
  (`config.archetype_rules.AD_STAT_KEYS`/`AP_STAT_KEYS`) across the
  cluster's representative items, since `SEMANTIC_TAGS` has no AD/AP tag of
  its own.
- **Name** (`BuildArchetype.name`): **user's explicit choice — a
  deterministic rule table, not an LLM call per cluster** (free, instant,
  traceable to the archetype's own computed profile).
  `config.archetype_rules.ARCHETYPE_NAME_BY_TAG` maps the cluster's single
  highest-fraction tag (among a curated subset, gated by
  `ArchetypeSettings.name_tag_min_presence`) plus the damage label to a name
  (e.g. `tank` → `"Tank"`, `burst` + `"AD"` → `"AD Burst"`), falling back to
  `"{damage} Generalist"`. `BuildArchetype` has
  `UniqueConstraint(champion_id, name)` — `naming.dedupe_name` resolves a
  same-champion collision (e.g. two roles both scoring the same name) by
  appending `" ({role})"`, then a numeric suffix.
- **Rating deltas** (`deltas.py`, one `ArchetypeTag` row per
  `config.taxonomy.RATING_NAMES`): `config.archetype_rules.RATING_TAG_MAP` —
  a compact 10-entry table, not a dense matrix, since 7 of the 10
  `RATING_NAMES` already share an exact name with a `SEMANTIC_TAGS` entry;
  the rest map to their closest tag (`frontline`←`tank`, `cc_score`←
  `cc_heavy`, `scaling_curve`←`scaling` minus `early_game`).
  `delta = archetype_delta_scale * mapped_tag_fraction`, clamped to
  ±`archetype_delta_max` — applied unconditionally (no presence-threshold
  gate like naming has), since this is the archetype's *actual* functional
  profile Phase 2's Model v0 reads ("numeric ratings after applying the
  archetype's deltas"), not cosmetic labeling. Phase 2 adds this delta to
  `champion_ratings` at feature-extraction time — this module doesn't read
  the champion's baseline rating at all.

**Loader (`loader.py`):** `BuildArchetype` has no column to scope a delete
by (unlike `champion_tags`'s `source`), so a champion's archetypes are
fully deleted and reinserted. Children (`ArchetypeItem`/`ArchetypeRune`/
`ArchetypeTag`) have no `ondelete="CASCADE"` at the DB level and bulk
`delete()` doesn't trigger ORM cascade, so they're deleted explicitly
before their parent rows — otherwise re-running would silently accumulate
orphaned child rows forever.

**CLI:** `python -m knowledge.archetypes.run --patch <patch> [--champion
<name>] [--role <role>]`. No live output exists in this environment — zero
`matchup_statistics` rows without a `RIOT_API_KEY` means zero champion+role
pairs clear the viability threshold, so a real run here completes cleanly
reporting 0 archetypes rather than crashing (verified against the actual
`data/knowledge.db`).

## Config (`config/settings.py`)

Frozen dataclasses, one per concern: `DataDragonSettings`, `WikiSettings`,
`RiotApiSettings`, `OtpSettings`, `LlmTaggingSettings`, `ArchetypeSettings`,
`PatchPolicy` (the last one governs statistical-DB patch fallback for the
training pipeline, not yet built). This is where the CLAUDE.md rule "config
values... never hard-coded" is enforced — DB path, external API base URLs,
timeouts, rate limits, politeness delays, and clustering/naming thresholds
all live here, not scattered through ingestion/knowledge code.
`RiotApiSettings.api_key` and `LlmTaggingSettings.api_key` are the fields
sourced from outside this file (`RIOT_API_KEY`/`ANTHROPIC_API_KEY`
environment variables) rather than a literal default, since they're
secrets, never committed. Two vocabulary/rule modules live separately from
`settings.py` since they're not runtime settings: `config/taxonomy.py`
(`SEMANTIC_TAGS`, `RATING_NAMES` — the knowledge/ pipeline's fixed tag/
rating vocabulary) and `config/archetype_rules.py` (`RATING_TAG_MAP`,
`ARCHETYPE_NAME_BY_TAG`, `AD_STAT_KEYS`/`AP_STAT_KEYS` — `knowledge/
archetypes/`'s derivation rules built on top of that vocabulary).

## Testing

`tests/conftest.py::session` gives an in-memory SQLite DB via
`Base.metadata.create_all()` (not migrations — faster, and schema-truth
still comes from the models). No test hits real network: `ingestion/*`
client functions are monkeypatched at the module level, and
`knowledge/client.py`'s `_call_anthropic` the same way (`tests/
test_knowledge_*.py`). The wiki parser tests run against real captured
wikitext fixtures (`tests/fixtures/wiki/*.wikitext`) as well as synthetic
cases, since real wikitext has quirks (stray HTML comments, inconsistent
field presence) that hand-written fixtures don't reliably surface.
`tests/test_archetypes_*.py` need neither network nor LLM monkeypatching —
build-archetype extraction is purely deterministic numerical logic
(clustering, naming, deltas), so those tests exercise the real functions
directly against seeded rows; where a test needs a small threshold (e.g.
`min_builds_per_champion_role`) rather than dozens of rows, it monkeypatches
`config.settings.ARCHETYPES` at each importing module's own name (each
module binds its own reference at import time, so patching
`config.settings.ARCHETYPES` itself wouldn't reach already-imported code).

## Known gaps / next steps

Per `docs/sepc.md`'s Phase 1 scope, still outstanding:

- ~~Semantic tag / numeric rating pipeline~~ — **done**: `knowledge/`
  populates `champion_ratings` and the `'llm'`/`'override'` side of
  `champion_tags`/`item_tags`/`rune_tags` (see the `knowledge/` section
  above). No live rows exist in this environment (needs an
  `ANTHROPIC_API_KEY`). `item_effects` (structured passive/active effect
  breakdown) remains out of scope — separate parsing task, not tied to this
  pipeline in the spec's wording.
- **Statistical DB** (Component 2) — match capture, win/pick/ban rate,
  ally/enemy pairing, final-build item/rune win rates, itemization-counter
  stats, and completed-item/skill-order build stats done (`riot_api`:
  `matches`, `match_participants`, `matchup_statistics`, `champion_synergy`,
  `champion_counters`, `item_statistics`, `rune_statistics`,
  `item_counter_statistics`, `match_timelines`, `build_path_statistics`,
  `skill_order_statistics`, see above), but no live data has been ingested
  in this environment (needs a `RIOT_API_KEY`). Still outstanding within
  Component 2: stat-shard perk stats, game-duration splits, multi-region
  crawling, historical-patch backfill. Lolalytics as a fallback source is
  **not pursued as an automated crawl** — its `robots.txt` explicitly
  disallows `ClaudeBot` (and other named AI crawlers), so
  `item_counter_statistics` above computes the equivalent
  "itemization-vs-matchup" signal from Riot match data instead, per
  `docs/sepc.md`'s Data Sources rule; a Lolalytics fallback would need a
  human-operated collection step, not an ingestion module.
- **OTP DB** (Component 3) — one-trick identification and build capture
  done (`otp`: `otp_players`, `otp_builds`, see above), but no live data
  has been ingested in this environment (needs a `RIOT_API_KEY`) and no
  live data exists to confirm real-world mastery/concentration thresholds
  are well-calibrated. Derived directly from the Riot API rather than
  OneTricks.gg: its `robots.txt` returns HTTP 429 on every fetch attempt
  (both `www.onetricks.gg` and bare `onetricks.gg`), a harder block than
  Lolalytics, so per the same precedent as `item_counter_statistics`, the
  one-trick signal (Champion-Mastery-V4 point concentration) and their
  builds (Match-V5, same as `riot_api`) are captured directly instead.
- ~~Build archetype extraction~~ — **done**: `knowledge/archetypes/`
  clusters `match_participants` + `otp_builds` into `build_archetypes` +
  `archetype_items`/`archetype_runes`/`archetype_tags` (see the
  `knowledge/archetypes/` section above). This was the last empty part of
  the knowledge DB. No live output exists in this environment (zero
  `matchup_statistics` rows without a `RIOT_API_KEY` means zero
  champion+role pairs clear the viability threshold); the clustering
  distance threshold and frequency/weight thresholds
  (`config.settings.ArchetypeSettings`) are unverified against real data,
  same caveat as `OtpSettings`' mastery thresholds.
- **`docs/data_report.md`** — Phase 1's "done when" criterion requires it
  (coverage, gaps, sample sizes across all three DBs) and it doesn't exist
  yet. With every Phase 1 pipeline now built (modulo the gaps below) and a
  `RIOT_API_KEY` still needed to actually populate the statistical/OTP/
  archetype data this environment lacks, this is the natural next step once
  real data exists to report on — or the report can honestly state "0 rows,
  pipeline untested against production data" if written before that.
- **Wiki Counters-table parsing** — decode `Template:Ctable`'s legend.
- **Runes, mechanics, minions, monsters wiki enrichment** — each needs its
  own page-structure discovery pass, the same way champions and items were
  discovered. Items are now done (see `wiki` above); runes/mechanics/
  minions/monsters are not.
- **Item stat block units** — `Item.stats` stores Data Dragon's raw flat
  keys (`FlatPhysicalDamageMod`, etc.) verbatim; no normalization to a
  common per-stat vocabulary yet (needed before cross-item comparisons like
  "most stat value per gold" are computable).
