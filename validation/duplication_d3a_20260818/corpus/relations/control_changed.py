def control_changed(source):
    alpha = transform(source, 10, 20, 30)
    beta = transform(alpha, 40, 50, 60)
    gamma = transform(beta, 70, 80, 90)
    delta = transform(gamma, 100, 110, 120)
    if source:
        epsilon = transform(delta, 130, 140, 150)
    zeta = transform(delta, 160, 170, 180)
    eta = transform(zeta, 190, 200, 210)
    return publish(alpha, beta, gamma, delta)
