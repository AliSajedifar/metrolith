// Independent Java complexity reference for ArchLens differential validation.
//
// Uses the JDK's own front end (com.sun.source Compiler Tree API), not
// tree-sitter, so it shares no parser, grammar, traversal or rule table with
// ArchLens. Parsing only: no attribution, no symbol resolution.
//
// Implemented from docs/COMPLEXITY_CONTRACT_V1.md and the hand-authored
// synthetic Java expectations. No ArchLens metric output was consulted.
//
// Canonical population (ArchLens's, implemented independently): methods with a
// body whose owner is a NAMED type. Constructors, body-less declarations,
// anonymous-class methods, lambdas and initializer blocks are excluded. Methods
// of named local classes, enum bodies and interface default/static methods are
// INCLUDED, matching the reviewed mapping.
//
// Not ground truth. javac is a reference front end whose disagreements are
// findings about both sides until adjudicated.

import com.sun.source.tree.*;
import com.sun.source.util.JavacTask;
import com.sun.source.util.SourcePositions;
import com.sun.source.util.Trees;

import javax.tools.Diagnostic;
import javax.tools.JavaCompiler;
import javax.tools.JavaFileObject;
import javax.tools.StandardJavaFileManager;
import javax.tools.ToolProvider;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;
import java.util.TreeSet;

public final class ReferenceComplexity {

    // Versioned independently of the JDK: the adapter's RULES can change while
    // the compiler stays fixed, and a reader must be able to tell which moved.
    static final String ADAPTER_VERSION = "2.0.0";

    static final class Record {
        String name;
        String qualifiedName;
        int startLine;
        int endLine;
        int nloc;
        int formalParameterCount;
        Integer decisionPointCount;
        Integer booleanOperatorCount;
        Integer maxConditionOperatorCount;
        Integer maxNestingDepth;
        Integer cyclomaticComplexity;
        String notEvaluableReason;
    }

    // ----------------------------------------------------------------------
    // clean-room comment masking, so a line mixing code and a trailing comment
    // still counts as code. javac exposes no comment table at the parse phase.
    // ----------------------------------------------------------------------
    static String maskComments(String source) {
        StringBuilder out = new StringBuilder(source.length());
        int index = 0;
        boolean inLine = false, inBlock = false, inString = false, inChar = false;
        boolean inTextBlock = false;
        while (index < source.length()) {
            char current = source.charAt(index);
            char next = index + 1 < source.length() ? source.charAt(index + 1) : '\0';
            if (inLine) {
                if (current == '\n') { inLine = false; out.append(current); }
                else { out.append(' '); }
                index++;
            } else if (inBlock) {
                if (current == '*' && next == '/') { inBlock = false; out.append("  "); index += 2; }
                else { out.append(current == '\n' ? '\n' : ' '); index++; }
            } else if (inTextBlock) {
                out.append(current);
                if (current == '"' && index + 2 < source.length()
                        && source.charAt(index + 1) == '"' && source.charAt(index + 2) == '"') {
                    out.append("\"\""); index += 3; inTextBlock = false;
                } else { index++; }
            } else if (inString) {
                out.append(current);
                if (current == '\\') {
                    if (index + 1 < source.length()) { out.append(next); index += 2; continue; }
                }
                if (current == '"') { inString = false; }
                index++;
            } else if (inChar) {
                out.append(current);
                if (current == '\\') {
                    if (index + 1 < source.length()) { out.append(next); index += 2; continue; }
                }
                if (current == '\'') { inChar = false; }
                index++;
            } else if (current == '/' && next == '/') {
                inLine = true; out.append("  "); index += 2;
            } else if (current == '/' && next == '*') {
                inBlock = true; out.append("  "); index += 2;
            } else if (current == '"' && next == '"' && index + 2 < source.length()
                    && source.charAt(index + 2) == '"') {
                inTextBlock = true; out.append("\"\"\""); index += 3;
            } else if (current == '"') {
                inString = true; out.append(current); index++;
            } else if (current == '\'') {
                inChar = true; out.append(current); index++;
            } else {
                out.append(current); index++;
            }
        }
        return out.toString();
    }

    static int countCodeLines(String[] raw, String[] masked, int start, int end) {
        int total = 0;
        for (int line = start - 1; line < end && line < raw.length; line++) {
            if (!raw[line].trim().isEmpty() && !masked[line].trim().isEmpty()) {
                total++;
            }
        }
        return total;
    }

    // ----------------------------------------------------------------------
    // metric computation -- bounded descent, stopping at every nested callable
    // or class boundary (contract section 3).
    // ----------------------------------------------------------------------
    static final class Measure {
        int decisions = 0;
        int booleans = 0;
        int maxCondition = 0;
        int maxDepth = 0;

