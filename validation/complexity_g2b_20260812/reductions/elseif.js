// Rule table 2.2: F-ELSEIF raises nesting for its OWN body.
// if a -> S-IF@0 = 1; else if b -> F-ELSEIF = 1; inner if c -> S-IF@1 = 2. Total 4.
export function elseIfBodyNesting(a, b, c) {
  if (a) {
    return 0;
  } else if (b) {
    if (c) { return 1; }
  }
  return 0;
}
