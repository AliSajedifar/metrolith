package d0;

@Deprecated
final class OperatorProbe {
    static final int CONSTANT = 1;
    int helper() { return 1; }
    int probe(int a, int b, Object value, int... rest) {
        int arithmetic = a + b - a * b / 2 % 3;
        int shifts = (a << b) + (a >> b) + (a >>> b);
        int bits = (a & b) | (a ^ b);
        boolean logic = a < b && a <= b || a > b || a >= b;
        boolean equality = a == b || a != b;
        boolean typed = value instanceof String;
        int unary = +a + -b + ~a;
        boolean negated = !logic;
        a += b; a -= b; a *= b; a /= b; a %= b;
        a &= b; a |= b; a ^= b; a <<= b; a >>= b; a >>>= b;
        a++; b--;
        int ternary = logic ? a : b;
        Runnable lambda = () -> helper();
        java.util.function.IntSupplier reference = this::helper;
        Object created = new Object();
        return arithmetic + shifts + bits + unary + ternary + rest.length + (negated ? 1 : 0);
    }
}