        static boolean isBoundary(Tree tree) {
            if (tree == null) return false;
            Tree.Kind kind = tree.getKind();
            return kind == Tree.Kind.LAMBDA_EXPRESSION
                    || kind == Tree.Kind.CLASS || kind == Tree.Kind.INTERFACE
                    || kind == Tree.Kind.ENUM || kind == Tree.Kind.RECORD
                    || kind == Tree.Kind.ANNOTATION_TYPE
                    || kind == Tree.Kind.METHOD
                    || (kind == Tree.Kind.NEW_CLASS
                        && ((NewClassTree) tree).getClassBody() != null);
        }

        /** && and || occurrences inside one expression, boundaries excluded. */
        int countBooleans(Tree tree) {
            if (tree == null || isBoundary(tree)) return 0;
            int total = 0;
            if (tree.getKind() == Tree.Kind.CONDITIONAL_AND
                    || tree.getKind() == Tree.Kind.CONDITIONAL_OR) {
                total++;
            }
            for (Tree child : childrenOf(tree)) {
                total += countBooleans(child);
            }
            return total;
        }

        void condition(Tree tree) {
            int found = countBooleans(tree);
            if (found > maxCondition) maxCondition = found;
        }

        void note(int depth) {
            if (depth > maxDepth) maxDepth = depth;
        }

        /** Expressions contribute operators and ternaries, never nesting. */
        void expression(Tree tree, int depth) {
            if (tree == null || isBoundary(tree)) return;
            Tree.Kind kind = tree.getKind();
            if (kind == Tree.Kind.CONDITIONAL_AND || kind == Tree.Kind.CONDITIONAL_OR) {
                booleans++;
            } else if (kind == Tree.Kind.CONDITIONAL_EXPRESSION) {
                decisions++;
                condition(((ConditionalExpressionTree) tree).getCondition());
            } else if (kind == Tree.Kind.SWITCH_EXPRESSION) {
                // A switch EXPRESSION (`return switch (c) { case 1 -> 1; }`)
                // reaches the traversal through an expression, not a statement.
                // Its arms are decisions exactly as a switch statement's are;
                // routing it here is what makes the arrow form count.
                switchArms(((SwitchExpressionTree) tree).getCases(), depth);
                return;
            }
            for (Tree child : childrenOf(tree)) {
                expression(child, depth);
            }
        }

        void statement(Tree tree, int depth) {
            if (tree == null || isBoundary(tree)) return;
            switch (tree.getKind()) {
                case IF -> {
                    IfTree node = (IfTree) tree;
                    decisions++;
                    condition(node.getCondition());
                    expression(node.getCondition(), depth);
                    statementBody(node.getThenStatement(), depth);
                    Tree alternative = node.getElseStatement();
                    if (alternative != null) {
                        // `else if` is an IfTree, not a block: it stays at this
                        // depth so the chain measures flat. A real `else` block
                        // nests.
                        if (alternative.getKind() == Tree.Kind.IF) {
                            statement(alternative, depth);
                        } else {
                            statementBody(alternative, depth);
                        }
                    }
                }
                case FOR_LOOP -> {
                    ForLoopTree node = (ForLoopTree) tree;
                    decisions++;
                    condition(node.getCondition());
                    expression(node.getCondition(), depth);
                    statementBody(node.getStatement(), depth);
                }
                case ENHANCED_FOR_LOOP -> {
                    decisions++;
                    statementBody(((EnhancedForLoopTree) tree).getStatement(), depth);
                }
                case WHILE_LOOP -> {
                    WhileLoopTree node = (WhileLoopTree) tree;
                    decisions++;
                    condition(node.getCondition());
                    expression(node.getCondition(), depth);
                    statementBody(node.getStatement(), depth);
                }
                case DO_WHILE_LOOP -> {
                    DoWhileLoopTree node = (DoWhileLoopTree) tree;
                    decisions++;
                    condition(node.getCondition());
                    expression(node.getCondition(), depth);
                    statementBody(node.getStatement(), depth);
                }
                case SWITCH -> switchArms(((SwitchTree) tree).getCases(), depth);
                case TRY -> {
                    TryTree node = (TryTree) tree;
                    statementBody(node.getBlock(), depth);
                    for (CatchTree clause : node.getCatches()) {
                        decisions++;   // `catch` is a decision; `finally` is not
                        statementBody(clause.getBlock(), depth);
                    }
                    if (node.getFinallyBlock() != null) {
                        statementBody(node.getFinallyBlock(), depth);
                    }
                }
                case SYNCHRONIZED -> {
                    // Contract section 10: 0 decisions, +1 nesting. Nesting is
                    // STRUCTURAL nesting.
                    statementBody(((SynchronizedTree) tree).getBlock(), depth);
                }
                case BLOCK -> {
                    // A bare block is not attached to a control construct and
                    // opens no level.
                    for (StatementTree child : ((BlockTree) tree).getStatements()) {
                        note(depth);
                        statement(child, depth);
                    }
                }
                case LABELED_STATEMENT ->
                        statement(((LabeledStatementTree) tree).getStatement(), depth);
                default -> {
                    for (Tree child : childrenOf(tree)) {
                        if (child instanceof StatementTree && !(tree instanceof ExpressionTree)) {
                            statement(child, depth);
                        } else {
                            expression(child, depth);
                        }
                    }
                }
            }
        }

