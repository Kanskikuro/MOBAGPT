# Architecture

What's actually implemented so far, as of patch `16.13.1`. For the full
product vision and phased delivery plan, see [docs/sepc.md](sepc.md); this
document only covers Phase 1's knowledge-DB work and how it's built.

## Database

One SQLite file (`data/knowledge.db`, gitignored — regenerate it by running
migrations + ingestion), one normalized schema. `docs/sepc.md`'s "Database"
section lists knowledge, statistical, and OTP tables together as a single
schema rather than separate physical databases; only the knowledge tables
exist today. Schema is managed by Alembic (`migrations/`), not
`Base.metadata.create_all()` — every schema change is a migration.

The CLAUDE.md hard rule that `/model` never imports the statistical-DB query
layer will be enforced by which modules import which query-layer code once
both exist, not by physical file separation (there's no `/model` yet).

### Tables populated today

| Table | Populated by | Notes |
|---|---|---|
| `patches` | `data_dragon` | one row per ingested Data Dragon version |
| `champions` | `data_dragon` | numeric id, riot key, display name, normalized name, title |
| `champion_stats` | `data_dragon` | base/per-level stats + Riot's own 1-10 attack/defense/magic/difficulty ratings |
| `champion_tags` | `data_dragon` (`source='data_dragon'`) | Riot's coarse tags (Fighter, Mage, ...) |
| `champion_abilities` | `data_dragon` | passive + spells; **rows are unstable across re-runs** (delete-then-reinsert, see below) |
| `champion_ability_details` | `wiki` (`source='wiki'`) and `manual` (`source='manual'`) | exact scalings, hidden mechanics, tips — see below |
| `items` | `data_dragon` | includes `stats` (flat stat mods), `depth`/`builds_from`/`builds_into` (build path) |
| `item_tags` | `data_dragon` (`source='data_dragon'`) | |
| `runes` | `data_dragon` | |

### Tables that exist but are still empty

`champion_ratings`, `rune_tags`, `item_effects`, `build_archetypes` +
`archetype_items`/`archetype_runes`/`archetype_tags` — all depend on
pipelines that don't exist yet (semantic tag/rating extraction, build
archetype clustering from statistical + OTP data). Their schema is defined
now so downstream code can be written against a stable shape, per the same
pattern as everything else in this schema.

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

**Currently loaded:** Ambessa (6 rows). Pending: Locke, Mel, Yunara, Zaahen
— same process once transcribed.

## Config (`config/settings.py`)

Frozen dataclasses, one per concern: `DataDragonSettings`, `WikiSettings`,
`PatchPolicy` (the last one belongs to the not-yet-built statistical DB).
This is where the CLAUDE.md rule "config values... never hard-coded" is
enforced — DB path, external API base URLs, timeouts, and politeness
delays all live here, not scattered through ingestion code.

## Testing

`tests/conftest.py::session` gives an in-memory SQLite DB via
`Base.metadata.create_all()` (not migrations — faster, and schema-truth
still comes from the models). No test hits real network: `ingestion/*`
client functions are monkeypatched at the module level. The wiki parser
tests run against real captured wikitext fixtures
(`tests/fixtures/wiki/*.wikitext`) as well as synthetic cases, since real
wikitext has quirks (stray HTML comments, inconsistent field presence) that
hand-written fixtures don't reliably surface.

## Known gaps / next steps

Per `docs/sepc.md`'s Phase 1 scope, still outstanding:

- **Semantic tag / numeric rating pipeline** (`champion_ratings`, the
  `'llm'`/`'override'` side of `champion_tags`/`rune_tags`) — LLM extraction
  from ability/item/rune text plus a manual override file that always wins.
- **Statistical DB** (Component 2) — Riot API primary source, Lolalytics as
  an isolated, ToS-checked fallback.
- **OTP DB** (Component 3) — OneTricks.gg.
- **Build archetype extraction** — needs both of the above.
- **Wiki Counters-table parsing** — decode `Template:Ctable`'s legend.
- **Runes, mechanics, minions, monsters wiki enrichment** — each needs its
  own page-structure discovery pass, the same way champions and items were
  discovered. Items are now done (see `wiki` above); runes/mechanics/
  minions/monsters are not.
- **Item stat block units** — `Item.stats` stores Data Dragon's raw flat
  keys (`FlatPhysicalDamageMod`, etc.) verbatim; no normalization to a
  common per-stat vocabulary yet (needed before cross-item comparisons like
  "most stat value per gold" are computable).
