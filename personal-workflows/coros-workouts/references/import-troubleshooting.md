# Import and synchronization troubleshooting

## Locate where the information changes

Compare these stages: decoded FIT → Intervals.icu workout text and graph → COROS workout step → watch. Identify the imported version by its internal workout name. A screenshot without the title does not establish which version is open.

- If FIT distances/durations are wrong, fix their encoding and validate again.
- If the FIT is correct but the platform estimates an implausible distance or duration, inspect the imported steps and athlete settings. Do not invent a file defect from a displayed total.
- If Intervals.icu contains pace text but its graph is blank with a watts axis, inspect running pace configuration before changing the FIT again.
- If Intervals.icu shows correct actual pace targets but COROS does not, investigate synchronization and target-type mapping. Renaming steps is not a repair for missing machine-readable targets.

## Pace configuration and import conversion

In Intervals.icu running settings, inspect **Parametres d'allure → Allure au seuil**. A `?` means it is missing. Enter the user's current threshold pace when authorized; obtain it from a current source rather than preserving an old example as a default. Also inspect pace units and workout target priority. If power is selected ahead of pace and the graph remains power-based, inspect the available controls and select the intended pace target within the user's authorized scope.

FIT import can convert absolute speed targets into percentages of threshold pace. Percentages alone do not prove a defect: compare the resulting actual pace with the prescription. After changing a threshold, existing percentage-based steps may represent a different pace. Replace these with absolute pace or re-import and verify.

Supported absolute-pace workout text example:

```
- 0.4km 4:05-3:55/km Pace
```

The distance and `Pace` suffix are significant. Put a pace prescription in the target syntax rather than only in a leading description. Inspect the rendered graph and step target after saving. For open steps, inspect whether import assigned a default zone, particularly Z1 to progressive strides, and restore the requested free effort.

A reported January 2026 case had missing exported pace targets and a blank watts graph; entering threshold pace resolved it. This is a diagnostic lead, not proof that every missing target has that cause. In this user's September 2026 session, a screenshot independently confirmed the threshold field was empty. A later user screenshot confirmed pace targets visible in the COROS app (400 m steps displayed 3:54–4:06/km), along with truncated titles. This verifies app display for the shown steps, not watch alerts or exact preservation of the original 3:55–4:05/km range.

## COROS

On editable native COROS workouts, individual running steps can have Distance as their target and Pace as their intensity. Save and send the workout to the watch after changes. Imported workouts may have editing restrictions; inspect the actual UI before promising a direct edit.

Mixed HR/pace transfer issues have been reported separately. This does not establish a mixed-target bug for a workout containing only pace and open steps. Ask for or inspect the actual Intervals.icu step targets before attributing the cause.

## Reference session: activation before a 5 km race

Use only when the user requests this session or as a validation example:

- 15 minutes easy warm-up.
- Four 400 m efforts at 3:55–4:05/km, each followed by 90 seconds easy jog.
- Four progressive 100 m strides, each followed by 90 seconds easy recovery.
- 10 minutes easy cool-down.

Expanded representation: 18 steps, 2,000 m of distance-based efforts, and 2,220 seconds (37 minutes) of timed steps. The 400 m efforts add roughly 6:16–6:32; total duration also includes the strides. Approximately 45 minutes is an estimate. Neither 20 nor 21 km is a prescribed total distance. Keep counters at the start of effort and recovery names and notes.

## Sources

Recheck current behavior when troubleshooting; these were consulted on 2026-09-11.

- [FIT import strips pace targets — threshold pace resolution](https://forum.intervals.icu/t/fit-import-strips-pace-targets/120680)
- [Intervals.icu absolute pace syntax](https://forum.intervals.icu/t/specify-workouts-using-absolute-pace/115846)
- [Intervals.icu workout library import](https://forum.intervals.icu/t/importing-fit-files-error/73087)
- [COROS custom workouts: step targets, intensity, save and sync](https://support.coros.com/hc/en-us/articles/47285577958932-Create-Custom-Workouts-in-Your-COROS-App)
- [Mixed pace and HR transfer report](https://forum.intervals.icu/t/coros-integration-mixed-pace-hr-intervals/127171)
- [Garmin FIT workout format](https://developer.garmin.com/fit/file-types/workout/)
