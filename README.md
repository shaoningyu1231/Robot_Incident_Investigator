# Robot Incident Investigator

[![CI](https://github.com/shaoningyu1231/Robot_Incident_Investigator/actions/workflows/ci.yml/badge.svg)](https://github.com/shaoningyu1231/Robot_Incident_Investigator/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](CONTRIBUTING.md)

**Evidence-grounded, robot-agnostic incident investigation for autonomous robots.**

Ask *why a robot stopped* and get a grounded, timestamped answer. Incident logic
lives in reusable **specs**, robot specifics live in **topic mapping profiles**,
a profile-driven **extractor** turns ROS1 bags into neutral evidence, and a small
**deterministic verifier** decides how strong that evidence is
(`high / medium / low`, `ok / conflicting`). An LLM narrates the investigation;
the rules verify it — and missing evidence lowers a verdict, never inflates it.

Born as the winning entry of the Gemini AI Hackathon @ Google Japan; now a
generalized open-source investigation layer (the hosted Gemini demo is one
frontend over it, not its boundary).

![Rerun-linked investigation: clicking a cited timestamp in the answer seeks the embedded Rerun viewer](docs/rerun_linked.gif)

- **Quickstart (~10 min, three paths):** [`docs/quickstart_ros1.md`](docs/quickstart_ros1.md)
- **Deep dives:** [`docs/incident_spec.md`](docs/incident_spec.md) · [`docs/topic_mapping.md`](docs/topic_mapping.md)

## Why it matters

When a robot stops, engineers dig through bags, logs, LiDAR frames, and velocity
plots by hand — slow, error-prone, and inaccessible to non-experts. Worse, an
LLM asked "why did it stop" will happily invent a confident answer. This project
turns the digging into an interactive investigation anyone can drive in natural
language, with every claim anchored to deterministic evidence checks.

## How it works

```
your ROS1 bag  +  topic mapping profile   (robot-specific; real names stay private)
      │
      ▼
extractor  ->  neutral metrics + abstract EVENT_* events      (no raw text, no real codes)
      │
      ▼
incident spec  ->  evidence compiled for a hypothesis window  (robot-agnostic, reusable)
      │
      ▼
deterministic verifier  ->  evidence_strength + recovery readiness
      │
      ▼
agent tools  ->  list_incident_candidates / verify_conclusion / inspect / search_logs
```

- **A new incident type is a spec, not a code change** — obstacle stop and
  localization (TF) jump ship today, on the same compiler and the same
  unchanged verifier.
- **Honest degradation by construction** — a single distance source caps at
  `medium`; a stop event plus a halt with all-clear sensors verifies `low`;
  reverse motion never satisfies a halt check; an absent anchor event falls
  back gracefully instead of crashing.
- **Multi-event recordings** — candidate discovery lists one investigable
  window per stop event (with repeated-publish coalescing), and each candidate
  is verified independently.

## Evaluated across scenarios (not one scripted case)

| scenario | verdict | what it proves |
|---|---|---|
| obstacle stop | `high/ok` | confirmed obstacle safety stop |
| planned stop | `low/ok` | **true-negative** — a normal stop is not misreported |
| sensor disagreement | `conflicting → insufficient` | **anti-hallucination** on contradictory sensors |
| localization jump | `high/ok` | second incident type, spec-only |
| stop without TF jump | `low/ok` | the jump is required positive evidence |
| two stop cycles | `high` then `low` | candidate discovery discriminates per window |
| recovery `[12,18]` / `[19,25]` | `blocked` / `conditions_met` | state changes as the obstacle clears |

## Verify it yourself (reproducible)

```
python tools/validate_incident.py     # data + rules
python backend/test_backend.py        # real uvicorn + HTTP/SSE, agent tools
python tools/eval_scenarios.py        # scenario discrimination
python tools/test_extractor.py        # extractor unit tests
python tools/eval_extractor.py        # profile -> extract -> spec -> verifier, declared + derived windows + fixtures
GEMINI_API_KEY=... python backend/online_check.py   # live Gemini acceptance
```

## Privacy and data boundary

All committed data is **fully synthetic** — no real error codes, bag names,
topic naming, or internal system details. Your own robot's data never needs to
leave your machine: real topics, event codes, and TF frame ids live only in
git-ignored `*.local.json` profiles, private smoke outputs stay under
`private_eval/`, and the redacted summary is refused if it would contain any
profile string. Verdict correctness is validated on the synthetic oracle only;
see [`CONTRIBUTING.md`](CONTRIBUTING.md) for the non-negotiables.

## Responsible AI / safety boundary

The system **does not issue safety certifications**. The model investigates and
explains; deterministic rules compute evidence strength and recovery-condition
readiness. `evidence_strength` reflects evidence completeness and consistency,
**not** a probability that the root cause is correct. Contradictory sensors
yield *insufficient evidence* rather than an invented answer.

## Hosted demo — Gemini on Cloud Run (demo implementation, optional)

The hosted demo is optional; the recommended path is local-first (see the
quickstart above). The demo app — originally built for the hackathon, hosted
separately when available — is a Starlette backend driving a Gemini
(`gemini-2.5-flash`) function-calling loop: multimodal `inlineData` image parts
(LiDAR + chart PNGs are sent back to the model), SSE streaming tool progress,
multi-turn follow-ups, and a deterministic offline fallback when Gemini or the
network is unavailable. It is one frontend over the deterministic layer above —
swap the model or the UI and the evidence rules stay the same. Run it yourself:

```
GEMINI_API_KEY="$(cat ~/.gemini_key)" PORT=8000 python backend/app.py   # http://127.0.0.1:8000
```

`/health` should report `integrity_ok:true` and `gemini:true`.

## Rerun-linked investigation (optional, local)

Run the investigation next to an embedded [Rerun](https://github.com/rerun-io/rerun)
viewer (the GIF above): ask what happened, then click a cited timestamp in the
answer and the viewer time cursor jumps to that instant — LiDAR, telemetry,
event log, and evidence markers all scrub together.

This runs **locally** — the 47 MB viewer and the `.rrd` are dev-only and
git-ignored, so the public Cloud Run demo stays lightweight:

```
pip install -r requirements-dev.txt          # includes rerun-sdk (dev only)
python tools/export_to_rerun.py              # synthetic incident -> rerun_build/*.rrd
python tools/prepare_rerun_web_assets.py     # stage viewer + .rrd into backend/static/ (git-ignored)
GEMINI_API_KEY=... PORT=8000 python backend/app.py   # then open http://localhost:8000/rerun
```

No bundler: the `/rerun` page loads the viewer through an ES module import map.
Or skip the server and open the recording in the standalone viewer:

```
python -m rerun rerun_build/demo_obstacle_stop_01.rrd    # add --web-viewer if headless
```

The exporter reuses the same synthetic source as the incident assets, so the
recording cannot drift from `timeline.json`. Rerun is an **optional dev
dependency**, **not** part of the Cloud Run runtime; it is open source under
permissive licenses (MIT OR Apache-2.0) — see
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
