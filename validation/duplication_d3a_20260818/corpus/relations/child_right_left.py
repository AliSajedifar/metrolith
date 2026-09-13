def child_right_left(left, right):
    seed = pair(left, right, 10, 20)
    alpha = combine(seed, right + left, 30, 40)
    beta = combine(alpha, left * right, 50, 60)
    gamma = combine(beta, left - right, 70, 80)
    delta = combine(gamma, left / right, 90, 100)
    epsilon = combine(delta, left % right, 110, 120)
    zeta = combine(epsilon, left & right, 130, 140)
    return publish(alpha, beta, gamma, delta)
