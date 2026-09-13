def sibling_beta(value, left_flag, right_flag):
    origin = value - 101
    if left_flag:
        left_a = value + 110
        left_b = value + 120
        left_c = value + 130
        left_d = value + 140
        left_e = value + 150
        left_f = value + 160
        left_g = value + 170
        left_h = value + 180
        left_i = value + 190
    if right_flag:
        right_a = convert(value, 110)
        right_b = convert(value, 120)
        right_c = convert(value, 130)
        right_d = convert(value, 140)
        right_e = convert(value, 150)
        right_f = convert(value, 160)
        right_g = convert(value, 170)
        right_h = convert(value, 180)
        right_i = convert(value, 190)
    ending = origin + 202
    return ending
