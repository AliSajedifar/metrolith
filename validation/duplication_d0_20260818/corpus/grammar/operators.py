async def operator_probe(a, b, c, awaitable, *args, **kwargs):
    arithmetic = (a + b, a - b, a * b, a @ b, a / b, a // b, a % b, a ** b)
    bitwise = (a << b, a >> b, a | b, a ^ b, a & b)
    boolean = (a and b, a or b, not a)
    comparisons = (a == b, a != b, a < b, a <= b, a > b, a >= b, a is b, a is not b, a in c, a not in c)
    unary = (+a, -a, ~a)
    a += b
    a -= b
    a *= b
    a @= b
    a /= b
    a //= b
    a %= b
    a **= b
    a <<= b
    a >>= b
    a |= b
    a ^= b
    a &= b
    captured = (named := a)
    expanded = [*args, a]
    mapping = {**kwargs, "value": b}
    awaited = await awaitable
    yield from_value
    yield awaited
    return arithmetic, bitwise, boolean, comparisons, unary, captured, expanded, mapping, named
