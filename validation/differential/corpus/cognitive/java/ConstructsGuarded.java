// Java cognitive-complexity micro-corpus, JDK 21 fragment (G1-A).
//
// `case X when g` finalized in JDK 21. The pinned reference JDK is 17.0.17.10
// and CANNOT compile this file, which is why it is separate: the JDK 17 corpus
// must stay compilable, and a rule that only a newer toolchain can express must
// not be allowed to break it.
//
// This is a toolchain limit, not an expressibility limit. F-GUARD is written
// here exactly as the frozen rule table specifies it, and whether it can be
// *validated* against PMD 7.7.0 on JDK 17 is an open G1-B question rather than
// a G0 reopening. The same limit was already recorded for cyclomatic complexity
// in C4: javac 17 rejected a guarded case and the adapter reported the callable
// not evaluable rather than inventing a number.

class ConstructsGuarded {

    int guardedSwitch(Object v) {
        return switch (v) {
            case Integer i when i > 0 -> 1;
            case Integer i -> 2;
            default -> 0;
        };
    }
}
