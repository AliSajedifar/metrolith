package d0;

import java.util.List;

final class TypeName {}

record CompactRecord(int value) {
    CompactRecord {
        int first = value + 1;
        int second = first + 1;
        int third = second + 1;
        value = third + 1;
    }
}

public class GrammarProbe<T extends TypeName> {
    static final long FIELD = 1L;
    private String member = "member";

    static {
        int first = 1;
        int second = first + 1;
        int third = second + 1;
        int fourth = third + 1;
    }

    {
        member = "one";
        member += "two";
        member = member.trim();
        member = member.toUpperCase();
    }

    public GrammarProbe() {
        this.member = "constructed";
        this.member += "!";
        this.member = this.member.trim();
        this.member = String.valueOf(this.member);
    }

    public synchronized int method(TypeName RoleName, int limit, String... rest) {
        Object nil = null;
        boolean truth = true;
        char character = 'λ';
        String string = "value\n";
        String text = """
                line one
                line two
                """;
        int integer = 0x2A;
        int decimal = 42;
        int octal = 052;
        int binary = 0b101010;
        double floating = 3.5e2;
        float single = 1.25f;
        double hexadecimalFloating = 0x1.0p2;
        long widened = 9L;

        class LocalType {
            int nestedMethod(int value) {
                int first = value + 1;
                int second = first * 2;
                int third = second / 3;
                return third;
            }
        }

        Runnable blockLambda = () -> {
            member = "lambda";
            integer++;
            truth = !truth;
            string += member;
        };
        Runnable expressionLambda = () -> member.trim();
        Runnable anonymous = new Runnable() {
            @Override public void run() {
                member = "anonymous";
                member += "!";
                member = member.trim();
                member = member.toUpperCase();
            }
        };

        if (truth && integer > 0) {
            integer += limit;
            member = string;
            truth = false;
            widened <<= 1;
        } else {
            integer -= limit;
            member = text;
            truth = true;
            widened >>= 1;
        }

        outer: for (int index = 0; index < limit; index++) {
            integer += index;
            member += index;
            truth |= index > 1;
            if (index == 2) continue outer;
        }

        for (String item : rest) {
            integer += item.length();
            member += item;
            truth = truth || item.isEmpty();
            widened += integer;
        }

        do {
            integer += 1;
            widened += 1L;
            truth = truth && integer < 1000;
            member += "d";
        } while (integer < 10);

        while (integer < 100) {
            integer++;
            widened--;
            truth = truth || integer == limit;
            member += ".";
        }

        try {
            integer = Integer.parseInt(member);
            widened += integer;
            truth = integer instanceof Integer;
            member = member.strip();
        } catch (RuntimeException error) {
            integer = -1;
            widened = 0L;
            truth = false;
            member = error.getMessage();
        } finally {
            integer += 1;
            widened += 1L;
            truth = !truth;
            member = String.valueOf(integer);
        }

        try (var resource = new java.io.ByteArrayInputStream(new byte[0])) {
            integer += resource.available();
            widened += integer;
            truth = resource.markSupported();
            member += "resource";
        } catch (java.io.IOException error) {
            integer = -2;
            widened = -2L;
            truth = false;
            member = error.getMessage();
        }

        synchronized (this) {
            integer ^= limit;
            widened >>>= 1;
            truth &= integer != 0;
            member += string;
        }

        {
            integer += 1;
            widened += 1L;
            truth = truth || integer > 0;
            member += "block";
        }

        switch (integer) {
            case 1:
                integer += 1;
                member = "one";
                truth = true;
                break;
            default:
                integer = 0;
                member = "default";
                truth = false;
                break;
        }

        int selected = switch (integer) {
            case 2 -> {
                int first = integer + 1;
                int second = first + 1;
                int third = second + 1;
                yield third + 1;
            }
            default -> 0;
        };
        return integer + selected + character + rest.length + List.of(rest).size();
    }
}
