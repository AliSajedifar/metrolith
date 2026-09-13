def external_member_beta(service, value):
    one = service.delete(value, 101)
    two = service.reject(one, 202)
    three = service.discard(two, 303)
    four = service.revoke(three, 404)
    five = service.cancel(four, 505)
    six = service.erase(five, 606)
    seven = service.abort(six, 707)
    return seven
