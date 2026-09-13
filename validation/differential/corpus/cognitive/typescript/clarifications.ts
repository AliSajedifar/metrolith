// TypeScript clarification corpus — Amendment 002 (owner-review closure).
//
// Additive: constructs.ts is untouched, so its line citations stay valid. Every
// callable here pins a clarification introduced in revision 3 of
// FROZEN_RULE_TABLE.md. Expectations are authored from that table.

export function seqParenSame(a: boolean, b: boolean, c: boolean): number {
  if (a && (b && c)) {
    return 1;
  }
  return 0;
}

export function seqParenMixed(a: boolean, b: boolean, c: boolean): number {
  if (a && (b || c)) {
    return 1;
  }
  return 0;
}

export function seqThreeRuns(a: boolean, b: boolean, c: boolean, d: boolean): number {
  if (a || b && c || d) {
    return 1;
  }
  return 0;
}

export function seqNullishRun(a: number | null, b: number | null, c: number): number {
  return a ?? b ?? c;
}

export function seqNullishThenOr(a: number | null, b: number | null, c: number): number {
  return (a ?? b) || c;
}

export function logicalAssignAnd(x: boolean, a: boolean, b: boolean): boolean {
  x &&= (a || b);
  return x;
}

export function logicalAssignOr(x: boolean, a: boolean): boolean {
  x ||= a;
  return x;
}

export function logicalAssignNullish(x: number | null, a: number): number | null {
  x ??= a;
  return x;
}

export function recursionTwice(n: number): number {
  if (n <= 0) {
    return 0;
  }
  return recursionTwice(n - 1) + recursionTwice(n - 2);
}

export function returnTraversed(a: boolean, b: number, c: number): number {
  return (a ? b : c);
}

export class Holder {
  recursiveMethod(n: number): number {
    if (n <= 0) {
      return 0;
    }
    return this.recursiveMethod(n - 1);
  }

  sameNameOtherReceiver(other: Holder, n: number): number {
    return other.recursiveMethod(n);
  }
}

export function discoveryFunctionClassMethod(flag: boolean): unknown {
  class Inner {
    measured(a: boolean): number {
      if (a) {
        return 1;
      }
      return 0;
    }
  }
  return flag ? Inner : null;
}
