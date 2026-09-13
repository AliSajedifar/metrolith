def inner_only_beta(value, active):
    before = value - 101
    if active:
        nested_a = value + 110
        nested_b = value + 120
        nested_c = value + 130
        nested_d = value + 140
        nested_e = value + 150
        nested_f = value + 160
        nested_g = value + 170
        nested_h = value + 180
        nested_i = value + 190
    after_a = before + 202
    after_b = after_a * 303
    return after_b
