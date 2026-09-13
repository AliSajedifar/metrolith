// Zero-suppression probe, JavaScript / eslint-plugin-sonarjs S3776.
//
// Same design as the Go and Java probes: a genuine zero and a control scoring 1
// in one file, so an absent zero is decidably suppression rather than a file the
// linter never reached.

// No rule-bearing construct. Frozen table: 0.
export function zeroCallable(a) {
  const b = a + 1;
  return b;
}

// Exactly one S-IF at nesting 0. Frozen table: 1.
export function nonZeroCallable(a) {
  if (a > 0) {
    return a;
  }
  return 0;
}
