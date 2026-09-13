// Accounting for the ESLint-versus-contract cyclomatic difference, per callable.
//
// ESLint core `complexity` is an EXTERNAL reference: it implements its own
// definition, and two of its rules have no counterpart in Complexity Contract
// 1.0.0 section 7.1 --
//
//   * every optional-chaining `?.` is a decision point (the contract gives it
//     explicitly +0);
//   * every default value in a binding pattern is a decision point -- in the
//     parameter list AND in an ordinary destructuring declaration inside the
//     body (the contract has no such rule).
//
// Naming a mechanism is not the same as showing it accounts for the numbers, so
// this counts every such construct per callable and lets the adjudication check
// `eslint == archlens + optional_chains + defaulted_parameters +
// destructuring_defaults` for EVERY disagreeing callable, not for a sample.
//
// It takes the callable spans from ArchLens rather than re-deriving a
// population: duplicating the population rule here would make the accounting a
// second implementation to keep in step, and it is evidence, not a metric.
// Nested callables are skipped exactly as contract section 3 requires, so the
// counts describe the outer callable's own traversal.
//
// Input:  --spans FILE, JSON [{path, start_line, end_line}, ...]
// Output: the same records plus optional_chain_count, defaulted_parameter_count
//         and destructuring_default_count.

"use strict";

const fs = require("fs");
const path = require("path");
const ts = require(process.env.ARCHLENS_TS_MODULE || "typescript");

const BOUNDARY = new Set([
  ts.SyntaxKind.FunctionDeclaration,
  ts.SyntaxKind.FunctionExpression,
  ts.SyntaxKind.ArrowFunction,
  ts.SyntaxKind.MethodDeclaration,
  ts.SyntaxKind.GetAccessor,
  ts.SyntaxKind.SetAccessor,
  ts.SyntaxKind.Constructor,
  ts.SyntaxKind.ClassDeclaration,
  ts.SyntaxKind.ClassExpression,
]);

function scriptKindOf(file) {
  const extension = path.extname(file).toLowerCase();
  if (extension === ".tsx") return ts.ScriptKind.TSX;
  if (extension === ".ts") return ts.ScriptKind.TS;
  if (extension === ".jsx") return ts.ScriptKind.JSX;
  return ts.ScriptKind.JS;
}

// The declaration whose span matches the requested one, and its own body.
function findDeclaration(sourceFile, startLine, endLine) {
  let found = null;
  function visit(node) {
    if (BOUNDARY.has(node.kind)) {
      const start = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile)).line + 1;
      const end = sourceFile.getLineAndCharacterOfPosition(node.getEnd()).line + 1;
      // ArchLens extends the start over decorators, so the reference start may
      // be lower; overlap containment is enough to identify the declaration.
      if (start >= startLine && end <= endLine && (found === null
          || (node.getEnd() - node.getStart(sourceFile))
             > (found.getEnd() - found.getStart(sourceFile)))) {
        found = node;
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(sourceFile);
  return found;
}

function count(declaration, sourceFile) {
  let optionalChains = 0;
  let defaulted = 0;

  // A default is a default wherever it is written. `({ locale = 'pt-BR' })`
  // carries its initializer on a BindingElement, not on the parameter, and
  // React component props make that the common form in this corpus.
  function countDefaults(node) {
    if (node.initializer !== undefined) defaulted += 1;
    if (node.name && (ts.isObjectBindingPattern(node.name)
        || ts.isArrayBindingPattern(node.name))) {
      for (const element of node.name.elements) {
        if (ts.isBindingElement(element)) countDefaults(element);
      }
    }
  }
  for (const parameter of declaration.parameters || []) {
    countDefaults(parameter);
  }

  // A destructuring default is a decision to ESLint wherever it is written,
  // including in an ordinary declaration: `const { theme = 'system' } = useTheme()`.
  let bodyDefaults = 0;

  function visit(node) {
    if (node !== declaration && BOUNDARY.has(node.kind)) return;
    if (node.questionDotToken !== undefined) optionalChains += 1;
    if (ts.isBindingElement(node) && node.initializer !== undefined) bodyDefaults += 1;
    ts.forEachChild(node, visit);
  }
  if (declaration.body) visit(declaration.body);

  return {
    optional_chain_count: optionalChains,
    defaulted_parameter_count: defaulted,
    destructuring_default_count: bodyDefaults,
  };
}

function main() {
  const index = process.argv.indexOf("--spans");
  if (index === -1) throw new Error("--spans FILE is required");
  const spans = JSON.parse(fs.readFileSync(process.argv[index + 1], "utf8"));

  const byFile = new Map();
  const emitted = spans.map((span) => {
    if (!byFile.has(span.path)) {
      const source = fs.readFileSync(span.path, "utf8");
      byFile.set(span.path, ts.createSourceFile(
        span.path, source, ts.ScriptTarget.Latest, true, scriptKindOf(span.path),
      ));
    }
    const sourceFile = byFile.get(span.path);
    const declaration = findDeclaration(sourceFile, span.start_line, span.end_line);
    if (declaration === null) {
      return Object.assign({}, span, { resolved: false });
    }
    return Object.assign({}, span, { resolved: true }, count(declaration, sourceFile));
  });

  process.stdout.write(JSON.stringify({ records: emitted }));
}

main();
