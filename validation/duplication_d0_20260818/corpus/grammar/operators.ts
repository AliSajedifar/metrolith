type RequiredMap<T> = { readonly [K in keyof T]-?: T[K] };
type Predicate = (value: unknown) => value is string;

async function* operatorProbe<T extends object>(a: number, b: number, object: T, iterable: number[]) {
    const arithmetic = a + b - a * b / 2 % 3 ** 2;
    const shifts = (a << b) + (a >> b) + (a >>> b);
    const bits = (a & b) | (a ^ b);
    const logic = a < b && a <= b || a > b || a >= b;
    const equality = a == b || a != b || a === b || a !== b;
    const membership = "field" in object || object instanceof Object;
    const unary = +a + -b + ~a;
    const types = [!logic, typeof object, void a, delete (object as any).removable];
    a += b; a -= b; a *= b; a /= b; a %= b; a **= b;
    a &= b; a |= b; a ^= b; a <<= b; a >>= b; a >>>= b;
    (object as any).left &&= a; (object as any).right ||= b; (object as any).value ??= 0;
    a++; b--;
    const ternary = logic ? a : b;
    const nullish = (object as any)?.field?.method?.() ?? 0;
    const spread = [...iterable, a];
    const clone = { ...(object as any), a };
    const arrow = (...rest: number[]) => rest.length;
    const asserted = object satisfies object;
    const nonNull = object!;
    const awaited = await Promise.resolve(a);
    yield awaited;
    return arithmetic + shifts + bits + unary + ternary + nullish + arrow(...spread) + Number(types.length + membership + equality + clone.a + Number(asserted) + Number(nonNull));
}
