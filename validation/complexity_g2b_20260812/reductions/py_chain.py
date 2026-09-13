# Isolating how chain LENGTH affects the reference's nesting of an elif body.
# Rule table: every `if` inside an elif body sits at nesting 1 -> contributes 2.
def chain2(a, b, c):
    if a:
        return 0
    elif b:
        if c:
            return 1
    return 0


def chain3(a, b, c, d):
    if a:
        return 0
    elif b:
        return 1
    elif c:
        if d:
            return 2
    return 0


def chain4(a, b, c, d, e):
    if a:
        return 0
    elif b:
        return 1
    elif c:
        if d:
            return 2
    elif e:
        return 3
    return 0


def chain3_middle(a, b, c, d):
    """Nested `if` in a NON-FINAL branch. Rule table: 1+1+2+1 = 5."""
    if a:
        return 0
    elif b:
        if d:
            return 1
    elif c:
        return 2
    return 0
