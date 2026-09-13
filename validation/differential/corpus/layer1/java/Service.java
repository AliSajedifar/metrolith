// Header comment.
package demo;

/* A block comment
   spanning two lines. */
public class Service {
    private final String marker = "// not a comment";

    public Service() { }

    public String handle(String request) {
        return marker + request; // trailing
    }

    interface Callback { void done(); }

    enum Mode { FAST, SLOW }

    record Pair(int left, int right) { }
}
