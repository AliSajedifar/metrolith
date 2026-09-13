// Java clarification corpus — Amendment 002 (owner-review closure).
//
// Additive: Constructs.java is untouched, so its line citations stay valid.
// Every method here pins a clarification introduced in revision 3 of
// FROZEN_RULE_TABLE.md. Expectations are authored from that table.

class Clarifications {

    int seqParenSame(boolean a, boolean b, boolean c) {
        if (a && (b && c)) {
            return 1;
        }
        return 0;
    }

    int seqParenMixed(boolean a, boolean b, boolean c) {
        if (a && (b || c)) {
            return 1;
        }
        return 0;
    }

    int seqThreeRuns(boolean a, boolean b, boolean c, boolean d) {
        if (a || b && c || d) {
            return 1;
        }
        return 0;
    }

    int recursionTwice(int n) {
        if (n <= 0) {
            return 0;
        }
        return recursionTwice(n - 1) + recursionTwice(n - 2);
    }

    int recursiveViaThis(int n) {
        if (n <= 0) {
            return 0;
        }
        return this.recursiveViaThis(n - 1);
    }

    int sameNameOtherReceiver(Other other, int n) {
        return other.recursiveViaThis(n);
    }

    int returnTraversed(boolean a, int b, int c) {
        return (a ? b : c);
    }

    int throwTraversed(boolean a, boolean b) {
        if (a && b) {
            throw new IllegalStateException("x");
        }
        return 0;
    }

    int discoveryLocalClassMethod(boolean flag) {
        class Inner {
            int measured(boolean a) {
                if (a) {
                    return 1;
                }
                return 0;
            }
        }
        Inner inner = new Inner();
        return inner.measured(flag);
    }

    static class Other {
        int recursiveViaThis(int n) {
            return n;
        }
    }
}
