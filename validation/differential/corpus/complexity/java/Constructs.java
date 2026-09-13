// Synthetic Complexity Contract 1.0.0 micro-corpus for Java.
//
// Every method isolates one construct family. Expected per-construct
// contributions are in expectations.json, authored from
// docs/COMPLEXITY_CONTRACT_V1.md BEFORE any reference adapter existed.
//
// This file is parsed, never compiled or executed.
package constructs;

import java.util.List;

class Constructs {

    int trivial() {
        return 1;
    }

    int sequentialBranches(int a, int b) {
        if (a > 0) {
            a++;
        }
        if (b > 0) {
            b++;
        }
        return a + b;
    }

    int elseIfChain(int v) {
        if (v < 0) {
            return -1;
        } else if (v == 0) {
            return 0;
        } else {
            return 1;
        }
    }

    int nestedBranches(int a, int b) {
        if (a > 0) {
            if (b > 0) {
                return a * b;
            }
        }
        return 0;
    }

    int loops(List<String> xs, int a) {
        for (String s : xs) {
            a++;
        }
        for (int i = 0; i < 3; i++) {
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

    int colonSwitch(int c) {
        switch (c) {
            case 1: case 2: return 1;
            default: return 0;
        }
    }

    int arrowSwitch(int c) {
        return switch (c) { case 1 -> 1; case 2, 3 -> 2; default -> 0; };
    }

    int guardedSwitch(Object o) {
        return switch (o) { case Integer i when i > 0 -> 1; default -> 0; };
    }

    int exceptionHandling(int a) {
        try {
            a++;
        } catch (IllegalStateException e) {
            a--;
        } catch (RuntimeException e) {
            a = 0;
        } finally {
            a += 3;
        }
        return a;
    }

    int ternary(int a) {
        return a > 0 ? 1 : 2;
    }

    boolean booleanOperators(boolean a, boolean b, boolean c) {
        if (a && b) {
            return true;
        }
        if (a || b || c) {
            return true;
        }
        return a && b || c;
    }

    int excludedLambda(List<Integer> xs) {
        xs.forEach(v -> {
            if (v > 0 && v < 9) {
                count(v);
            }
        });
        return 1;
    }

    int excludedAnonymousClass() {
        Runnable r = new Runnable() {
            public void run() {
                if (true) {
                    count(1);
                }
            }
        };
        return 1;
    }

    int bareBlock(int a) {
        {
            a++;
        }
        return a;
    }

    int synchronizedBlock(int a) {
        synchronized (this) {
            a++;
        }
        return a;
    }

    int receiverAndVarargs(Constructs Constructs.this, int a, int... rest) {
        return a;
    }

    int overloaded(int a) {
        return a;
    }

    int overloaded(String a) {
        return a.length();
    }

    int measuredLocalClass(int flag) {
        class Local {
            int innerMethod(int value) {
                if (value > 0) {
                    return value;
                }
                return -value;
            }
        }
        return flag;
    }

    int commentsAndBlanks(int a) {
        // a comment does not count

        int value = a; /* block comment */

        return value;
    }

    void count(int v) {
    }

    Constructs() {
    }

    static {
    }

    interface Api {
        default int defaultMethod(int x) {
            return x;
        }

        int abstractMethod(int y);
    }

    enum Kind {
        A;

        int enumMethod() {
            return 1;
        }
    }
}
