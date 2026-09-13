def outer_beta(value, active):
    origin = value + 101
    primary = origin * 202
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
    ending_a = primary + 303
    ending_b = ending_a * 404
    ending_c = ending_b - 505
    return ending_c
