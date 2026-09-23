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
The threshold is conservative policy, not calibrated accuracy. Uncached category and
coverage questions are sent together in one request with a 15-second budget and
no transport retry. Cached groups are omitted. All response groups must validate
before new answers are cached; previously cached groups survive provider failure.

The writer produces only `ai_summary` when Jev is active. With Jev disabled it
may also classify an unlocked category. Time, venue, city, organizer, admission,
availability and series are always assembled from sanitized facts in code, as
before, rather than generated a second time. All existing text-quality checks
remain mandatory, and accepted cached summaries are reused unchanged.

On a failed summary, Jev can replace a full writer retry with a narrowly scoped
removal decision. Only sentences nominated by existing local promotion, sponsor,
health-claim, unsupported admission/registration or audience checks are eligible.
Before any Jev request or cache lookup, the proposed remainder must pass the full
local validator with the original source material and publication occurrence facts.
Too-short, copied or otherwise invalid remainders go directly to the writer retry
without paying for an unusable semantic approval.
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

## Typesafe documentation review (20 September 2026)

Reviewed the official [introduction](https://docs.typesafe.ai/introduction),
[Choice](https://docs.typesafe.ai/primitives/choice),
[Noul](https://docs.typesafe.ai/primitives/noul),
[structured questions](https://docs.typesafe.ai/primitives/advanced),
[fan-out](https://docs.typesafe.ai/patterns/fan-out) and
[confidence](https://docs.typesafe.ai/confidence) guidance.

- Keep code in control; use the typed Decisions endpoint, not chat JSON prompting.
  Direct HTTP is supported; changing SDK or credentials is unnecessary.
- Batch the independent unresolved category and extraction-routing questions.
  Jev evaluates each question independently; it cannot see other question ids or
  instructions. The shared state contains the full semantic event material.
  Coverage has its exact candidate occurrence facts in structured instructions;
  category cannot see those facts. This keeps the independently cached category
  reusable across dates without weakening the occurrence-specific facts key.
- Describe category boundaries in English, not just German display labels. Keep
  every category and explicit `other`/`unknown` exits. Do not translate or shorten
  original German source material. Ambiguous activity/audience combinations abstain.
- Count usage once per API request, never once per returned question or cache group.
  Internal routing metadata retains answers, probabilities and provider confidence
  for diagnosis; it is stripped before generative writing and public output.
- The category's chosen-option probability threshold remains 0.98. Probability
  and provider `confidence` are different quantities; the official docs explicitly
  permit using the distribution directly. This threshold is an application policy,
  not an empirically established 98 percent accuracy guarantee.

An evaluated alternative split coverage into three Nouls (missing information,
contradiction, concrete occurrence) and converted sentence repair to a Noul about
lost supported facts. The docs recommend Noul for binary propositions, but this
model's conservative probability gate rejected the previously accepted safe
repair (0.06 probability of information loss, above the 0.02 ceiling). The mixed
fact/invalid-claim case correctly remained a rewrite. Do not enable that migration
or relax thresholds simply to follow the primitive recommendation. The production
Choice contracts retain the tested action routing and safe fallback. Coverage is
restricted to short near-repetitions; complicated material goes to extraction.
Revisit atomic Noul decomposition with a representative labeled evaluation set.

Validation includes a live before/after request comparison, six synthetic safety
and cache cases, and shadow classification of eight source-backed snapshot events.
Shadow results are diagnostics only: they do not overwrite source categories or
publish events. Provider probes are not a calibrated accuracy or speed benchmark.
