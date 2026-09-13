// Zero-suppression probe, Java / PMD CognitiveComplexity.
//
// Same design as the Go probe: a genuine zero and a control scoring 1, in one
// compilation unit, so an absent zero cannot be confused with an unread file.
public class ZeroProbe {

    // No rule-bearing construct. Frozen table: 0.
    public int zeroCallable(int a) {
        int b = a + 1;
        return b;
    }

    // Exactly one S-IF at nesting 0. Frozen table: 1.
    public int nonZeroCallable(int a) {
        if (a > 0) {
            return a;
        }
        return 0;
    }
}
