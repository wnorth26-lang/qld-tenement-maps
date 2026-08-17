# AEL Map Studio production boundary

## Implemented slice

`map_spec.py` and `map_service.py` turn the prototype renderer into a bounded
production component. The public boundary is:

```text
generate_tenement_map(map_spec) -> manifest, artifacts, provenance, warnings
```

The renderer does not accept prose or prompts. An AI assistant may translate a
request into the versioned JSON schema, but only enumerated settings reach the
renderer. Unknown fields, unknown layers, unsupported map types and invalid
conditional combinations are rejected.

Version 1 supports official live Queensland EPMs, locality maps, annual
sub-block maps and partial-relinquishment maps. It deliberately does not accept
arbitrary uploaded geometry at the service boundary.

The manifest records the canonical specification hash, renderer version,
official tenement resolution, map CRS/zone/scale, artifact hashes, source URLs,
published source dates where exposed, source copyright text, per-layer render
results and warnings. `map_date` is explicit and PDF creation timestamps are
suppressed so repeated rendering against unchanged source responses is stable.

`tenement_not_found` is a verified zero and is distinct from `source_failure`.
Optional layers report `matches`, `verified_zero` or `source_failure`; missing
context therefore cannot silently look like a complete evidence map.

## Licensing boundary

Queensland service metadata currently identifies the source as State of
Queensland/Department of Resources material. The exact copyright text and
dataset date exposed by each selected layer are captured in every manifest.
Before commercial launch, the product owner must verify the applicable licence
for every catalogued layer and retain the required State attribution on or with
each map.

Esri World Imagery and Light Gray basemaps have separate Esri and data-supplier
terms. Static output requires visible attribution, and direct resale or
commercial monetisation of basemap-derived maps needs a documented licence
review. `basemap: none` is the safest default until that review is complete.

The repository itself has no software licence file. Do not represent its source
code as open-source until the owner adds an explicit licence.

## Deployment audit

The existing Streamlit app remains a useful demonstration UI, not the
production architecture. Community Cloud is unsuitable for a commercial flow
that processes client identity/email data or needs durable jobs, reliable
webhooks and controlled artifact retention. The production renderer should run
as a headless worker using the Agg backend.

Later infrastructure, intentionally excluded from this slice:

- authenticated accounts, organisations and saved house-style presets;
- a durable job queue with idempotency keys, retries and worker timeouts;
- object storage, signed download URLs, retention and deletion policies;
- payment checkout, webhook handling, refunds and billable-state settlement;
- email delivery through a transactional provider;
- rate limits, quotas, observability and operational alerting;
- malware scanning and policy controls if uploaded geometry is reintroduced;
- a reviewed catalogue of Queensland and Esri licence/attribution rules.

Payment must settle only after artifact hashes and a complete manifest exist.
Invalid specs, verified-zero tenements, required-source failures and render
failures are non-billable.

## Tests

`test_map_service.py` covers the closed schema, conditional validation,
deterministic artifact/spec hashes, source provenance dates, verified-zero
tenements, sanitised source failures and the non-generative rendering boundary.
