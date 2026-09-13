// Independent Java entity reference for ArchLens differential validation.
//
// Uses the JDK's own front end (com.sun.source Compiler Tree API) rather than
// tree-sitter, so it shares no parser, grammar or code with ArchLens. Parsing
// stops at the parse phase: no attribution, no symbol resolution, and therefore
// no synthesized default constructors, which keeps the count comparable with a
// purely syntactic counter.
//
// Emits one JSON object per input file on stdout, plus a totals object, so the
// Python driver never has to parse Java output formats.
//
// Not ground truth. javac is a reference implementation whose disagreements are
// findings about both sides until adjudicated.

import com.sun.source.tree.ClassTree;
import com.sun.source.tree.CompilationUnitTree;
import com.sun.source.tree.MethodTree;
import com.sun.source.util.JavacTask;
import com.sun.source.util.TreeScanner;

import javax.tools.JavaCompiler;
import javax.tools.JavaFileObject;
import javax.tools.StandardJavaFileManager;
import javax.tools.ToolProvider;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

public final class ReferenceEntityCount {

    // Counted under ArchLens's documented decomposition, plus the raw javac
    // totals so the definition gap itself stays visible as evidence.
    //
    // ArchLens derives:
    //     classes_structs   = classes + records + structs
    //     methods_functions = module_functions + class_methods + receiver_methods
    //
    // so interfaces, enums and annotation types are NOT classes_structs, and
    // constructors and body-less (interface/abstract) declarations are NOT
    // methods_functions. Implementing that same definition with a different
    // parser is what makes a disagreement evidence about an implementation
    // rather than about wording.
    private static final class Counts {
        int types;              // ArchLens definition: class + record
        int methods;            // ArchLens definition: concrete named-owner methods
        int allTypes;           // every named declared type
        int interfaces;
        int enums;
        int annotationTypes;
        int constructors;
        int abstractOrInterfaceMethods;
        int anonymousClassMethods;
    }

    public static void main(String[] args) throws IOException {
        if (args.length < 1) {
            System.err.println("usage: ReferenceEntityCount <file-list>");
            System.exit(2);
        }
        List<String> files = new ArrayList<>();
        for (String line : Files.readAllLines(Path.of(args[0]), StandardCharsets.UTF_8)) {
            String trimmed = line.trim();
            if (!trimmed.isEmpty()) {
                files.add(trimmed);
            }
        }

        JavaCompiler compiler = ToolProvider.getSystemJavaCompiler();
        if (compiler == null) {
            System.err.println("{\"error\":\"no system Java compiler available\"}");
            System.exit(3);
        }

        StringBuilder out = new StringBuilder();
        out.append("{\"files\":[");
        int totalTypes = 0;
        int totalMethods = 0;
        int parsed = 0;
        int failed = 0;

        for (int index = 0; index < files.size(); index++) {
            String file = files.get(index);
            if (index > 0) {
                out.append(',');
            }
            Counts counts = new Counts();
            String error = null;
            try (StandardJavaFileManager manager =
                         compiler.getStandardFileManager(null, null, StandardCharsets.UTF_8)) {
                Iterable<? extends JavaFileObject> units =
                        manager.getJavaFileObjectsFromStrings(List.of(file));
                // Diagnostics are swallowed deliberately: a file that does not
                // compile can still parse, and a parse is all this needs.
                JavacTask task = (JavacTask) compiler.getTask(
                        null, manager, diagnostic -> { }, null, null, units);
                for (CompilationUnitTree unit : task.parse()) {
                    new TreeScanner<Void, Boolean>() {
                        @Override
                        public Void visitClass(ClassTree node, Boolean insideAnonymous) {
                            // An anonymous class body arrives with an empty
                            // simple name. Neither side counts those as types.
                            boolean anonymous = node.getSimpleName() == null
                                    || node.getSimpleName().length() == 0;
                            if (!anonymous) {
                                counts.allTypes++;
                                switch (node.getKind()) {
                                    case CLASS, RECORD -> counts.types++;
                                    case INTERFACE -> counts.interfaces++;
                                    case ENUM -> counts.enums++;
                                    case ANNOTATION_TYPE -> counts.annotationTypes++;
                                    default -> { }
                                }
                            }
                            return super.visitClass(node, anonymous);
                        }

                        @Override
                        public Void visitMethod(MethodTree node, Boolean insideAnonymous) {
                            boolean constructor = "<init>".contentEquals(node.getName());
                            boolean bodyless = node.getBody() == null;
                            boolean anonymousOwner = Boolean.TRUE.equals(insideAnonymous);

                            if (constructor) {
                                counts.constructors++;
                            } else if (bodyless) {
                                counts.abstractOrInterfaceMethods++;
                            } else if (anonymousOwner) {
                                counts.anonymousClassMethods++;
                            } else {
                                counts.methods++;
                            }
                            return super.visitMethod(node, insideAnonymous);
                        }
                    }.scan(unit, Boolean.FALSE);
                }
                parsed++;
            } catch (Exception exception) {
                failed++;
                error = exception.getClass().getSimpleName() + ": " + exception.getMessage();
            }

            out.append("{\"path\":").append(quote(file))
               .append(",\"types\":").append(counts.types)
               .append(",\"methods\":").append(counts.methods)
               .append(",\"all_types\":").append(counts.allTypes)
               .append(",\"interfaces\":").append(counts.interfaces)
               .append(",\"enums\":").append(counts.enums)
               .append(",\"annotation_types\":").append(counts.annotationTypes)
               .append(",\"constructors\":").append(counts.constructors)
               .append(",\"abstract_or_interface_methods\":")
               .append(counts.abstractOrInterfaceMethods)
               .append(",\"anonymous_class_methods\":").append(counts.anonymousClassMethods)
               .append(",\"error\":").append(error == null ? "null" : quote(error))
               .append('}');
            totalTypes += counts.types;
            totalMethods += counts.methods;
        }

        out.append("],\"totals\":{\"types\":").append(totalTypes)
           .append(",\"methods\":").append(totalMethods)
           .append(",\"files_parsed\":").append(parsed)
           .append(",\"files_failed\":").append(failed)
           .append("}}");
        System.out.println(out);
    }

    private static String quote(String value) {
        StringBuilder builder = new StringBuilder("\"");
        for (int index = 0; index < value.length(); index++) {
            char character = value.charAt(index);
            switch (character) {
                case '"' -> builder.append("\\\"");
                case '\\' -> builder.append("\\\\");
                case '\n' -> builder.append("\\n");
                case '\r' -> builder.append("\\r");
                case '\t' -> builder.append("\\t");
                default -> {
                    if (character < 0x20) {
                        builder.append(String.format("\\u%04x", (int) character));
                    } else {
                        builder.append(character);
                    }
                }
            }
        }
        return builder.append('"').toString();
    }
}
