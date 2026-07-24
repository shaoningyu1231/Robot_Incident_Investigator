# Incident specs

Two specs ship today: `specs/obstacle_stop.json` (obstacle-triggered safety
stop) and `specs/localization_jump.json` (localization/TF-jump stop) — the same
compiler and the same unchanged verifier serve both, which is the point: adding
an incident type means writing a spec, not code.

An incident spec (`specs/*.json`) is the robot-agnostic half of an investigation:
it declares WHAT evidence a conclusion needs and how to observe it in the neutral
intermediate representation (`timeline.json` / `logs.jsonl`) — never how a
specific robot publishes it. Mapping a robot's real topics onto the neutral roles
is the topic mapping profile's job (see [`topic_mapping.md`](topic_mapping.md)).

The compiler (`tools/incident_spec.py`) evaluates a spec against one incident's
extracted assets and emits the annotations shape consumed by the deterministic
verifier (`tools/incident_rules.py`). The verifier is unchanged by design — a
spec changes where annotations come from, not how they are judged. Anchor times
are discovered by the compiler from the data; they are never written in a spec.

## Signals

A signal declares how to observe one piece of evidence.

- `metric_threshold_state` — a zone-style observation that ALWAYS emits one
  evidence: `positive_label` (e.g. `obstacle`) when the condition holds inside
  the search window, else `negative_label` (e.g. `clear`) as a real negative
  observation. The negative observation is what turns sensor disagreement into a
  label conflict instead of a missing-evidence `low`.
- `metric_threshold_crossing` — fires on the first in-window transition into the
  condition; when it never fires, the compiler emits an out-of-window
  placeholder so the verifier sees `required_present: false` and degrades to
  `low` instead of crashing.
- `log_event` — the first in-window occurrence of an abstract `EVENT_*` code;
  absent means the same out-of-window placeholder.
- `anchor_strategy` — `first`, `first_crossing` (require a real transition), or
  `first_true_after:<signal>` (only match at or after the dependency's anchor;
  dependency absent blocks the match).
- `"abs": true` — compare `|value|` against the threshold. A halt means
  `|v| <= 0.01`; a signed reverse velocity must not satisfy a `<=` halt check.

## Conclusions

Each conclusion declares its evidence contract.

- `search_window` — the declared window bracketing the hypothesis.
- `search_window_strategy` — `{kind: "around_log_event", code, pre_s, post_s}`
  derives the window as `[t - pre_s, t + post_s]` around the first occurrence of
  the event in the incident's own logs, and falls back to the declared window
  when the event is absent. This is what lets one spec follow a real incident's
  actual time instead of a fixture-specific window; callers may still override.
- `required_signals` — entries are either a plain signal name or
  `{"id", "required": "always" | "if_available"}`. `if_available` marks a source
  some robots simply do not have (e.g. a second independent distance sensor).
  When its metric is absent from every timeline row, the signal is dropped from
  required evidence but kept in its corroboration group as a label-less
  "source unavailable" evidence — the verifier's label-corroboration check then
  fails and the level is naturally capped at `medium`. A metric that exists but
  never fires stays a real negative observation and is never skipped.
- `positive_required` — groups of `{any_of, placeholder}`: at least one member
  must observe its positive label; otherwise the `placeholder` signal degrades
  to an out-of-window placeholder and the conclusion falls to `low`. A
  conclusion asserting an obstacle needs at least one positive observation —
  clear observations corroborate or conflict, they never satisfy it. Without
  this, a stop event plus a velocity halt with all sensors reading clear could
  verify as `high`.
- `corroboration_groups`, `temporal_checks`, `metric_checks` — the same contract
  the verifier already enforces. Temporal and metric checks are compiled only
  for signals that actually fired; placeholders never participate.

The compiled output records how it was produced in `compile_info` (resolved
window, window mode, demoted signals, forced placeholders) so a report can state
degradations explicitly instead of hiding them.

## Candidate discovery

A long recording can contain several occurrences of the anchor event; "the first
event wins" is not an investigation. `list_incident_candidates` (in
`tools/incident_spec.py`, exposed to the agent as a tool) enumerates one
candidate per occurrence of the conclusion's anchor event in the neutral logs —
`candidate_id`, `event_t`, `transition`, and the window derived from
`search_window_strategy`. It is discovery only: each candidate's window is then
verified individually (`compile_spec` with `window_override`), and the verdicts
must discriminate. The `two_stop_cycles` synthetic scenario locks this in: a
genuine obstacle stop verifies `high/ok` while a second stop/clear event pair
with no obstacle observation and no halt verifies `low/ok`.

Noisy real logs often republish the same fault; `coalesce_window_s` on the
search-window strategy folds repeated publishes within the window of the last
KEPT candidate into one (kept-relative — the same semantics as the extractor's
`deduplicate_window_s`; default 0 = off). Occurrences beyond the window start a
new candidate, so genuinely separate incidents still list separately.

## Honest degradation, by construction

The verifier's ladder (`high` / `medium` / `low`, verdict `ok` / `conflicting`)
is unchanged; the spec semantics make missing-modality data degrade instead of
inflate:

- one distance source, corroboration declared but unavailable — `medium`, not `high`
- stop event and halt present but no sensor observed an obstacle — `low`, not `high`
- robot moving in reverse the whole window (`|v|` never near zero) — halt not
  observed, `low`
- anchor event absent from the logs — window falls back, signals degrade to
  placeholders, `low` — never a crash

## Correctness gate

`tools/eval_extractor.py` is the canonical gate: generate the synthetic bags,
extract with the synthetic profile, compile the spec, run the unchanged
verifier. It runs the three-scenario oracle in BOTH window modes — declared and
derived — and adds real-compatibility fixtures on a mutated hero extract:
missing `front_distance_m` must yield `medium/ok`, no positive observation must
yield `low/ok`, and a constant reverse velocity must yield `low/ok`. Real bags
have no ground truth and are never the correctness oracle (see the privacy rules
in [`topic_mapping.md`](topic_mapping.md)).
