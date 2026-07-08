# Topic mapping profiles

Robot Incident Investigator keeps three concerns separate so the same incident
logic runs on different robots without baking any robot's topics into the core:

- **Incident spec** (`specs/*.json`) — the incident logic: which signals must be
  observed (front obstacle, stop event, velocity halt, …), how they corroborate,
  and the expected verdict. Robot-agnostic. References **abstract** event names,
  never a real error code.
- **Topic mapping profile** (`profiles/*.example.json`, or a private
  `*.local.json`) — how *this* robot's topics play the neutral roles the spec
  needs. Robot-specific. May be a public generic example or a private fleet file.
- **Extractor** (planned) — reads a profile + a bag, and produces the neutral
  intermediate representation the spec compiler already consumes
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
- `events` — maps each **abstract** event to a source role + a local match rule,
  emitting an abstract `output_code` (e.g. `EVENT_OBSTACLE_STOP`). Real codes live
  only in the private match rules.

### Roles

`front_scan`, `cmd_vel`, `actual_vel`, `odom`, `rosout`, `diag`, `tf`. A role is
optional: if a robot does not publish it, the dependent signal simply degrades
(see graceful degradation).

### Extract kinds

- `front_min_range` — front-sector minimum LaserScan/points range (geometric, no
  ML). Params: `front_sector_deg`, `front_axis_rad` (front direction, for robots
  whose scan zero is not forward), `range_min_m`, `range_max_m`, `output_metric`.
- `scalar_field` — pull one numeric field (e.g. `twist.twist.linear.x`) into an
  `output_metric`. The field path depends on `msgtype`.
- `log_event` — match a log/rosout entry for an abstract event (private match).
- `diagnostic_event` — match a `DiagnosticArray` status for an abstract event.
- *(later)* `tf_jump` — detect a discontinuity in a tf transform.

`output_metric` names must match the neutral timeline schema the compiler reads
(`front_min_range_m`, `front_distance_m`, `planner_speed_mps`, `applied_speed_mps`,
`actual_speed_mps`, `safety_state`).

## Graceful degradation

- **Topic missing** → its metric is absent → the signal is absent / placeholder →
  the conclusion drops toward `low` / `insufficient_evidence`. Not a crash.
- **Event missing** → no stop/clear event emitted; recovery/verdict degrade, no crash.
- **Unsupported msgtype** → explicit warning; that role is skipped.

## Privacy rules

- `*.local.json` and `private_eval/` are git-ignored; never commit them.
- Real topic / namespace / node / error-code / diagnostic names must not appear in
  the public repo, README, issues, or PRs.
- The private smoke-test manifest redacts topic names and non-standard msgtype
  package names (see `tools/private_eval_real_bag.py`).

## Correctness boundary

A generic profile demonstrates the **role→topic contract**; it does not, by
itself, reproduce a real incident correctly (the public repo ships no real bag,
and the generic example intentionally leaves `front_distance_m` / real events
unmapped). Verdict correctness is validated only on the synthetic scenarios
(`tools/eval_spec.py`), which remain the correctness oracle.

## Planned example profiles

`generic_ros1.example.json` ships now. `generic_ros2_mcap.example.json` and a
`turtlebot3` / Nav2 example are planned. Fleet-specific profiles stay local and
are never committed.
