// G2-B minimal reproduction: Java F-RECURSION ignores the receiver.
//
// Frozen rule table 4.2 B recognizes a member self-call only when the receiver
// is `this` or the enclosing type name; 4.3 states that
// `arbitraryObject.sameName()` is NOT recursion merely because the name matches.
public class JavaRecursionReceiver {

    private Helper helper;

    // 4.2 A -- bare self-name call. Expected 1.
    public int bareSelfCall(int a) {
        return bareSelfCall(a);
    }

    // 4.2 B -- `this`-qualified. Expected 1.
    public int thisSelfCall(int a) {
        return this.thisSelfCall(a);
    }

    // 4.2 B -- enclosing type name. Expected 1.
    public static int typeSelfCall(int a) {
        return JavaRecursionReceiver.typeSelfCall(a);
    }

    // 4.3 -- same name, ANOTHER receiver (a field). Expected 0.
    public int fieldReceiverSameName(int a) {
        return helper.fieldReceiverSameName(a);
    }

    // 4.3 -- same name, ANOTHER type. Expected 0.
    public static int staticOtherTypeSameName(int a) {
        return Helper.staticOtherTypeSameName(a);
    }
}

class Helper {
    public int fieldReceiverSameName(int a) { return a; }
    public static int staticOtherTypeSameName(int a) { return a; }
}
