---
name: coros-workouts
description: Create or revise structured running workout FIT files for COROS via Intervals.icu, with numbered step names and pace targets; troubleshoot import, estimated distance, or missing watch guidance.
---

# COROS structured workouts

## Preserve the workout and make every step readable

Build the session requested by the user. Preserve durations, distances, repetition counts, recovery placement, and pace ranges when making naming or format changes. Treat example sessions as examples, not training defaults. Use current user-provided fitness values; previously reported thresholds are not permanent settings.

For every repeated effort, put its position within its set at the very beginning of BOTH the FIT step name and notes. Intervals.icu may use notes for the displayed description, and devices can truncate labels.

- `1/5 Accel`, through `5/5 Accel`.
- `1/4 400m` for four prescribed intervals.
- `1/4 Recup` after the first effort in a four-repeat set.

Use compact labels in BOTH step names and notes, since the integration may derive the title from notes. Limit every step title and its matching short notes to at most 12 characters, including spaces, punctuation, and repetition counters. This is the user's strict naming requirement, not a documented COROS limit. Shorten the action label as needed while preserving the complete counter. Keep the counter and action first; remove decorative separators and duplicate pace/duration text. Use `Echauff.`, `Calme`, `Accel`, and `Recup` when needed. Display distance, duration, and pace through their structured fields. Keep longer coaching instructions in the overall workout description or accompanying session summary, not in step notes used as titles.

Use the actual denominator for each set, not the workout's total step count. Restart the counter for a separate set. Number recoveries consistently with the effort they follow. Prefer the user's language. Inspect device/app rendering when available; character count alone cannot guarantee display width.

Encode requested pace as machine-readable targets and include readable pace in the overall session summary. A title containing pace is only a label; it does not create watch guidance or alerts. Keep easy/open steps and progressive strides free of numerical targets unless the user requested those targets. A progressive stride is not automatically Z1 and is not an all-out sprint. Ask for missing pace preferences only when needed to implement the request.

## Create and validate the FIT

1. Inspect an existing file before attributing an import problem to its encoding. If a referenced `/mnt/data` file is absent locally, inspect conversation attachments and likely Downloads/workspace copies; state which copy was inspected. Decode the bytes rather than relying on the filename or previous assistant claims.
2. Generate a structured workout: `file_id.type=workout`, `workout.sport=running`, `num_valid_steps` matching the actual workout-step message count, and sequential zero-based step indices. Do not include fabricated activity records, laps, GPS tracks, or completed-session totals.
3. Explicitly expanded repetitions are appropriate when individual counters must survive import. Preserve whether recovery follows every effort, including the last one; do not silently change this while expanding a set.
4. Use documented setters from a FIT encoder, and inspect its units. Raw FIT time duration uses milliseconds; distance duration uses centimetres; custom speed targets use m/s with a scale of 1000. Libraries may accept seconds, metres, and m/s and apply these scales themselves. Avoid double scaling.
5. For custom speed targets, use `target_type=speed`, custom zone value `0`, and ordered speed bounds. For pace bounds in seconds/km: low speed = 1000 / slower pace; high speed = 1000 / faster pace. Example: 3:55–4:05/km corresponds to approximately 4.082–4.255 m/s. Use open targets for unprescribed blocks.
6. Validate CRC/header integrity and decode the finished artifact using Garmin's FIT SDK plus an independent decoder such as fitdecode. Use a fresh decoder/stream after an integrity check if it consumes the stream. Check every step's duration type, decoded value, intensity, target, order, count, and counter prefix in names and notes. Assert that every decoded step name and short notes is at most 12 characters, counting spaces and counters. Assert that numerical pace targets round-trip within FIT quantization tolerance.
7. Calculate fixed time and fixed distance separately. A session mixing time and distance does not specify one exact total distance or duration. Give estimates with assumptions; distinguish platform estimates from encoded values.
8. Deliver a clearly versioned FIT with a matching internal workout name so the user can identify the imported version. Link the artifact and report actual verification: file validity, platform import, and watch display are three separate checks.

## Intervals.icu and COROS verification

For import, use Intervals.icu's workout library import and schedule the workout for synchronization to COROS. Consult current official instructions when navigating live interfaces. With screenshots, locate the control using visible labels and nearby landmarks rather than guessed coordinates. COROS activity-history FIT import is distinct from planned-workout synchronization.

When targets or distances look wrong, read [references/import-troubleshooting.md](references/import-troubleshooting.md). Inspect the imported workout before generating another version. Binary validity alone does not establish successful import or watch guidance.

Complete a file-only request once the requested artifact passes semantic validation, stating any untested platform behavior. For an end-to-end request, verify the imported workout and COROS step targets if access is available. Otherwise identify the specific missing observation or access; keep the integration outcome explicitly unverified.
