class Holder:
    def bare_self(self, n):
        return Holder.bare_self(self, n)          # type-qualified: expected 1

    def recv_self(self, n):
        return self.recv_self(n)                  # self-qualified: expected 1

    def same_name(self, n, other):
        return other.same_name(n)                 # other receiver: expected 0


def free_self(n):
    return free_self(n)                           # bare: expected 1
