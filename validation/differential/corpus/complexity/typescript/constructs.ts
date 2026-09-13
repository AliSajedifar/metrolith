// Synthetic Complexity Contract 1.0.0 micro-corpus for TypeScript.
//
// Covers the families TypeScript adds over JavaScript: the `this` parameter,
// overload signatures, optional and defaulted parameters, and decorators.
// Expected per-construct contributions are in expectations.json.
//
// This file is parsed, never compiled or executed.

export function trivial(): number {
  return 1;
}

export function sequentialBranches(a: number, b: number): number {
  if (a > 0) {
    a++;
  }
  if (b > 0) {
    b++;
  }
  return a + b;
}

export function elseIfChain(v: number): number {
  if (v < 0) {
    return -1;
  } else if (v === 0) {
    return 0;
  } else {
    return 1;
  }
}

export function loops(xs: number[], a: number): number {
  for (const x of xs) {
    a++;
  }
  while (a > 0) {
    a--;
  }
  return a;
}

export function switching(a: number): number {
  switch (a) {
    case 1:
      return 1;
    case 2:
      return 2;
    default:
      return 0;
  }
}

export function booleanOperators(a: boolean, b: boolean, c: boolean): boolean {
  if (a && b) {
    return true;
  }
  if (a || b || c) {
    return true;
  }
  return a && b || c;
}

export function nullishCoalescing(a?: number, b: number = 1): number {
  return a ?? b;
}

export function optionalAndDefaultParameters(a: number, b?: string, c = 3): number {
  return a;
}

export function excludedArrow(xs: number[]): number {
  const predicate = (v: number) => v > 0 && v < 9;
  if (xs.length) {
    return 1;
  }
  return 0;
}

export function commentsAndBlanks(a: number): number {
  // a comment does not count

  const value = a; /* block comment */

  return value;
}

class Widget {
  withThis(this: Widget, a: number, { b, c }: any, ...rest: number[]): number {
    if (a > 0 && a < 9) {
      return a;
    }
    return 0;
  }

  plain(a: number, b: string): number {
    return a;
  }

  @HostListener("click")
  @Throttle(100)
  decorated(event: Event): number {
    return 1;
  }
}

declare function declaredOnly(a: number): number;

function overloadedImplementation(a: number): number;
function overloadedImplementation(a: string): number;
function overloadedImplementation(a: any): number {
  if (typeof a === "number") {
    return a;
  }
  return a.length;
}

function HostListener(name: string): MethodDecorator {
  return () => undefined;
}

function Throttle(ms: number): MethodDecorator {
  return () => undefined;
}
