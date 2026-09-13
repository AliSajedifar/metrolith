"""D28 characterization matrix — if/elif/else chains, one variable at a time.

Generated from a single template so the only differences between probes are the
dimensions under study. Every unrelated construct is absent: no boolean
operators, no comprehensions, no nested callables, no recursion, no `async`.

Dimensions varied independently:

* chain length      -- `if`; `if/elif`; `if/elif/elif`
* branch position   -- first / middle / final
* trailing `else`   -- present / absent
* construct         -- `if`, `for` loop, ternary, `try/except`
* nesting depth     -- 0 (directly in the branch) / 1 (inside one more `if`)
* construct count   -- one / two in the SAME branch

The expected ArchLens value of each probe is frozen in `d28_matrix_expected.json`
**from the frozen rule table and before the reference was run**. The formula, in
full, is rule table sections 2.1 and 2.2:

    S-IF for the chain head              1 + n
    F-ELSEIF per `elif`                  1        (flat)
    F-ELSE for a trailing bare `else`    1        (flat)
    every branch body sits at nesting    n + 1
    a structural construct there         1 + (n + 1)
    one extra enclosing `if` (depth 1)   1 + (n + 1) for the wrapper,
                                         1 + (n + 2) for the construct inside it

with n = 0, because every chain here is at the top of its function.
"""


def p001_if_len1_first_noelse_d0_x1(c1, c2, c3, items):
    if c1:
        if c1:
            pass
    return 0


def p002_if_len1_first_noelse_d0_x2(c1, c2, c3, items):
    if c1:
        if c1:
            pass
        if c1:
            pass
    return 0


def p003_if_len1_first_noelse_d1_x1(c1, c2, c3, items):
    if c1:
        if c2:
            if c1:
                pass
    return 0


def p004_loop_len1_first_noelse_d0_x1(c1, c2, c3, items):
    if c1:
        for _ in items:
            pass
    return 0


def p005_loop_len1_first_noelse_d0_x2(c1, c2, c3, items):
    if c1:
        for _ in items:
            pass
        for _ in items:
            pass
    return 0


def p006_loop_len1_first_noelse_d1_x1(c1, c2, c3, items):
    if c1:
        if c2:
            for _ in items:
                pass
    return 0


def p007_ternary_len1_first_noelse_d0_x1(c1, c2, c3, items):
    if c1:
        v = 1 if c1 else 2
    return 0


def p008_ternary_len1_first_noelse_d0_x2(c1, c2, c3, items):
    if c1:
        v = 1 if c1 else 2
        v = 1 if c1 else 2
    return 0


def p009_ternary_len1_first_noelse_d1_x1(c1, c2, c3, items):
    if c1:
        if c2:
            v = 1 if c1 else 2
    return 0


def p010_except_len1_first_noelse_d0_x1(c1, c2, c3, items):
    if c1:
        try:
            pass
        except ValueError:
            pass
    return 0


def p011_except_len1_first_noelse_d1_x1(c1, c2, c3, items):
    if c1:
        if c2:
            try:
                pass
            except ValueError:
                pass
    return 0


def p012_if_len1_first_else_d0_x1(c1, c2, c3, items):
    if c1:
        if c1:
            pass
    else:
        pass
    return 0


def p013_if_len1_first_else_d0_x2(c1, c2, c3, items):
    if c1:
        if c1:
            pass
        if c1:
            pass
    else:
        pass
    return 0


def p014_if_len1_first_else_d1_x1(c1, c2, c3, items):
    if c1:
        if c2:
            if c1:
                pass
    else:
        pass
    return 0


def p015_loop_len1_first_else_d0_x1(c1, c2, c3, items):
    if c1:
        for _ in items:
            pass
    else:
        pass
    return 0


def p016_loop_len1_first_else_d0_x2(c1, c2, c3, items):
    if c1:
        for _ in items:
            pass
        for _ in items:
            pass
    else:
        pass
    return 0


