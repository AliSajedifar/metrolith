// Independent JavaScript / TypeScript entity reference.
//
// Uses the TypeScript compiler API as the parser for BOTH languages. ArchLens
// uses tree-sitter-javascript and tree-sitter-typescript, so the two share no
// grammar, no parser and no code. Parsing only: no type checker, no program
// construction, so nothing is resolved or synthesized.
//
// Definition implemented -- ArchLens's, independently:
//
//   classes_structs    named class declarations and class expressions held by
//                      a stable module-scope variable/CommonJS assignment.
//                      Interfaces and type aliases are NOT classes (ArchLens
//                      tracks interfaces as a separate component that does not
//                      feed classes_structs).
//
//   methods_functions  named module/namespace-level function declarations,
//                      stable module-bound function/arrow expressions, exact
//                      CommonJS function assignments, and direct methods or
//                      accessors of counted classes/module-bound objects.
//                      Excluded: constructors, bodyless declarations, anonymous
//                      default declarations/callbacks, nested functions, and
//                      members of nested/property-assigned/inline objects.
//
// Excluded populations are reported separately so the size of each definitional
// exclusion stays visible as evidence.
//
// Not ground truth. tsc is a reference front end whose disagreements are
// findings about both sides until adjudicated.

const fs = require("fs");
const path = require("path");
const ADAPTER_VERSION = "1.1.0";

const modulesRoot = process.argv[2];
const listing = process.argv[3];
const ts = require(path.join(modulesRoot, "node_modules", "typescript"));

function scriptKind(file) {
  const lower = file.toLowerCase();
  if (lower.endsWith(".tsx")) return ts.ScriptKind.TSX;
  if (lower.endsWith(".ts")) return ts.ScriptKind.TS;
  if (lower.endsWith(".jsx")) return ts.ScriptKind.JSX;
  return ts.ScriptKind.JS;
}