        /** A control construct's body opens exactly one nesting level. */
        void statementBody(Tree body, int depth) {
            if (body == null) return;
            if (body.getKind() == Tree.Kind.BLOCK) {
                for (StatementTree child : ((BlockTree) body).getStatements()) {
                    note(depth + 1);
                    statement(child, depth + 1);
                }
            } else {
                note(depth + 1);
                statement(body, depth + 1);
            }
        }

        /** A switch opens one level and each arm opens another. */
        void switchArms(List<? extends CaseTree> cases, int depth) {
            for (CaseTree arm : cases) {
                boolean isDefault = arm.getExpressions() == null
                        || arm.getExpressions().isEmpty();
                if (!isDefault) {
                    decisions++;   // one per arm; `case 1, 2 ->` is ONE arm
                    for (ExpressionTree value : arm.getExpressions()) {
                        condition(value);
                        expression(value, depth);
                    }
                }
                Tree body = arm.getBody();
                if (body != null) {
                    if (body.getKind() == Tree.Kind.BLOCK) {
                        for (StatementTree child : ((BlockTree) body).getStatements()) {
                            note(depth + 2);
                            statement(child, depth + 2);
                        }
                    } else {
                        note(depth + 2);
                        statement(body, depth + 2);
                    }
                } else {
                    for (StatementTree child : arm.getStatements()) {
                        note(depth + 2);
                        statement(child, depth + 2);
                    }
                }
            }
        }
    }

    /** Structural children, via the visitor rather than reflection. */
    static List<Tree> childrenOf(Tree tree) {
        List<Tree> found = new ArrayList<>();
        tree.accept(new com.sun.source.util.SimpleTreeVisitor<Void, Void>() {
            @Override
            protected Void defaultAction(Tree node, Void unused) {
                return null;
            }
        }, null);
        // SimpleTreeVisitor gives no child list, so use a scanner limited to
        // depth 1.
        new com.sun.source.util.TreeScanner<Void, Void>() {
            boolean atRoot = true;
            @Override
            public Void scan(Tree node, Void unused) {
                if (atRoot) { atRoot = false; return super.scan(node, unused); }
                if (node != null) found.add(node);
                return null;
            }
        }.scan(tree, null);
        return found;
    }

