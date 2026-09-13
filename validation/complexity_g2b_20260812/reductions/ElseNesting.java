// D29 probe: nesting of a construct inside the `else` body of an if/else-if
// chain. Rule table 2.2 -> S-IF@0 1 + F-ELSEIF 1 + F-ELSE 1 + S-TERNARY@1 2 = 5.
public class ElseNesting {
    public String chainElseTernary(boolean a, boolean b, boolean c) {
        if (a) {
            return "a";
        } else if (b) {
            return "b";
        } else {
            return c ? "yes" : "no";
        }
    }
}
