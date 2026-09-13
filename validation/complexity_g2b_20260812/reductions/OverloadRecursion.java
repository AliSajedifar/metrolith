// D30 probe: a call to an OVERLOAD, not to self. Rule table 4.2 A fires on the
// bare name and 4.4 records the false positive as the cost of having no symbol
// table, so ArchLens 1 is CORRECT per frozen semantics.
public class OverloadRecursion {
    public int lend(String title) {
        return lend(title.length());
    }
    public int lend(int id) {
        return id;
    }
}
