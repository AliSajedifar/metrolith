# D28 rate probe: is the effect -1 per STRUCTURAL CONSTRUCT in a non-final
# branch, or -1 per offending branch?
def one_construct(a, b, d):
    """S-IF@0 1 + S-TERNARY@1 2 + F-ELSEIF 1 = 4."""
    if a:
        x = 1 if b else 2
    elif d:
        return 0
    return x


def two_constructs_one_branch(a, b, c, d):
    """S-IF@0 1 + S-TERNARY@1 2 + S-TERNARY@1 2 + F-ELSEIF 1 = 6."""
    if a:
        x = 1 if b else 2
        y = 3 if c else 4
    elif d:
        return 0
    return x, y


def two_branches_one_each(a, b, c, d):
    """S-IF@0 1 + S-TERNARY@1 2 + F-ELSEIF 1 + S-TERNARY@1 2 + F-ELSEIF 1 = 7."""
    if a:
        x = 1 if b else 2
    elif c:
        y = 3 if d else 4
    elif d:
        return 0
    return 0


def two_constructs_with_trailing_else(a, b, c, d):
    """With a bare `else` closing the chain, every if/elif body is non-final.
    S-IF@0 1 + S-TERNARY@1 2 + F-ELSEIF 1 + S-TERNARY@1 2 + F-ELSE 1 = 7."""
    if a:
        x = 1 if b else 2
    elif c:
        x = 3 if d else 4
    else:
        x = 5
    return x