def p017_loop_len1_first_else_d1_x1(c1, c2, c3, items):
    if c1:
        if c2:
            for _ in items:
                pass
    else:
        pass
    return 0


def p018_ternary_len1_first_else_d0_x1(c1, c2, c3, items):
    if c1:
        v = 1 if c1 else 2
    else:
        pass
    return 0


def p019_ternary_len1_first_else_d0_x2(c1, c2, c3, items):
    if c1:
        v = 1 if c1 else 2
        v = 1 if c1 else 2
    else:
        pass
    return 0


def p020_ternary_len1_first_else_d1_x1(c1, c2, c3, items):
    if c1:
        if c2:
            v = 1 if c1 else 2
    else:
        pass
    return 0


def p021_except_len1_first_else_d0_x1(c1, c2, c3, items):
    if c1:
        try:
            pass
        except ValueError:
            pass
    else:
        pass
    return 0


def p022_except_len1_first_else_d1_x1(c1, c2, c3, items):
    if c1:
        if c2:
            try:
                pass
            except ValueError:
                pass
    else:
        pass
    return 0


def p023_if_len2_first_noelse_d0_x1(c1, c2, c3, items):
    if c1:
        if c1:
            pass
    elif c2:
        pass
    return 0


def p024_if_len2_first_noelse_d0_x2(c1, c2, c3, items):
    if c1:
        if c1:
            pass
        if c1:
            pass
    elif c2:
        pass
    return 0


def p025_if_len2_first_noelse_d1_x1(c1, c2, c3, items):
    if c1:
        if c2:
            if c1:
                pass
    elif c2:
        pass
    return 0


def p026_loop_len2_first_noelse_d0_x1(c1, c2, c3, items):
    if c1:
        for _ in items:
            pass
    elif c2:
        pass
    return 0


def p027_loop_len2_first_noelse_d0_x2(c1, c2, c3, items):
    if c1:
        for _ in items:
            pass
        for _ in items:
            pass
    elif c2:
        pass
    return 0


def p028_loop_len2_first_noelse_d1_x1(c1, c2, c3, items):
    if c1:
        if c2:
            for _ in items:
                pass
    elif c2:
        pass
    return 0


def p029_ternary_len2_first_noelse_d0_x1(c1, c2, c3, items):
    if c1:
        v = 1 if c1 else 2
    elif c2:
        pass
    return 0


def p030_ternary_len2_first_noelse_d0_x2(c1, c2, c3, items):
    if c1:
        v = 1 if c1 else 2
        v = 1 if c1 else 2
    elif c2:
        pass
    return 0


def p031_ternary_len2_first_noelse_d1_x1(c1, c2, c3, items):
    if c1:
        if c2:
            v = 1 if c1 else 2
    elif c2:
        pass
    return 0


def p032_except_len2_first_noelse_d0_x1(c1, c2, c3, items):
    if c1:
        try:
            pass
        except ValueError:
            pass
    elif c2:
        pass
    return 0


def p033_except_len2_first_noelse_d1_x1(c1, c2, c3, items):
    if c1:
        if c2:
            try:
                pass
            except ValueError:
                pass
    elif c2:
        pass
    return 0


def p034_if_len2_first_else_d0_x1(c1, c2, c3, items):
    if c1:
        if c1:
            pass
    elif c2:
        pass
    else:
        pass
    return 0


def p035_if_len2_first_else_d0_x2(c1, c2, c3, items):
    if c1:
        if c1:
            pass
        if c1:
            pass
    elif c2:
        pass
    else:
        pass
    return 0


def p036_if_len2_first_else_d1_x1(c1, c2, c3, items):
    if c1:
        if c2:
            if c1:
                pass
    elif c2:
        pass
    else:
        pass
    return 0


def p037_loop_len2_first_else_d0_x1(c1, c2, c3, items):
    if c1:
        for _ in items:
            pass
    elif c2:
        pass
    else:
        pass
    return 0


