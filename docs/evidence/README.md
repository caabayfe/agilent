# Evidence snapshots

Frozen copies of `evals/reports/` output. That folder is git-ignored, so these copies are what survive a fresh clone. Trace ids resolve only while the Postgres volume that produced them exists.

| Folder | What |
|---|---|
| `2026-09-27-model-matrix/` | `make eval-matrix` under the per-class G4 budget (fast 6 s, reasoning 12 s): 4 certified, 4 blocked. `matrix-interrupted-20260927.*` is an earlier attempt, aborted by a network outage (`APIConnectionError` on gpt-5.4) and the host sleeping, kept rather than hidden. |
| `2026-09-27-model-matrix-4s-budget/` | The same matrix under the original single 4 s G4 budget, where every reasoning model and gpt-4o was blocked on latency. |
