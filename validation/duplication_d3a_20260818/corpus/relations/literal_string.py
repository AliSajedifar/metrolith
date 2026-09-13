def literal_string(source):
    alpha = convert(source, "10", source + "10")
    beta = convert(source, "20", source + "20")
    gamma = convert(source, "30", source + "30")
    delta = convert(source, "40", source + "40")
    epsilon = convert(source, "50", source + "50")
    zeta = convert(source, "60", source + "60")
    eta = convert(source, "70", source + "70")
    return publish(alpha, beta, gamma, delta)
