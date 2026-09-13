// JavaScript clarification corpus — Amendment 002 (owner-review closure).
//
// Additive: constructs.js is untouched, so its line citations stay valid. Every
// callable here pins a clarification introduced in revision 3 of
// FROZEN_RULE_TABLE.md. Expectations are authored from that table.

export function seqParenSame(a, b, c) {
  if (a && (b && c)) {
    return 1;
  }
  return 0;
}

export function seqParenMixed(a, b, c) {
  if (a && (b || c)) {
    return 1;
  }
  return 0;
}

export function seqThreeRuns(a, b, c, d) {
  if (a || b && c || d) {
    return 1;
  }
  return 0;
}

export function seqNullishRun(a, b, c) {
  return a ?? b ?? c;
}

export function seqNullishThenOr(a, b, c) {
  return (a ?? b) || c;
}

export function logicalAssignAnd(x, a, b) {
  x &&= (a || b);
  return x;
}

export function logicalAssignOr(x, a) {
  x ||= a;
  return x;
}

export function logicalAssignNullish(x, a) {
  x ??= a;
  return x;
}

export function recursionTwice(n) {
  if (n <= 0) {
    return 0;
  }
  return recursionTwice(n - 1) + recursionTwice(n - 2);
}

export function returnTraversed(a, b, c) {
  return (a ? b : c);
}

export class Holder {
  recursiveMethod(n) {
    if (n <= 0) {
      return 0;
    }
    return this.recursiveMethod(n - 1);
  }

  sameNameOtherReceiver(other, n) {
    return other.recursiveMethod(n);
  }
}

export function discoveryFunctionClassMethod(flag) {
  class Inner {
    measured(a) {
      if (a) {
        return 1;
      }
      return 0;
    }
  }
  return flag ? Inner : null;
}
