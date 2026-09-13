# Conformance oracle amendments

## B2-ORACLE-001: git-native-symlink-excluded

Authority: the owner's instruction, “Metrolith B2 — Authorized Single-Case
Oracle Amendment and Closeout,” explicitly authorizes this amendment and its
dependent integrity pins, conditional on independent fixture/contract confirmation.

That condition was confirmed against the declared fixture and the runner's actual
native filesystem materialization, using lstat and readlink before inventory or
metric computation. app.py is a regular file containing `x = 1`; target.py is a
regular file containing `y = 2`; link.py is a symlink to target.py. Neither regular
file matches a declared exclusion. Metric Contract 3.0.0 counts each included
production source path once, so Source Files is 2. The symlink remains excluded.

Only the existing `observations["aggregate/source_files"]` expectation changes
from 1 to 2, and this case's manifest notes explain both counted regular files.
The excluded-link observation, fixture bytes, capability requirements, historical
review fields, all other expectations and production semantics are unchanged.
The original failed observation and unapplied proposal remain historical evidence;
this amendment does not claim an earlier review or rewrite their outcomes.

| File | Original SHA-256 | Amended SHA-256 |
|---|---|---|
| data/expected/git-native-symlink-excluded.json | 384c41f0c51d9188e8489c1d1b099711947b8fa26d54e0a9cb5aa20d3398ffb7 | f82300c312e26a133ccb5a3c6868509ddca7fa5eeb70461f4f352abbbb45fa20 |
| data/manifest.json | 55670a52303d13eb2c270dfc348c05fdf568d51465fb9df67a8d4a9c08e6846c | d8f488e36765e32c16ecf49c2c1f1cc0d627f2b12e1c2d5ff92fee9c925cdf32 |

The exact-byte preservation guard in tests/test_metrolith_brand_migration.py
retains its original historical baseline, verifies these original identities
against that baseline, and pins the amended bytes and this note independently.
Every other protected file remains subject to its existing preservation check.
