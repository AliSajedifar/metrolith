// JavaScript cognitive-complexity micro-corpus (G1-A).
//
// Every callable pins named rules from FROZEN_RULE_TABLE.md. Expected values in
// expectations.json are authored FROM THAT TABLE and from nothing else.

export function trivial(a) {
  return a;
}

export function plainIf(a) {
  if (a) {
    return 1;
  }
  return 0;
}

export function ifElseIfElse(a) {
  if (a === 1) {
    return 1;
  } else if (a === 2) {
    return 2;
  } else {
    return 3;
  }
}

export function nestedIf(a, b) {
  if (a) {
    if (b) {
      return 1;
    }
  }
  return 0;
}

export function loops(xs) {
  let total = 0;
  for (const x of xs) {
    while (total < x) {
      total += 1;
    }
  }
  return total;
}

export function switchWhole(a) {
  switch (a) {
    case 1:
      return 1;
    case 2:
      return 2;
    default:
      return 3;
  }
}

export function exceptionHandling(a) {
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

export function ternary(a) {
  return a ? 1 : 0;
}

export function boolSameOperator(a, b, c) {
  if (a && b && c) {
    return 1;
  }
  return 0;
}

export function boolMixedOperators(a, b, c) {
  if (a && b || c) {
    return 1;
  }
  return 0;
}

export function nullishSequence(a, b) {
  return (a ?? b) ?? 1;
}

export function optionalChaining(a) {
  return a?.b?.c;
}

export function bitwiseNotCounted(a, b) {
  if ((a & b) !== 0) {
    return 1;
  }
  return 0;
}

export function labelledFlow(xs) {
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

export function unlabelledJump(xs) {
  for (const x of xs) {
    break;
  }
  return 0;
}

export function recursive(n) {
  if (n <= 0) {
    return 0;
  }
  return recursive(n - 1);
}

export function callbackExcluded(xs) {
  return xs.map((x) => {
    if (x > 0) {
      return x;
    }
    return 0;
  });
}

export class Widget {
  render(a) {
    if (a) {
      return 1;
    }
    return 0;
  }
}