    public static void main(String[] args) throws IOException {
        if (args.length < 1) {
            System.err.println("usage: ReferenceComplexity <file-list>");
            System.exit(2);
        }
        List<String> files = new ArrayList<>();
        for (String line : Files.readAllLines(Path.of(args[0]), StandardCharsets.UTF_8)) {
            String trimmed = line.trim();
            if (!trimmed.isEmpty()) files.add(trimmed);
        }

        JavaCompiler compiler = ToolProvider.getSystemJavaCompiler();
        if (compiler == null) {
            System.err.println("{\"error\":\"no system Java compiler available\"}");
            System.exit(3);
        }

        StringBuilder out = new StringBuilder();
        out.append("{\"adapter_version\":\"").append(ADAPTER_VERSION).append("\",\"files\":[");

        for (int index = 0; index < files.size(); index++) {
            if (index > 0) out.append(',');
            String file = files.get(index);
            String source = Files.readString(Path.of(file), StandardCharsets.UTF_8);
            String[] raw = source.split("\n", -1);
            String[] masked = maskComments(source).split("\n", -1);
            List<Record> records = new ArrayList<>();
            TreeSet<Long> errorLines = new TreeSet<>();

            try (StandardJavaFileManager manager =
                         compiler.getStandardFileManager(null, null, StandardCharsets.UTF_8)) {
                Iterable<? extends JavaFileObject> units =
                        manager.getJavaFileObjectsFromStrings(List.of(file));
                JavacTask task = (JavacTask) compiler.getTask(
                        null, manager,
                        diagnostic -> {
                            if (diagnostic.getKind() == Diagnostic.Kind.ERROR) {
                                errorLines.add(diagnostic.getLineNumber());
                            }
                        },
                        null, null, units);
                Trees trees = Trees.instance(task);
                for (CompilationUnitTree unit : task.parse()) {
                    SourcePositions positions = trees.getSourcePositions();
                    LineMap lineMap = unit.getLineMap();
                    Deque<String> owners = new ArrayDeque<>();

                    new com.sun.source.util.TreeScanner<Void, Boolean>() {
                        @Override
                        public Void visitClass(ClassTree node, Boolean insideAnonymous) {
                            boolean anonymous = node.getSimpleName() == null
                                    || node.getSimpleName().length() == 0;
                            if (!anonymous) owners.addLast(node.getSimpleName().toString());
                            Void result = super.visitClass(node, anonymous);
                            if (!anonymous) owners.removeLast();
                            return result;
                        }

                        @Override
                        public Void visitNewClass(NewClassTree node, Boolean insideAnonymous) {
                            // An anonymous class body is a boundary: its methods
                            // are anonymous_class_methods, outside the canonical
                            // population.
                            if (node.getClassBody() != null) {
                                return null;
                            }
                            return super.visitNewClass(node, insideAnonymous);
                        }

                        @Override
                        public Void visitMethod(MethodTree node, Boolean insideAnonymous) {
                            boolean constructor = node.getName().contentEquals("<init>");
                            if (node.getBody() == null || constructor
                                    || Boolean.TRUE.equals(insideAnonymous)) {
                                return super.visitMethod(node, insideAnonymous);
                            }

                            long startPos = positions.getStartPosition(unit, node);
                            long endPos = positions.getEndPosition(unit, node);
                            int startLine = (int) lineMap.getLineNumber(startPos);
                            int endLine = (int) lineMap.getLineNumber(endPos);

                            Record record = new Record();
                            record.name = node.getName().toString();
                            record.qualifiedName = String.join(".", owners)
                                    + (owners.isEmpty() ? "" : ".") + record.name;
                            record.startLine = startLine;
                            record.endLine = endLine;
                            record.nloc = countCodeLines(raw, masked, startLine, endLine);
                            // javac keeps the receiver parameter OUT of
                            // getParameters(), so the exclusion is structural.
                            // A varargs parameter is a single VariableTree.
                            record.formalParameterCount = node.getParameters().size();

                            boolean unparsed = false;
                            for (Long line : errorLines) {
                                if (line >= startLine && line <= endLine) { unparsed = true; break; }
                            }
                            if (unparsed) {
                                record.notEvaluableReason = "javac_parse_error_in_span";
                            } else {
                                Measure measure = new Measure();
                                for (StatementTree statement : node.getBody().getStatements()) {
                                    measure.note(0);
                                    measure.statement(statement, 0);
                                }
                                record.decisionPointCount = measure.decisions;
                                record.booleanOperatorCount = measure.booleans;
                                record.maxConditionOperatorCount = measure.maxCondition;
                                record.maxNestingDepth = measure.maxDepth;
                                record.cyclomaticComplexity =
                                        1 + measure.decisions + measure.booleans;
                            }
                            records.add(record);
                            return super.visitMethod(node, insideAnonymous);
                        }
                    }.scan(unit, false);
                }
            }

            out.append("{\"path\":").append(quote(file))
               .append(",\"adapter_version\":").append(quote(ADAPTER_VERSION))
               .append(",\"callables\":[");
            for (int r = 0; r < records.size(); r++) {
                if (r > 0) out.append(',');
                Record record = records.get(r);
                out.append("{\"name\":").append(quote(record.name))
                   .append(",\"qualified_name\":").append(quote(record.qualifiedName))
                   .append(",\"callable_kind\":\"class_method\"")
                   .append(",\"start_line\":").append(record.startLine)
                   .append(",\"end_line\":").append(record.endLine)
                   .append(",\"nloc\":").append(record.nloc)
                   .append(",\"formal_parameter_count\":").append(record.formalParameterCount)
                   .append(",\"decision_point_count\":").append(record.decisionPointCount)
                   .append(",\"boolean_operator_count\":").append(record.booleanOperatorCount)
                   .append(",\"max_condition_operator_count\":").append(record.maxConditionOperatorCount)
                   .append(",\"max_nesting_depth\":").append(record.maxNestingDepth)
                   .append(",\"cyclomatic_complexity\":").append(record.cyclomaticComplexity)
                   .append(",\"not_evaluable_reason\":")
                   .append(record.notEvaluableReason == null ? "null" : quote(record.notEvaluableReason))
                   .append('}');
            }
            out.append("]}");
        }
        out.append("]}");
        System.out.println(out);
    }

    static String quote(String value) {
        StringBuilder builder = new StringBuilder("\"");
        for (char character : value.toCharArray()) {
            switch (character) {
                case '"' -> builder.append("\\\"");
                case '\\' -> builder.append("\\\\");
                case '\n' -> builder.append("\\n");
                case '\r' -> builder.append("\\r");
                case '\t' -> builder.append("\\t");
                default -> builder.append(character);
            }
        }
        return builder.append('"').toString();
    }
}
