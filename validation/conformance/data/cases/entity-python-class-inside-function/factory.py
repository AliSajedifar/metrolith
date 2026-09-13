def factory():
    class Inner:
        def method(self):
            return 1

    return Inner