def p038_loop_len2_first_else_d0_x2(c1, c2, c3, items):
    if c1:
        for _ in items:
            pass
        for _ in items:
            pass
    elif c2:
        pass
    else:
        pass
    return 0


def p039_loop_len2_first_else_d1_x1(c1, c2, c3, items):
    if c1:
        if c2:
            for _ in items:
                pass
    elif c2:
        pass
    else:
        pass
    return 0


def p040_ternary_len2_first_else_d0_x1(c1, c2, c3, items):
    if c1:
        v = 1 if c1 else 2
    elif c2:
        pass
    else:
        pass
    return 0


def p041_ternary_len2_first_else_d0_x2(c1, c2, c3, items):
    if c1:
        v = 1 if c1 else 2
        v = 1 if c1 else 2
    elif c2:
        pass
    else:
        pass
    return 0


def p042_ternary_len2_first_else_d1_x1(c1, c2, c3, items):
    if c1:
        if c2:
            v = 1 if c1 else 2
    elif c2:
        pass
    else:
        pass
    return 0


def p043_except_len2_first_else_d0_x1(c1, c2, c3, items):
    if c1:
        try:
            pass
        except ValueError:
            pass
    elif c2:
        pass
    else:
        pass
    return 0


def p044_except_len2_first_else_d1_x1(c1, c2, c3, items):
    if c1:
        if c2:
            try:
                pass
            except ValueError:
                pass
    elif c2:
        pass
    else:
        pass
    return 0


def p045_if_len2_final_noelse_d0_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        if c1:
            pass
    return 0


