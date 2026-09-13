// TypeScript cognitive-complexity micro-corpus (G1-A).
//
// Every callable pins named rules from FROZEN_RULE_TABLE.md. Expected values in
// expectations.json are authored FROM THAT TABLE and from nothing else.
//
// The JSX short-circuit divergence lives in constructs.tsx: `.ts` and `.tsx` are
// different parsers to ArchLens, and JSX is only legal in the latter.

type Maybe<T> = T | null;

export function trivial(a: number): number {
  return a;
}

export function plainIf(a: boolean): number {
  if (a) {
    return 1;
  }
  return 0;
}

export function ifElseIfElse(a: number): number {
  if (a === 1) {
    return 1;
  } else if (a === 2) {
    return 2;
  } else {
    return 3;
  }
}

export function nestedIf(a: boolean, b: boolean): number {
  if (a) {
    if (b) {
      return 1;
    }
  }
  return 0;
}

export function loops(xs: number[]): number {
  let total = 0;
  for (const x of xs) {
    while (total < x) {
      total += 1;
    }
  }
  return total;
}

export function switchWhole(a: number): number {
  switch (a) {
    case 1:
      return 1;
    case 2:
      return 2;
    default:
      return 3;
  }
}

export function exceptionHandling(a: string): number {
  try {
    return Number(a);
  } catch (error) {
    return 0;
  } finally {
    if (a) {
      String(a);
    }
  }
}

export function ternary(a: boolean): number {
  return a ? 1 : 0;
}

export function boolSameOperator(a: boolean, b: boolean, c: boolean): number {
  if (a && b && c) {
    return 1;
  }
  return 0;
}

export function boolMixedOperators(a: boolean, b: boolean, c: boolean): number {
  if (a && b || c) {
    return 1;
  }
  return 0;
}

export function nullishSequence(a: Maybe<number>, b: Maybe<number>): number {
  return (a ?? b) ?? 1;
}

export function optionalChaining(a: { b?: { c?: number } }): number | undefined {
  return a?.b?.c;
}

export function bitwiseNotCounted(a: number, b: number): number {
  if ((a & b) !== 0) {
    return 1;
  }
  return 0;
}

export function labelledFlow(xs: number[]): number {
  outer: for (const x of xs) {
    for (const y of xs) {
      if (x > y) {
        break outer;
      }
      continue outer;
    }
  }
  return 0;
}

export function unlabelledJump(xs: number[]): number {
  for (const x of xs) {
    break;
  }
  return 0;
}

export function recursive(n: number): number {
  if (n <= 0) {
    return 0;
  }
  return recursive(n - 1);
}

export function callbackExcluded(xs: number[]): number[] {
  return xs.map((x) => {
    if (x > 0) {
      return x;
    }
    return 0;
  });
}

export function typeOnlyConstructs(a: unknown): number {
  const widened = a as Maybe<number>;
  const forced = widened!;
  return forced;
}

export function overloaded(a: number): number;
export function overloaded(a: string): number;
export function overloaded(a: number | string): number {
  return typeof a === "number" ? a : a.length;
}

export class Widget {
  render(a: boolean): number {
    if (a) {
      return 1;
    }
    return 0;
  }
}
