# Does a construct in a loop/if HEADER sit at the construct's own level or one
# deeper? Rule table 2.1: a structural rule raises nesting for its BODY only.
def ternary_in_loop_iterable(items, c):
    """S-LOOP@0 1 + S-TERNARY@0 1 = 2."""
    for _ in (items if c else []):
        pass
    return 0


def ternary_in_loop_body(items, c):
    """S-LOOP@0 1 + S-TERNARY@1 2 = 3."""
    for _ in items:
        v = 1 if c else 2
    return 0


def ternary_in_if_test(c, d):
    """S-IF@0 1 + S-TERNARY@0 1 = 2."""
    if (d if c else False):
        pass
    return 0


def ternary_in_while_test(c, d):
    """S-LOOP@0 1 + S-TERNARY@0 1 = 2."""
    while (d if c else False):
        break
    return 0
