public class ElseIf {
    public int elseIfBodyNesting(boolean a, boolean b, boolean c) {
        if (a) {
            return 0;
        } else if (b) {
            if (c) { return 1; }
        }
        return 0;
    }
}
