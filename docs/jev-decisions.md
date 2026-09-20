# Jev structured decisions

`nrw_events.decisions.OpenRouterDecisionClient` provides Jev through OpenRouter's
Decisions endpoint, separately from generative enrichment. It supports `noul`,
`choice` and `score`, including structured rubric descriptions. The enrichment router uses this client on eligible cache misses; successful
existing summaries and extracted facts are reused unchanged.

```python
from nrw_events.config import load_env_file
from nrw_events.decisions import OpenRouterDecisionClient

load_env_file()
result = OpenRouterDecisionClient().evaluate(
    state={"title": "Jazzkonzert"},
    questions={"music": {"type": "noul", "instructions": "Music event?"}},
)
probability = result["answers"]["music"]["noul"]
```

Run from this repository (the smoke script uses a synthetic event):

```sh
python3 scripts/test-jev-decisions.py --live
bash scripts/test.sh --agent tests.test_decisions
```

Explicit client credentials take precedence, then nonblank
`JEV_OPENROUTER_API_KEY`, then `OPENROUTER_API_KEY`. The model defaults to
`typesafe/jev-1.13`, overridable with `JEV_OPENROUTER_MODEL`. An explicitly blank
key/model fails. The dedicated key is in ignored local `.env` files.

Inputs are limited to 32,000 serialized UTF-8 bytes and JSON-compatible values.
Answers must match the requested names/types, probability distributions and
rubrics. Invalid output fails closed; there is no chat fallback. Errors contain
safe codes and optional HTTP status, never response bodies or request data.
Only transport failures and HTTP 408/429/5xx retry (default once, maximum three).
The default budget is 30 seconds; urllib uses the remaining time as its socket
timeout and the client checks the budget between attempts and after reading.
Unlike the TypeScript AbortController, urllib cannot cancel DNS resolution.
Consumers own acceptance thresholds and side effects.

On Dokploy this importer runs within `veranstaltungen-bonn-w4cxyt`, not as a
separate application. That application's encrypted environment contains the
JEV variables; its entrypoint forwards runtime variables to scheduled imports.
Saved environment changes take effect on the next deployment. This preparation
does not restart production or import events.

## Enrichment routing

`NRW_EVENTS_AI_JEV_ENABLED=true` enables the router when a JEV or OpenRouter
key is present. Set it to false to use the original pipeline. Jev is called only
after the existing summary/facts cache checks. Only unresolved questions are requested; classification and coverage have
separate reusable cache keys. No event is removed based on a Jev answer.

The router constructs facts only from existing structured fields. ISO dates
must be valid. Complex prices, vendor fees and invalid values retain generative
extraction. Simple explicit visitor prices retain their exact amount; missing
admission stays unknown. No source prose is shortened for routing.

When material equals the label-bound rendering of existing fields, completeness
is known from construction and no coverage request is made. This also covers
publication reattaching that rendering as private description text. With real
prose, a cheap overlap check asks Jev only about short near-repetitions; other
prose goes directly to extraction unchanged. This heuristic only avoids a
predictably negative routing request: it never authorizes skipping extraction.
A semantic coverage decision must select `complete` with probability at least
0.98. Timeouts, uncertainty and provider errors retain extraction.

Categories are a separate cached `choice` decision, requested only when the
existing category is not locked at confidence >= 0.75. Its cache includes source,
title, venue, city, organizer, series and complete semantic source text. It
excludes separate occurrence dates/times and label-bound fallback prose, allowing
identical recurring content to reuse classification. Dates embedded in real
prose are retained. Changes in programme, source, venue or title invalidate the
category decision; no occurrence facts, descriptions or identity guards are
shared by this cache. Uncertain categories retain the deterministic source
category. A structured record with a locked category makes no Jev request at all.
The threshold is conservative policy, not calibrated accuracy. Category and
coverage calls share one 15-second budget with no transport retry.

The writer produces only `ai_summary` when Jev is active. With Jev disabled it
may also classify an unlocked category. Time, venue, city, organizer, admission,
availability and series are always assembled from sanitized facts in code, as
before, rather than generated a second time. All existing text-quality checks
remain mandatory, and accepted cached summaries are reused unchanged.

On a failed summary, Jev can replace a full writer retry with a narrowly scoped
removal decision. Only sentences nominated by existing local promotion, sponsor,
health-claim, unsupported admission/registration or audience checks are eligible.
Jev must accept with >= 0.98 that deleting them loses no supported visitor fact.
Mixed factual/promotional sentences require rewriting. Copying and incomplete
sentences cannot use deletion. The entire edited summary then passes the original
validator again; uncertainty, errors, or another validation failure retain the
normal writer retry. The exact text, facts, error and rubric form the repair cache
key. Repair usage is charged with that writer attempt, and `_jev_repair` records
successful replacement in the internal cache only. This is not a text-length
optimization: valid sentences are preserved.

`ai_jev_decisions` caches validated answers, including extraction fallbacks, by
input, requested model and operation-specific rubric. `_jev` in cached stage-one
facts records the resolved model, rubric, category and whether extraction was
replaced; it is removed before writing. Token/cost usage is included in the
existing enrichment totals. INFO logs report routing outcomes and usage without
source text or credentials. The website release transfers the decision cache
alongside accepted summaries, preserving existing production decision rows.