function analyze(file, text) {
  const source = ts.createSourceFile(
    file, text, ts.ScriptTarget.Latest, /*setParentNodes*/ true, scriptKind(file)
  );

  const counts = {
    types: 0,
    methods: 0,
    interfaces: 0,
    type_aliases: 0,
    enums: 0,
    constructors: 0,
    accessors: 0,
    bodyless_declarations: 0,
    nested_functions: 0,
    arrow_or_expression_functions: 0,
    anonymous_functions: 0,
    anonymous_container_members: 0,
  };

  // A declaration is "nested" when an enclosing function-like node exists
  // between it and the source file. Class bodies do not make a method nested.
  function insideFunction(node) {
    let current = node.parent;
    while (current && current.kind !== ts.SyntaxKind.SourceFile) {
      if (
        ts.isFunctionDeclaration(current) ||
        ts.isFunctionExpression(current) ||
        ts.isArrowFunction(current) ||
        ts.isMethodDeclaration(current) ||
        ts.isConstructorDeclaration(current) ||
        ts.isGetAccessorDeclaration(current) ||
        ts.isSetAccessorDeclaration(current)
      ) {
        return true;
      }
      current = current.parent;
    }
    return false;
  }

  function propertyPath(node) {
    if (ts.isIdentifier(node)) return [node.text];
    if (!ts.isPropertyAccessExpression(node)) return null;
    const prefix = propertyPath(node.expression);
    return prefix ? prefix.concat([node.name.text]) : null;
  }

  function isExactCommonJsTarget(node) {
    const parts = propertyPath(node);
    if (!parts) return false;
    return (
      (parts.length === 2 && parts[0] === "module" && parts[1] === "exports") ||
      (parts.length === 3 && parts[0] === "module" && parts[1] === "exports") ||
      (parts.length === 2 && parts[0] === "exports")
    );
  }

  function isModuleVariableInitializer(node) {
    const parent = node.parent;
    return Boolean(
      parent &&
      ts.isVariableDeclaration(parent) &&
      parent.initializer === node &&
      ts.isIdentifier(parent.name) &&
      !insideFunction(parent)
    );
  }

  // True when the callable is held by a reviewed stable module-scope binding.
  // Exact property paths matter: `module.exportsFoo` and `exports.a.b` are not
  // the CommonJS forms in the metric contract.
  function namedModuleBinding(node) {
    if (isModuleVariableInitializer(node)) return true;
    const parent = node.parent;
    if (!parent) return false;
    if (
      ts.isBinaryExpression(parent) &&
      parent.operatorToken.kind === ts.SyntaxKind.EqualsToken &&
      parent.right === node &&
      isExactCommonJsTarget(parent.left) &&
      !insideFunction(parent)
    ) {
      return true;
    }
    return false;
  }

  function countedClass(node) {
    if (ts.isClassDeclaration(node)) return Boolean(node.name);
    return ts.isClassExpression(node) && namedModuleBinding(node);
  }

  // A member counts only when its immediate owner is a counted class or an
  // object literal bound directly to a module-scope identifier. A property
  // name on an outer object is not enough: `const x = { nested: { m() {} } }`
  // makes `m` indirect, and a property inside an anonymous call argument has
  // no stable owner at all.
  //
  // Two earlier adapter revisions over-counted accessors without checking an
  // owner and then treated any immediate PropertyAssignment as a named owner.
  // Layer-2 adjudication showed that the latter still admitted nested members
  // of anonymous containers. The direct-binding test below is the reviewed
  // boundary and is independent of ArchLens's tree representation.
  function ownerIsNamed(node) {
    const owner = node.parent;
    if (!owner) return false;
    if (ts.isClassDeclaration(owner) || ts.isClassExpression(owner)) {
      return countedClass(owner);
    }
    if (ts.isObjectLiteralExpression(owner)) {
      return isModuleVariableInitializer(owner);
    }
    return false;
  }

  function visit(node) {
    if (ts.isClassDeclaration(node) || ts.isClassExpression(node)) {
      if (countedClass(node)) {
        counts.types += 1;
      }
    } else if (ts.isInterfaceDeclaration(node)) {
      counts.interfaces += 1;
    } else if (ts.isTypeAliasDeclaration(node)) {
      counts.type_aliases += 1;
    } else if (ts.isEnumDeclaration(node)) {
      counts.enums += 1;
    } else if (ts.isConstructorDeclaration(node)) {
      counts.constructors += 1;
    } else if (
      ts.isGetAccessorDeclaration(node) || ts.isSetAccessorDeclaration(node)
    ) {
      // Accessors ARE methods under the mapped definition: they are callable
      // class members with a body, and ArchLens reaches them through the same
      // `method_definition` node as an ordinary method. The first draft of the
      // mapping asserted they were excluded on both sides; the Layer 1
      // TypeScript case disagreed 3-vs-2 and adjudication showed the mapping
      // was wrong, not either implementation.
      counts.accessors += 1;
      if (!node.body) {
        counts.bodyless_declarations += 1;
      } else if (!ownerIsNamed(node)) {
        counts.anonymous_container_members += 1;
      } else {
        counts.methods += 1;
      }
    } else if (ts.isMethodDeclaration(node) || ts.isMethodSignature(node)) {
      if (!node.body) {
        counts.bodyless_declarations += 1;
      } else if (!ownerIsNamed(node)) {
        counts.anonymous_container_members += 1;
      } else {
        counts.methods += 1;
      }
    } else if (ts.isFunctionDeclaration(node)) {
      if (!node.body) {
        counts.bodyless_declarations += 1;
      } else if (!node.name) {
        counts.anonymous_functions += 1;
      } else if (insideFunction(node)) {
        counts.nested_functions += 1;
      } else {
        counts.methods += 1;
      }
    } else if (ts.isArrowFunction(node) || ts.isFunctionExpression(node)) {
      // An arrow function or function expression bound to a NAMED module-scope
      // variable is a function under the mapped definition -- `const f = () =>
      // {}` is the dominant modern style and ArchLens counts it as a module
      // function. Only genuinely anonymous inline callbacks are excluded.
      //
      // The first draft of the mapping excluded arrow functions wholesale. The
      // Layer 2 TypeScript subject disagreed on three files and adjudication
      // showed the mapping was wrong, not either implementation.
      counts.arrow_or_expression_functions += 1;
      if (namedModuleBinding(node)) {
        counts.methods += 1;
      } else if (insideFunction(node)) {
        counts.nested_functions += 1;
      } else {
        counts.anonymous_functions += 1;
      }
    }
    ts.forEachChild(node, visit);
  }

  ts.forEachChild(source, visit);
  return counts;
}

function main() {
  const files = fs.readFileSync(listing, "utf8")
    .split(/\r?\n/).map((line) => line.trim()).filter(Boolean);

  const entries = [];
  const totals = { types: 0, methods: 0, files_parsed: 0, files_failed: 0 };

  for (const file of files) {
    try {
      const text = fs.readFileSync(file, "utf8");
      const counts = analyze(file, text);
      entries.push(Object.assign({ path: file, error: null }, counts));
      totals.types += counts.types;
      totals.methods += counts.methods;
      totals.files_parsed += 1;
    } catch (error) {
      entries.push({
        path: file, types: null, methods: null,
        error: `${error.name}: ${error.message}`,
      });
      totals.files_failed += 1;
    }
  }

  process.stdout.write(JSON.stringify({
    files: entries,
    totals,
    adapter_version: ADAPTER_VERSION,
    typescript_version: ts.version,
  }));
}

main();
