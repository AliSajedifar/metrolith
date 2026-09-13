// Zero-suppression probe, TypeScript / eslint-plugin-sonarjs S3776.
//
// SonarJS covers JavaScript and TypeScript with one rule but two parses, so the
// suppression claim is measured separately per language rather than inferred
// from the JavaScript result.

// No rule-bearing construct. Frozen table: 0.
export function zeroCallable(a: number): number {
  const b: number = a + 1;
  return b;
}

// Exactly one S-IF at nesting 0. Frozen table: 1.
export function nonZeroCallable(a: number): number {
  if (a > 0) {
    return a;
  }
  return 0;
}
