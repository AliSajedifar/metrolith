async function* operatorProbe(a, b, object, iterable) {
    const arithmetic = a + b - a * b / 2 % 3 ** 2;
    const shifts = (a << b) + (a >> b) + (a >>> b);
    const bits = (a & b) | (a ^ b);
    const logic = a < b && a <= b || a > b || a >= b;
    const equality = a == b || a != b || a === b || a !== b;
    const membership = "field" in object || object instanceof Object;
    const unary = +a + -b + ~a;
    const types = [!logic, typeof object, void a, delete object.removable];
    a += b; a -= b; a *= b; a /= b; a %= b; a **= b;
    a &= b; a |= b; a ^= b; a <<= b; a >>= b; a >>>= b;
    object.left &&= a; object.right ||= b; object.value ??= 0;
    a++; b--;
    const ternary = logic ? a : b;
    const nullish = object?.field?.method?.() ?? 0;
    const spread = [...iterable, a];
    const clone = { ...object, a };
    const arrow = (...rest) => rest.length;
    const awaited = await Promise.resolve(a);
    yield awaited;
    return arithmetic + shifts + bits + unary + ternary + nullish + arrow(...spread) + Number(types.length + membership + equality + clone.a);
}
