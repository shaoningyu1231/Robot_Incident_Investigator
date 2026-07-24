# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [v0.2.0] - 2026-07-24

### Added

- Robot-agnostic incident specs (`specs/`) and topic mapping profiles
  (`profiles/`): incident logic references abstract `EVENT_*` codes and neutral
  metrics; robot-specific topics, codes, and TF frame ids live in profiles.
- Profile-driven neutral extractor for ROS1 bags (`tools/extract_incident.py`):
  metrics with explicit resampling, abstract event matchers
  (json_string_event / rosout_text / diagnostic_status), structured
  count-only warnings.
- Real-compatible spec compilation with honest degradation: event-derived
  search windows with declared fallback, `required: if_available` sources
  capped at `medium`, `positive_required` observations (stop + halt with
  all-clear sensors verifies `low`, not `high`), and `abs` halt semantics
  (reverse motion is not a halt).
- Agent-facing deterministic tools: `verify_conclusion` (request-time spec
  compilation for derived / declared / overridden hypothesis windows) and
  `list_incident_candidates` (one candidate per anchor-event occurrence, with
  repeated-publish coalescing).
- Private real-bag smoke test (`tools/private_eval_smoke.py`): a repeatable
  local-only acceptance flow with a PASS/FAIL checklist and redacted summaries.
- Rerun-linked investigation mode: cited timestamps seek an embedded Rerun web
  viewer (dev-only, synthetic data).
- TF jump signal support (`tf_jump` extract kind) and a second incident spec,
  `specs/localization_jump.json` — a new incident type is a spec, not a code
  change.
- Generic ROS1 quickstart (`docs/quickstart_ros1.md`) covering the synthetic
  demo, private-bag smoke, and Rerun paths.

### Changed

- Project positioning expanded from a hackathon demo to an evidence-grounded,
  robot-agnostic incident investigation layer; the deterministic verifier is
  unchanged throughout.

### Security / Privacy

- Private profiles (`*.local.json`), bags, and extraction outputs remain
  gitignored; the smoke test refuses non-gitignored output directories.
- Smoke summaries are self-checked and refused if they would contain any
  profile string: topics, msgtypes, matcher values, or TF frame ids.

## [v0.1.0] - 2026-06-27

### Added

- Hackathon-winning Gemini demo (Gemini AI Hackathon @ Google Japan).
- Synthetic obstacle-stop incident with exported multimodal assets (LiDAR
  frames, telemetry charts, structured logs).
- Deterministic evidence verification (`evidence_strength`, conflict
  detection, recovery-readiness checks) shared by the server and the eval
  harness.
- Multimodal Gemini function-calling investigation flow with SSE streaming and
  multi-turn history.
- Cloud Run demo deployment and a deterministic offline fallback.
