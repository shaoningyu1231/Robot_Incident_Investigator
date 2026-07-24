# Topic mapping profiles

Robot Incident Investigator keeps three concerns separate so the same incident
logic runs on different robots without baking any robot's topics into the core:

- **Incident spec** (`specs/*.json`) — the incident logic: which signals must be
  observed (front obstacle, stop event, velocity halt, …), how they corroborate,
  and the expected verdict. Robot-agnostic. References **abstract** event names,
  never a real error code. Format and compilation semantics (event-derived
  search windows, `if_available` sources, positive-observation requirements):
  see [`incident_spec.md`](incident_spec.md).
- **Topic mapping profile** (`profiles/*.example.json`, or a private
  `*.local.json`) — how *this* robot's topics play the neutral roles the spec
  needs. Robot-specific. May be a public generic example or a private fleet file.
- **Extractor** (`tools/extract_incident.py`) — reads a profile + a bag, and
  produces the neutral intermediate representation the spec compiler consumes
  (`timeline.json`, `logs.jsonl`). It knows nothing about whether the source was
  a LexxPluss AMR or a TurtleBot.

Public core code understands only the neutral roles below — never a company's
internal topic names.

## Public vs private profiles

- **Public example** (`profiles/generic_ros1.example.json`): generic ROS topics
  only (`/scan`, `/cmd_vel`, `/odom`, …). `privacy.commit_safe = true`. Committed.
- **Private profile** (`private_eval/<case>/topic_mapping.local.json`): may contain
  real topic names, namespaces, and event codes. **Never committed** — matched by
  `*.local.json` and `private_eval/` in `.gitignore`.

Real topic / namespace / node / diagnostic / error-code strings never enter the
public repo. A private deployment fills its `.local.json` locally; only sanitized
role labels are shared.

## Profile fields

- `profile_version`, `profile_id`, `robot_family` — identity.
- `privacy.contains_private_topics`, `privacy.commit_safe` — a self-declaration;
  a profile with private topics must not be committed.
- `time.source` — where timestamps come from (`bag_message_time` for now).
- `roles` — maps each neutral role to a real topic + msgtype + how to extract it.
- `events` — maps each **abstract** event to `{source_role, matcher, transition,
  output_code, emit}`. The `matcher` decides a match on the private source; the
  extractor emits only the abstract `output_code` (e.g. `EVENT_OBSTACLE_STOP`) —
  never the raw log text, diagnostic name, or real code. `transition`
  (`assert` | `clear`) is declared, not guessed from content.
- `_status` / `_caveats` (local profiles; ignored by the extractor) — mark a role
  or event mapping `"confirmed"` or `"provisional"` (missing means provisional)
  and record known semantic caveats as free text. The private smoke test reports
  **counts only**; status labels and caveat text never leave the machine.

### Roles

`front_scan`, `cmd_vel`, `actual_vel`, `odom`, `rosout`, `diag`, `tf`. A role is
optional: if a robot does not publish it, the dependent signal simply degrades
(see graceful degradation).

### Metric extract kinds (per role)

- `front_min_range` — front-sector minimum LaserScan range (geometric, no ML).
  Params: `front_sector_deg`, `front_axis_rad` (front direction, for robots whose
  scan zero is not forward), `range_min_m`, `range_max_m`, `output_metric`.
- `scalar_field` — pull one numeric field (e.g. `twist.twist.linear.x`) into an
  `output_metric`. The field path depends on `msgtype`.
- *(later)* `tf_jump` — discontinuity in a tf transform.

Resampling: metrics are floor-bucketed to `resample.rate_hz` (default 10 Hz); the
per-metric `aggregation` defaults to `min` for `front_min_range` and `last` for
`scalar_field`. `output_metric` names must match the neutral timeline schema the
compiler reads (`front_min_range_m`, `front_distance_m`, `planner_speed_mps`,
`applied_speed_mps`, `actual_speed_mps`, `safety_state`).

### Event matchers (per abstract event)

`matcher.kind` is one of, with `op` in {`exact`, `contains`} (v1; unbounded regex is
intentionally unsupported):

- `json_string_event` — parse a `std_msgs/String` JSON payload; match `field` `op` `value`.
- `rosout_text` — `rosgraph_msgs/Log`; optional `level_min`, and `value` `op` on the
  message text. The text itself is never emitted.
- `diagnostic_status` — `diagnostic_msgs/DiagnosticArray`; match a status `field`
  (name/message) `op` `value`, optional `level_min`. Names are never emitted.

`emit: edge` (or `deduplicate_window_s`) collapses repeated state republishes so a
DiagnosticArray / rosout stream does not bloat the log. The extractor validates that
`output_code` is unique, `source_role` exists, and `matcher.kind` is supported; a
source message matching several events is handled in profile order.

## Graceful degradation

- **Topic missing** → its metric is absent → the signal is absent / placeholder →
  the conclusion drops toward `low` / `insufficient_evidence`. Not a crash.
- **Corroborating source this robot simply doesn't have** → a spec-level
  `required: if_available` entry demotes it to a label-less corroboration member;
  the verdict caps at `medium` instead of failing (see
  [`incident_spec.md`](incident_spec.md)).
- **Event missing** → no stop/clear event emitted; recovery/verdict degrade, and a
  spec's event-derived search window falls back to its declared window. No crash.
- **Unsupported msgtype / matcher kind / missing field / missing topic** → a
  structured warning; that role or event is skipped. Warnings are counts
  (`{code, role?, count}`) only — never real topic / node / code / diagnostic names
  or log text.

## Privacy rules

- `*.local.json` and `private_eval/` are git-ignored; never commit them.
- Real topic / namespace / node / error-code / diagnostic names must not appear in
  the public repo, README, issues, or PRs.
- The private smoke-test manifest redacts topic names and non-standard msgtype
  package names (see `tools/private_eval_real_bag.py`).
- `tools/private_eval_smoke.py` is the repeatable private acceptance flow:
  extract → `list_incident_candidates` → verify each candidate → sanitized
  summary with a PASS/FAIL checklist. It requires `--out` under `private_eval/`
  (checked against `.gitignore`), asserts honest degradation (no `high` with
  demoted or force-placeholdered signals), and refuses to write a summary
  containing any profile string (topics, msgtypes, matcher values). It checks
  plumbing and privacy, never verdict ground truth — that stays with the
  synthetic oracle.

## Correctness boundary

A generic profile demonstrates the **role→topic contract**; it does not, by
itself, reproduce a real incident correctly (the public repo ships no real bag,
and the generic example intentionally leaves `front_distance_m` / real events
unmapped). Verdict correctness is validated only on the synthetic scenarios
(`tools/eval_extractor.py` — the canonical spec gate: profile matcher maps
source-specific events to `EVENT_*`, the extractor emits neutral logs, the spec
compiler consumes them, the verifier decides). The gate runs the oracle in both
declared- and derived-window modes and adds real-compatibility fixtures (missing
corroboration metric → `medium`, no positive observation → `low`, reversing
never halts → `low`). `tools/eval_scenarios.py` covers the legacy hand-authored
path. These remain the correctness oracle.

## Planned example profiles

`generic_ros1.example.json` ships now. `generic_ros2_mcap.example.json` and a
`turtlebot3` / Nav2 example are planned. Fleet-specific profiles stay local and
are never committed.
