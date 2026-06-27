# Robot Incident Investigator — Submission

**Project name:** Robot Incident Investigator

**One-liner:** Ask why an autonomous robot stopped and get a grounded, evidence-backed
answer — a multimodal Gemini agent that investigates LiDAR, motion telemetry, and event
logs, cites timestamps, and refuses to overclaim when evidence is missing or conflicting.

## Problem
When a robot stops, engineers dig through bags, logs, plots, and sensor frames. It is slow
and inaccessible to non-experts. This turns it into an interactive investigation.

## What it does
- Ask in natural language: "Why did it stop?" / "What must be satisfied before it resumes?"
- Gemini runs a tool-using investigation loop: it picks the time window, calls
  `inspect_incident_window`, receives **LiDAR and chart PNGs as multimodal image parts**,
  and explains the root cause with clickable evidence timestamps.
- Deterministic backend rules verify `evidence_strength` and `recovery_readiness`; the model
  narrates, the rules verify. No invented confidence, no safety certification.

## Google Cloud usage (eligibility)
- **Gemini API** (`gemini-2.5-flash`): multimodal function-calling investigation loop —
  function declarations, `inlineData` image parts (LiDAR/charts), multi-turn follow-ups.
- Deployed on **Cloud Run**.

## Tech highlights
- Real tool-use loop (not a single prompt): function calling + image parts + SSE streaming
  progress + multi-turn history + deterministic offline fallback.
- Shared deterministic rule engine (`incident_rules.py`) used by both the backend and the
  test/eval harness — single source of truth.
- Material Design 3 dark UI: synchronized timeline (LiDAR + telemetry charts), clickable
  evidence cards and timestamps, live tool-call stepper, cancel/retry/timeout.

## Evaluated (not one scripted case)
- `obstacle_stop` → high (confirmed obstacle safety stop)
- `planned_stop` → low (true-negative: a normal planned stop is NOT misreported)
- `sensor_disagreement` → conflicting → insufficient evidence (anti-hallucination)
- recovery `[12,18]` → blocked, `[19,25]` → conditions_met (state changes as obstacle clears)

Verification baselines: data 20/20 · backend 30/30 (real HTTP/SSE) · online 6/6 · scenario
eval 3/3 + recovery 2/2.

## Data boundary
All demo data is **fully synthetic**, inspired by common AMR failure patterns. It is a real
ROS1 bag with standard topics (scan / odom / cmd velocity / logs) but uses no real error
codes, bag names, topic naming, or internal system details, and exposes no customer, company,
or robot data.

## Links
- Deployed app (Cloud Run): `<FILL AFTER DEPLOY>`
- GitHub (public): `<FILL AFTER PUSH>`

## Run locally
```
GEMINI_API_KEY="$(cat ~/.gemini_key)" PORT=8000 python backend/app.py   # http://127.0.0.1:8000
```
Health check expects `integrity_ok:true`, `gemini:true`.
