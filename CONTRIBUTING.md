# Contributing

Thanks for your interest. This project is an evidence-grounded, robot-agnostic
robot incident investigation layer; contributions that keep the verdicts honest
and the data private are very welcome.

## Setup

Python 3.10+:

```
pip install -r requirements-dev.txt
```

Then follow [`docs/quickstart_ros1.md`](docs/quickstart_ros1.md) — the synthetic
path needs no robot data and proves the full chain on your machine.

## Tests — all gates must pass

```
python tools/test_extractor.py      # extractor unit tests
python tools/eval_extractor.py      # the canonical oracle: bags -> extract -> spec -> verifier
python tools/eval_scenarios.py      # legacy hand-authored annotations path
python tools/validate_incident.py   # frozen hero assets + rules
python backend/test_backend.py      # real HTTP server (needs a local socket)
```

## Privacy rules — non-negotiable

- Never commit `*.local.json`, `private_eval/`, or any real robot bag.
- Real topic names, log text, diagnostic names, error codes, and TF frame ids
  must not appear anywhere public: code, fixtures, tests, issues, or PRs. Share
  redacted summaries only.
- Verdict correctness is validated exclusively on the synthetic oracle. Real
  bags are for local plumbing smoke tests; they have no public ground truth and
  never become fixtures.
- Lessons from private data are contributed as abstract capabilities (a new
  matcher kind, a new degradation rule), never as real-world results.

## Contribution patterns

- **New incident type** — write a spec (`specs/*.json`), a synthetic scenario
  (`scenarios/*.json`) with expected verdicts, and add it to the eval. No
  verifier or extractor changes should be needed; see
  [`docs/incident_spec.md`](docs/incident_spec.md).
- **New robot** — write a topic mapping profile; commit only generic examples
  (`profiles/*.example.json`), keep fleet-specific mappings local. See
  [`docs/topic_mapping.md`](docs/topic_mapping.md).
- **New extract kind or event matcher** — the extractor stays mechanical:
  it derives neutral metrics/events; thresholds and logic belong in specs.
- **The verifier is frozen.** `tools/incident_rules.py` is the deterministic
  core that every verdict depends on; changes to it need strong justification
  and the full oracle green before and after.
- **Honest degradation is the design.** Missing evidence must lower a verdict,
  never inflate it — `low/ok` on incomplete data is correct behavior, and
  gates that lock this in (fixtures asserting `low`/`medium`) are as valuable
  as features.

## PR expectations

- One topic per PR, with all gates green.
- English commit messages and code comments.
- Behavior changes come with their gate (a scenario, fixture, or test that
  fails without the change) and with the affected docs updated in the same PR.
- Synthetic data only in fixtures — see the privacy rules above.
