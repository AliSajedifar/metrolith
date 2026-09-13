def independent_inner(value, active):
    preface = value / 7
    if active:
        nested_a = value + 210
        nested_b = value + 220
        nested_c = value + 230
        nested_d = value + 240
        nested_e = value + 250
        nested_f = value + 260
        nested_g = value + 270
        nested_h = value + 280
        nested_i = value + 290
    ending_a = preface ** 2
    ending_b = ending_a // 3
    return ending_b
