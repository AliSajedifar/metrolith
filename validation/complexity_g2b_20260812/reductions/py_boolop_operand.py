# D31 probe: a ternary inside a boolean operand.
# Rule table 3 + 6.2: the `or` is one run (1) and its operands are still
# traversed, so the ternary contributes S-TERNARY@0 (1). Total 2.
def ternary_inside_or(a, user):
    return a or (f(user) if user else None)


def f(x):
    return x