def p046_if_len2_final_noelse_d0_x2(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        if c1:
            pass
        if c1:
            pass
    return 0


def p047_if_len2_final_noelse_d1_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        if c2:
            if c1:
                pass
    return 0


def p048_loop_len2_final_noelse_d0_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        for _ in items:
            pass
    return 0


def p049_loop_len2_final_noelse_d0_x2(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        for _ in items:
            pass
        for _ in items:
            pass
    return 0


def p050_loop_len2_final_noelse_d1_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        if c2:
            for _ in items:
                pass
    return 0


def p051_ternary_len2_final_noelse_d0_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        v = 1 if c1 else 2
    return 0


def p052_ternary_len2_final_noelse_d0_x2(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        v = 1 if c1 else 2
        v = 1 if c1 else 2
    return 0


def p053_ternary_len2_final_noelse_d1_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        if c2:
            v = 1 if c1 else 2
    return 0


def p054_except_len2_final_noelse_d0_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        try:
            pass
        except ValueError:
            pass
    return 0


def p055_except_len2_final_noelse_d1_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        if c2:
            try:
                pass
            except ValueError:
                pass
    return 0


def p056_if_len2_final_else_d0_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        if c1:
            pass
    else:
        pass
    return 0


def p057_if_len2_final_else_d0_x2(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        if c1:
            pass
        if c1:
            pass
    else:
        pass
    return 0


def p058_if_len2_final_else_d1_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        if c2:
            if c1:
                pass
    else:
        pass
    return 0


def p059_loop_len2_final_else_d0_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        for _ in items:
            pass
    else:
        pass
    return 0


def p060_loop_len2_final_else_d0_x2(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        for _ in items:
            pass
        for _ in items:
            pass
    else:
        pass
    return 0


def p061_loop_len2_final_else_d1_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        if c2:
            for _ in items:
                pass
    else:
        pass
    return 0


def p062_ternary_len2_final_else_d0_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        v = 1 if c1 else 2
    else:
        pass
    return 0


def p063_ternary_len2_final_else_d0_x2(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        v = 1 if c1 else 2
        v = 1 if c1 else 2
    else:
        pass
    return 0


def p064_ternary_len2_final_else_d1_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        if c2:
            v = 1 if c1 else 2
    else:
        pass
    return 0


def p065_except_len2_final_else_d0_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        try:
            pass
        except ValueError:
            pass
    else:
        pass
    return 0


def p066_except_len2_final_else_d1_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        if c2:
            try:
                pass
            except ValueError:
                pass
    else:
        pass
    return 0


def p067_if_len3_first_noelse_d0_x1(c1, c2, c3, items):
    if c1:
        if c1:
            pass
    elif c2:
        pass
    elif c3:
        pass
    return 0


def p068_if_len3_first_noelse_d0_x2(c1, c2, c3, items):
    if c1:
        if c1:
            pass
        if c1:
            pass
    elif c2:
        pass
    elif c3:
        pass
    return 0


def p069_if_len3_first_noelse_d1_x1(c1, c2, c3, items):
    if c1:
        if c2:
            if c1:
                pass
    elif c2:
        pass
    elif c3:
        pass
    return 0


def p070_loop_len3_first_noelse_d0_x1(c1, c2, c3, items):
    if c1:
        for _ in items:
            pass
    elif c2:
        pass
    elif c3:
        pass
    return 0


def p071_loop_len3_first_noelse_d0_x2(c1, c2, c3, items):
    if c1:
        for _ in items:
            pass
        for _ in items:
            pass
    elif c2:
        pass
    elif c3:
        pass
    return 0


def p072_loop_len3_first_noelse_d1_x1(c1, c2, c3, items):
    if c1:
        if c2:
            for _ in items:
                pass
    elif c2:
        pass
    elif c3:
        pass
    return 0


def p073_ternary_len3_first_noelse_d0_x1(c1, c2, c3, items):
    if c1:
        v = 1 if c1 else 2
    elif c2:
        pass
    elif c3:
        pass
    return 0


def p074_ternary_len3_first_noelse_d0_x2(c1, c2, c3, items):
    if c1:
        v = 1 if c1 else 2
        v = 1 if c1 else 2
    elif c2:
        pass
    elif c3:
        pass
    return 0


def p075_ternary_len3_first_noelse_d1_x1(c1, c2, c3, items):
    if c1:
        if c2:
            v = 1 if c1 else 2
    elif c2:
        pass
    elif c3:
        pass
    return 0


def p076_except_len3_first_noelse_d0_x1(c1, c2, c3, items):
    if c1:
        try:
            pass
        except ValueError:
            pass
    elif c2:
        pass
    elif c3:
        pass
    return 0


def p077_except_len3_first_noelse_d1_x1(c1, c2, c3, items):
    if c1:
        if c2:
            try:
                pass
            except ValueError:
                pass
    elif c2:
        pass
    elif c3:
        pass
    return 0


def p078_if_len3_first_else_d0_x1(c1, c2, c3, items):
    if c1:
        if c1:
            pass
    elif c2:
        pass
    elif c3:
        pass
    else:
        pass
    return 0


def p079_if_len3_first_else_d0_x2(c1, c2, c3, items):
    if c1:
        if c1:
            pass
        if c1:
            pass
    elif c2:
        pass
    elif c3:
        pass
    else:
        pass
    return 0


def p080_if_len3_first_else_d1_x1(c1, c2, c3, items):
    if c1:
        if c2:
            if c1:
                pass
    elif c2:
        pass
    elif c3:
        pass
    else:
        pass
    return 0


def p081_loop_len3_first_else_d0_x1(c1, c2, c3, items):
    if c1:
        for _ in items:
            pass
    elif c2:
        pass
    elif c3:
        pass
    else:
        pass
    return 0


def p082_loop_len3_first_else_d0_x2(c1, c2, c3, items):
    if c1:
        for _ in items:
            pass
        for _ in items:
            pass
    elif c2:
        pass
    elif c3:
        pass
    else:
        pass
    return 0


def p083_loop_len3_first_else_d1_x1(c1, c2, c3, items):
    if c1:
        if c2:
            for _ in items:
                pass
    elif c2:
        pass
    elif c3:
        pass
    else:
        pass
    return 0


def p084_ternary_len3_first_else_d0_x1(c1, c2, c3, items):
    if c1:
        v = 1 if c1 else 2
    elif c2:
        pass
    elif c3:
        pass
    else:
        pass
    return 0


def p085_ternary_len3_first_else_d0_x2(c1, c2, c3, items):
    if c1:
        v = 1 if c1 else 2
        v = 1 if c1 else 2
    elif c2:
        pass
    elif c3:
        pass
    else:
        pass
    return 0


def p086_ternary_len3_first_else_d1_x1(c1, c2, c3, items):
    if c1:
        if c2:
            v = 1 if c1 else 2
    elif c2:
        pass
    elif c3:
        pass
    else:
        pass
    return 0


def p087_except_len3_first_else_d0_x1(c1, c2, c3, items):
    if c1:
        try:
            pass
        except ValueError:
            pass
    elif c2:
        pass
    elif c3:
        pass
    else:
        pass
    return 0


def p088_except_len3_first_else_d1_x1(c1, c2, c3, items):
    if c1:
        if c2:
            try:
                pass
            except ValueError:
                pass
    elif c2:
        pass
    elif c3:
        pass
    else:
        pass
    return 0


def p089_if_len3_middle_noelse_d0_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        if c1:
            pass
    elif c3:
        pass
    return 0


def p090_if_len3_middle_noelse_d0_x2(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        if c1:
            pass
        if c1:
            pass
    elif c3:
        pass
    return 0


def p091_if_len3_middle_noelse_d1_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        if c2:
            if c1:
                pass
    elif c3:
        pass
    return 0


def p092_loop_len3_middle_noelse_d0_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        for _ in items:
            pass
    elif c3:
        pass
    return 0


def p093_loop_len3_middle_noelse_d0_x2(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        for _ in items:
            pass
        for _ in items:
            pass
    elif c3:
        pass
    return 0


def p094_loop_len3_middle_noelse_d1_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        if c2:
            for _ in items:
                pass
    elif c3:
        pass
    return 0


def p095_ternary_len3_middle_noelse_d0_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        v = 1 if c1 else 2
    elif c3:
        pass
    return 0


def p096_ternary_len3_middle_noelse_d0_x2(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        v = 1 if c1 else 2
        v = 1 if c1 else 2
    elif c3:
        pass
    return 0


def p097_ternary_len3_middle_noelse_d1_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        if c2:
            v = 1 if c1 else 2
    elif c3:
        pass
    return 0


def p098_except_len3_middle_noelse_d0_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        try:
            pass
        except ValueError:
            pass
    elif c3:
        pass
    return 0


def p099_except_len3_middle_noelse_d1_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        if c2:
            try:
                pass
            except ValueError:
                pass
    elif c3:
        pass
    return 0


def p100_if_len3_middle_else_d0_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        if c1:
            pass
    elif c3:
        pass
    else:
        pass
    return 0


def p101_if_len3_middle_else_d0_x2(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        if c1:
            pass
        if c1:
            pass
    elif c3:
        pass
    else:
        pass
    return 0


def p102_if_len3_middle_else_d1_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        if c2:
            if c1:
                pass
    elif c3:
        pass
    else:
        pass
    return 0


def p103_loop_len3_middle_else_d0_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        for _ in items:
            pass
    elif c3:
        pass
    else:
        pass
    return 0


def p104_loop_len3_middle_else_d0_x2(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        for _ in items:
            pass
        for _ in items:
            pass
    elif c3:
        pass
    else:
        pass
    return 0


def p105_loop_len3_middle_else_d1_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        if c2:
            for _ in items:
                pass
    elif c3:
        pass
    else:
        pass
    return 0


def p106_ternary_len3_middle_else_d0_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        v = 1 if c1 else 2
    elif c3:
        pass
    else:
        pass
    return 0


def p107_ternary_len3_middle_else_d0_x2(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        v = 1 if c1 else 2
        v = 1 if c1 else 2
    elif c3:
        pass
    else:
        pass
    return 0


def p108_ternary_len3_middle_else_d1_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        if c2:
            v = 1 if c1 else 2
    elif c3:
        pass
    else:
        pass
    return 0


def p109_except_len3_middle_else_d0_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        try:
            pass
        except ValueError:
            pass
    elif c3:
        pass
    else:
        pass
    return 0


def p110_except_len3_middle_else_d1_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        if c2:
            try:
                pass
            except ValueError:
                pass
    elif c3:
        pass
    else:
        pass
    return 0


def p111_if_len3_final_noelse_d0_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        pass
    elif c3:
        if c1:
            pass
    return 0


def p112_if_len3_final_noelse_d0_x2(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        pass
    elif c3:
        if c1:
            pass
        if c1:
            pass
    return 0


def p113_if_len3_final_noelse_d1_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        pass
    elif c3:
        if c2:
            if c1:
                pass
    return 0


def p114_loop_len3_final_noelse_d0_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        pass
    elif c3:
        for _ in items:
            pass
    return 0


def p115_loop_len3_final_noelse_d0_x2(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        pass
    elif c3:
        for _ in items:
            pass
        for _ in items:
            pass
    return 0


def p116_loop_len3_final_noelse_d1_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        pass
    elif c3:
        if c2:
            for _ in items:
                pass
    return 0


def p117_ternary_len3_final_noelse_d0_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        pass
    elif c3:
        v = 1 if c1 else 2
    return 0


def p118_ternary_len3_final_noelse_d0_x2(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        pass
    elif c3:
        v = 1 if c1 else 2
        v = 1 if c1 else 2
    return 0


def p119_ternary_len3_final_noelse_d1_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        pass
    elif c3:
        if c2:
            v = 1 if c1 else 2
    return 0


def p120_except_len3_final_noelse_d0_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        pass
    elif c3:
        try:
            pass
        except ValueError:
            pass
    return 0


def p121_except_len3_final_noelse_d1_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        pass
    elif c3:
        if c2:
            try:
                pass
            except ValueError:
                pass
    return 0


def p122_if_len3_final_else_d0_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        pass
    elif c3:
        if c1:
            pass
    else:
        pass
    return 0


def p123_if_len3_final_else_d0_x2(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        pass
    elif c3:
        if c1:
            pass
        if c1:
            pass
    else:
        pass
    return 0


def p124_if_len3_final_else_d1_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        pass
    elif c3:
        if c2:
            if c1:
                pass
    else:
        pass
    return 0


def p125_loop_len3_final_else_d0_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        pass
    elif c3:
        for _ in items:
            pass
    else:
        pass
    return 0


def p126_loop_len3_final_else_d0_x2(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        pass
    elif c3:
        for _ in items:
            pass
        for _ in items:
            pass
    else:
        pass
    return 0


def p127_loop_len3_final_else_d1_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        pass
    elif c3:
        if c2:
            for _ in items:
                pass
    else:
        pass
    return 0


def p128_ternary_len3_final_else_d0_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        pass
    elif c3:
        v = 1 if c1 else 2
    else:
        pass
    return 0


def p129_ternary_len3_final_else_d0_x2(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        pass
    elif c3:
        v = 1 if c1 else 2
        v = 1 if c1 else 2
    else:
        pass
    return 0


def p130_ternary_len3_final_else_d1_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        pass
    elif c3:
        if c2:
            v = 1 if c1 else 2
    else:
        pass
    return 0


def p131_except_len3_final_else_d0_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        pass
    elif c3:
        try:
            pass
        except ValueError:
            pass
    else:
        pass
    return 0


def p132_except_len3_final_else_d1_x1(c1, c2, c3, items):
    if c1:
        pass
    elif c2:
        pass
    elif c3:
        if c2:
            try:
                pass
            except ValueError:
                pass
    else:
        pass
    return 0
