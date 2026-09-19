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
after the existing summary/facts cache checks. One request asks about fact
coverage and the category. No event is removed based on a Jev answer.

The router constructs facts only from existing structured fields. ISO dates
must be valid; source material is limited to 4,000 characters. Complex prices,
vendor fees and invalid values go straight to the generative extractor. Simple
explicit visitor prices retain their exact amount; missing admission stays unknown.

Without source prose, completeness is known from construction. With prose,
coverage must select `complete` with probability at least 0.98; otherwise the
original extraction runs. A category replaces the writer's classification only
at the same threshold. Existing locked categories still take precedence.
These thresholds are conservative routing policy, not a claim of calibrated
accuracy. A nine-case live probe retained extraction for every additional-fact,
contradiction, non-event and injected-instruction case; even a redundant prose
example fell back. The no-prose case skipped extraction. Broader savings must
be measured on actual cache misses, not extrapolated from this small probe.

Accepted facts still pass the existing sanitizer. The writer receives no source
prose and its output still passes all existing quality checks. Jev does not
replace text generation. Errors and timeouts fall back to extraction, without
using up extraction attempts. The batch budget reserves a slot for routing;
Jev uses at most 15 seconds and no transport retry in this path.

`ai_jev_decisions` caches validated answers, including extraction fallbacks, by
input, requested model, rubric and routing mode. `_jev` in cached stage-one
facts records the resolved model, rubric, category and whether extraction was
replaced; it is removed before writing. Token/cost usage is included in the
existing enrichment totals. INFO logs report routing outcomes and usage without
source text or credentials. The website release transfers the decision cache
alongside accepted summaries, preserving existing production decision rows.
