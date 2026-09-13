// Synthetic Complexity Contract 1.0.0 micro-corpus for JavaScript.
//
// Every callable isolates one construct family. Expected per-construct
// contributions are in expectations.json, authored from
// docs/COMPLEXITY_CONTRACT_V1.md.
//
// This file is parsed, never bundled or executed.

function trivial() {
  return 1;
}

function sequentialBranches(a, b) {
  if (a > 0) {
    a++;
  }
  if (b > 0) {
    b++;
  }
  return a + b;
}

function elseIfChain(v) {
  if (v < 0) {
    return -1;
  } else if (v === 0) {
    return 0;
  } else {
    return 1;
  }
}

function nestedBranches(a, b) {
  if (a > 0) {
    if (b > 0) {
      return a * b;
    }
  }
  return 0;
}

function loops(xs, a) {
  for (const x of xs) {
    a++;
  }
  for (const k in xs) {
    a++;
  }
  for (let i = 0; i < 3; i++) {
    a++;
  }
  while (a > 0) {
    a--;
  }
  do {
    a++;
  } while (a < 3);
  return a;
}

function switching(a) {
  switch (a) {
    case 1:
      return 1;
    case 2:
      return 2;
    default:
      return 0;
  }
}

function exceptionHandling(a) {
  try {
    a++;
  } catch (e) {
    a--;
  } finally {
    a = 0;
  }
  return a;
}

function ternary(a) {
  return a > 0 ? 1 : 2;
}

function booleanOperators(a, b, c) {
  if (a && b) {
    return true;
  }
  if (a || b || c) {
    return true;
  }
  return a && b || c;
}

function nullishAndLogicalAssignment(a, b) {
  const n = a ?? b;
  let z = a;
  z ??= 1;
  z ||= 2;
  z &&= 3;
  return n + z;
}

function optionalChaining(a) {
  return a?.b?.c;
}

function excludedCallbacks(xs) {
  xs.map((v) => v && v.z);
  xs.forEach(function (v) {
    if (v) {
      return v;
    }
    return null;
  });
  if (xs) {
    return 1;
  }
  return 0;
}

function measuredInnerClass(flag) {
  class Inner {
    innerMethod(value) {
      if (value > 0) {
        return value;
      }
      return -value;
    }
  }
  return flag ? Inner : null;
}

function destructuringParameters({ a, b }, [c], ...rest) {
  return a;
}

function commentsAndBlanks(a) {
  // a comment does not count

  const value = a; /* block comment */

  return value;
}

const boundArrow = (x) => x && x.y;

const boundExpression = function (x) {
  if (x) {
    return 1;
  }
  return 0;
};

class Widget {
  constructor() {
    this.size = 1;
  }

  render(x) {
    if (x) {
      return 1;
    }
    return 0;
  }

  get size() {
    return this._size;
  }

  static make(a, b) {
    return a + b;
  }

  #secret(a) {
    return a;
  }

  handler = () => {
    if (this.size) {
      return 1;
    }
    return 0;
  };
}

const helpers = {
  first(a) {
    return a;
  },
  get second() {
    return 2;
  },
};

module.exports.exported = function (z) {
  return z;
};
