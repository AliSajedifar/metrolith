// Java cognitive-complexity micro-corpus (G1-A).
//
// Every callable pins named rules from FROZEN_RULE_TABLE.md. Expected values in
// expectations.json are authored FROM THAT TABLE and from nothing else.
//
// JDK 21 `case X when g` lives in ConstructsGuarded.java, not here: the pinned
// reference JDK is 17 and cannot compile it. That is a toolchain limit, not a
// limit on whether the rule is expressible in Java.

class Constructs {

    int trivial(int a) {
        return a;
    }

    int plainIf(boolean a) {
        if (a) {
            return 1;
        }
        return 0;
    }

    int ifElseIfElse(int a) {
        if (a == 1) {
            return 1;
        } else if (a == 2) {
            return 2;
        } else {
            return 3;
        }
    }

    int nestedIf(boolean a, boolean b) {
        if (a) {
            if (b) {
                return 1;
            }
        }
        return 0;
    }

    int loops(int[] xs) {
        int total = 0;
        for (int x : xs) {
            while (x > 0) {
                x -= 1;
                total += 1;
            }
        }
        return total;
    }

    int switchWhole(int a) {
        switch (a) {
            case 1:
                return 1;
            case 2:
                return 2;
            default:
                return 3;
        }
    }

    int switchExpression(int a) {
        return switch (a) {
            case 1 -> 1;
            case 2 -> 2;
            default -> 3;
        };
    }

    int exceptionHandling(String a) {
        try {
            return Integer.parseInt(a);
        } catch (NumberFormatException e) {
            return 0;
        } catch (RuntimeException e) {
            return -1;
        } finally {
            if (a != null) {
                a.length();
            }
        }
    }

    int multiCatch(String a) {
        try {
            return Integer.parseInt(a);
        } catch (NumberFormatException | NullPointerException e) {
            return 0;
        }
    }

    int ternary(boolean a) {
        return a ? 1 : 0;
    }

    int boolSameOperator(boolean a, boolean b, boolean c) {
        if (a && b && c) {
            return 1;
        }
        return 0;
    }

    int boolMixedOperators(boolean a, boolean b, boolean c) {
        if (a && b || c) {
            return 1;
        }
        return 0;
    }

    int bitwiseNotCounted(int a, int b) {
        if ((a & b) != 0) {
            return 1;
        }
        return 0;
    }

    int labelledFlow(int[] xs) {
        outer:
        for (int x : xs) {
            for (int y : xs) {
                if (x > y) {
                    break outer;
                }
                continue outer;
            }
        }
        return 0;
    }

    int unlabelledJump(int[] xs) {
        for (int x : xs) {
            break;
        }
        return 0;
    }

    int recursive(int n) {
        if (n <= 0) {
            return 0;
        }
        return recursive(n - 1);
    }

    int synchronizedBlock(boolean a) {
        synchronized (this) {
            if (a) {
                return 1;
            }
        }
        return 0;
    }

    Runnable lambdaExcluded(boolean a) {
        return () -> {
            if (a) {
                System.out.print(1);
            }
        };
    }

    Runnable anonymousClassExcluded(boolean a) {
        return new Runnable() {
            @Override
            public void run() {
                if (a) {
                    System.out.print(1);
                }
            }
        };
    }
}
