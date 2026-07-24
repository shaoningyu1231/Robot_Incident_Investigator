# ROS1 quickstart

From clone to verified verdicts in about ten minutes. Three independent paths:
a fully synthetic demo (no bag needed), your own private ROS1 bag (local-only),
and an optional Rerun visualization. Nothing here sends data anywhere.

How the pipeline fits together: a **topic mapping profile** maps your robot's
topics onto neutral roles ([`topic_mapping.md`](topic_mapping.md)); the
**extractor** emits neutral metrics and abstract `EVENT_*` events; an
**incident spec** compiles them into evidence
([`incident_spec.md`](incident_spec.md)); a small **deterministic verifier**
decides `high` / `medium` / `low` and `ok` / `conflicting`. The agent layer
(candidate discovery, hypothesis-window verification) sits on top of the same
deterministic tools.

## Prerequisites

Python 3.10+ and the dev dependencies (`rosbags`, `numpy`, `Pillow`, plus the
optional `rerun-sdk`; the runtime server deps come along via `requirements.txt`):

```
pip install -r requirements-dev.txt
```

## Path A — synthetic demo (no bag needed)

Everything is generated; these are also the project's correctness gates:

```
python tools/test_extractor.py      # extractor unit tests
python tools/eval_extractor.py      # bags -> extract -> spec -> verifier (the canonical oracle)
python tools/eval_scenarios.py      # legacy hand-authored annotations path
python tools/validate_incident.py   # frozen hero incident assets + rules
python backend/test_backend.py      # real HTTP server, agent tools, SSE
```

All green means the full chain works on your machine: one obstacle-stop spec
discriminates a real obstacle stop (`high/ok`) from a planned stop (`low/ok`)
and from contradicting sensors (`low/conflicting`), a localization-jump spec
does the same for TF discontinuities, and candidate discovery separates two
stop cycles in one recording.

To see the intermediate representation with your own eyes:

```
python tools/generate_synthetic_bag.py eval_build/demo.bag
python tools/extract_incident.py --bag eval_build/demo.bag \
    --profile profiles/synthetic_demo.example.json --out eval_build/demo_extract
```

`eval_build/demo_extract/` then holds the three neutral files every downstream
step consumes: `timeline.json` (resampled metrics), `logs.jsonl` (abstract
events only), `metadata.json` (resample stats + structured warnings).

## Path B — your own ROS1 bag (private, local-only)

Your bag and everything derived from it stays on your machine. The flow is
discover → map → smoke.

Discover what is in the bag (topic names are hashed in the printable manifest;
the real names go to a git-ignored local file):

```
python tools/private_eval_real_bag.py --bag /path/your.bag --out private_eval/case_001
```

Map your topics onto the neutral roles. Start from the committed generic
example and fill it with real names from `topic_candidates.local.txt`:

```
cp profiles/generic_ros1.example.json private_eval/case_001/topic_mapping.local.json
```

Edit the copy locally: real topic names, msgtypes, event matchers (map your
robot's real stop/clear codes or log lines to abstract `EVENT_*` codes), and —
if you have TF — the real `parent_frame` / `child_frame` of your localization
transform. Optional but recommended: mark each role/event with
`"_status": "confirmed"` or `"provisional"` and note semantic caveats in
`"_caveats"` — the smoke test reports counts only, never the text. See
[`topic_mapping.md`](topic_mapping.md) for every field.

Run the repeatable private acceptance flow:

```
python tools/private_eval_smoke.py --bag /path/your.bag \
    --profile private_eval/case_001/topic_mapping.local.json \
    --out private_eval/case_001/smoke
```

It extracts, lists incident candidates, verifies each candidate window, and
writes `summary.redacted.json` plus a PASS/FAIL checklist (nonzero exit on any
failure — usable as a local regression gate). Success means the pipeline is
plumbed and degrades honestly; it does not mean, and must never be tuned to
mean, a `high` verdict.

## Path C — optional Rerun visualization

Run the investigation next to an embedded [Rerun](https://github.com/rerun-io/rerun)
viewer; clicking a cited timestamp seeks the viewer. Dev-only and git-ignored,
synthetic data only:

```
python tools/export_to_rerun.py
python tools/prepare_rerun_web_assets.py
PORT=8000 python backend/app.py      # then open http://localhost:8000/rerun
```

## Privacy boundary

- `*.local.json` and `private_eval/` are git-ignored; never commit them. The
  smoke test refuses to run if its output directory is not git-ignored.
- The redacted summary contains no topic names, no raw log or diagnostic text,
  no real error codes, and no TF frame ids — a built-in self-check refuses to
  write a summary containing any string from your profile.
- TF frame ids are private: they live only in your `*.local.json`, like topics.
- Never route a private bag, profile, or extraction output through any cloud
  service, LLM, or assistant. Verdict correctness is validated on the synthetic
  oracle only; a real bag has no ground truth.

## Troubleshooting

- `missing_role_connection` warning — a mapped role's topic is not in the bag.
  This is graceful degradation, not an error: the dependent signal degrades and
  the verdict drops toward `low`. The synthetic demo itself shows one (the hero
  bag has no tf topic). Fix the topic name in your local profile if the topic
  should exist.
- `unsupported_msgtype` / `deserialize_failed` warnings — the role's declared
  `msgtype` does not match the bag, or the message cannot be decoded. Check the
  msgtype column in `topic_candidates.local.txt`.
- `list_incident_candidates count >= 1` fails — no abstract stop event was
  derived, so your event matchers did not match anything. Check the matcher
  `kind` / `op` / `value` against your real stop log line or code, and remember
  only the abstract `output_code` is ever emitted.
- `low/ok` is not failure — it is the honest verdict when required evidence is
  missing (no halt observed, no positive obstacle/jump observation, or a
  corroborating source your robot simply does not have). The system is designed
  to refuse to promote such cases; see the honest-degradation section of
  [`incident_spec.md`](incident_spec.md).
- Repeated fault publishes flooding the candidate list — set
  `coalesce_window_s` in the spec's `search_window_strategy`.
- Rerun page says "not prepared" — run the two Path C commands first; the
  viewer assets are git-ignored by design.
