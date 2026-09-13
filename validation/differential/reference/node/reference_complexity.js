// Independent JavaScript / TypeScript complexity reference.
//
// Uses the TypeScript compiler API as the parser for BOTH languages. ArchLens
// uses tree-sitter-javascript and tree-sitter-typescript, so the two share no
// grammar, no parser, no traversal and no rule table. Parsing only: no type
// checker, no program construction, nothing resolved or synthesized.
//
// Implemented from docs/COMPLEXITY_CONTRACT_V1.md and the hand-authored JS/TS
// synthetic expectations. No ArchLens metric output was consulted.
//
// Canonical population (ArchLens's, implemented independently): module-scope
// function declarations, function/arrow/generator expressions held by a stable
// named module-scope binding or an exact CommonJS export target, and direct
// methods or accessors of a counted class or a module-bound object literal.
// Excluded: constructors, nested and anonymous functions, class-property
// arrows, and declaration-only signatures/overloads.
//
// Not ground truth. The TypeScript compiler is a reference front end whose
// disagreements are findings about both sides until adjudicated.

"use strict";

const fs = require("fs");
const path = require("path");
const ts = require(process.env.ARCHLENS_TS_MODULE || "typescript");

// Versioned independently of TypeScript and Node: the adapter's RULES can
// change while the parser stays fixed, and a reader must be able to tell which
// moved.
const ADAPTER_VERSION = "2.2.0";

// -------------------------------------------------------------------------
// clean-room comment masking, so a line mixing code and a trailing comment
// still counts as code.
// -------------------------------------------------------------------------
function maskComments(source) {
  let out = "";
  let i = 0;
  let inLine = false, inBlock = false, inSingle = false, inDouble = false, inTemplate = false;
  while (i < source.length) {
    const c = source[i];
    const n = source[i + 1];
    if (inLine) {
      if (c === "\n") { inLine = false; out += c; } else { out += " "; }
      i++;
    } else if (inBlock) {
      if (c === "*" && n === "/") { inBlock = false; out += "  "; i += 2; }
      else { out += c === "\n" ? "\n" : " "; i++; }
    } else if (inSingle || inDouble || inTemplate) {
      out += c;
      if (c === "\\") { if (i + 1 < source.length) { out += n; i += 2; continue; } }
      if (inSingle && c === "'") inSingle = false;
      else if (inDouble && c === '"') inDouble = false;
      else if (inTemplate && c === "`") inTemplate = false;
      i++;
    } else if (c === "/" && n === "/") { inLine = true; out += "  "; i += 2; }
    else if (c === "/" && n === "*") { inBlock = true; out += "  "; i += 2; }
    else if (c === "'") { inSingle = true; out += c; i++; }
    else if (c === '"') { inDouble = true; out += c; i++; }
    else if (c === "`") { inTemplate = true; out += c; i++; }
    else { out += c; i++; }
  }
  return out;
}

function countCodeLines(raw, masked, start, end) {
  let total = 0;
  for (let line = start - 1; line < end && line < raw.length; line++) {
    if (raw[line].trim() !== "" && masked[line].trim() !== "") total++;
  }
  return total;
}

// -------------------------------------------------------------------------
// boundaries: a nested callable or class ends attribution for its parent
// -------------------------------------------------------------------------
function isBoundary(node) {
  return ts.isFunctionDeclaration(node)
    || ts.isFunctionExpression(node)
    || ts.isArrowFunction(node)
    || ts.isMethodDeclaration(node)
    || ts.isGetAccessorDeclaration(node)
    || ts.isSetAccessorDeclaration(node)
    || ts.isConstructorDeclaration(node)
    || ts.isClassDeclaration(node)
    || ts.isClassExpression(node);
}

const LOGICAL_BINARY = new Set([
  ts.SyntaxKind.AmpersandAmpersandToken,
  ts.SyntaxKind.BarBarToken,
  ts.SyntaxKind.QuestionQuestionToken,
]);
const LOGICAL_ASSIGN = new Set([
  ts.SyntaxKind.AmpersandAmpersandEqualsToken,
  ts.SyntaxKind.BarBarEqualsToken,
  ts.SyntaxKind.QuestionQuestionEqualsToken,
]);

function isLogicalOperator(node) {
  if (!ts.isBinaryExpression(node)) return false;
  const kind = node.operatorToken.kind;
  return LOGICAL_BINARY.has(kind) || LOGICAL_ASSIGN.has(kind);
}

// && || ?? &&= ||= ??= inside one expression, boundaries excluded.
function countBooleans(node) {
  if (!node || isBoundary(node)) return 0;
  let total = isLogicalOperator(node) ? 1 : 0;
  node.forEachChild((child) => { total += countBooleans(child); });
  return total;
}

// -------------------------------------------------------------------------
// bounded descent
// -------------------------------------------------------------------------
class Measure {
  constructor() {
    this.decisions = 0;
    this.booleans = 0;
    this.maxCondition = 0;
    this.maxDepth = 0;
  }

  condition(node) {
    const found = countBooleans(node);
    if (found > this.maxCondition) this.maxCondition = found;
  }

  note(depth) {
    if (depth > this.maxDepth) this.maxDepth = depth;
  }

  // A control construct's body opens exactly one nesting level.
  body(node, depth) {
    if (!node) return;
    this.note(depth + 1);
    walkStatement(this, node, depth + 1);
  }

  walk(node, depth) {
    if (!node || isBoundary(node)) return;

    if (ts.isIfStatement(node)) {
      this.decisions++;
      this.condition(node.expression);
      this.walkExpression(node.expression, depth);
      this.body(node.thenStatement, depth);
      if (node.elseStatement) {
        // `else if` is an IfStatement, not a block: it stays at this depth so
        // the chain measures flat. A real `else` block nests.
        if (ts.isIfStatement(node.elseStatement)) this.walk(node.elseStatement, depth);
        else this.body(node.elseStatement, depth);
      }
      return;
    }
    if (ts.isForStatement(node)) {
      this.decisions++;
      this.condition(node.condition);
      // The header's other two clauses hold operators too: `for (let i = a ?? 0;
      // ...; i += b || 1)`. Only the condition used to be scanned.
      this.walkExpression(node.initializer, depth);
      this.walkExpression(node.condition, depth);
      this.walkExpression(node.incrementor, depth);
      this.body(node.statement, depth);
      return;
    }
    if (ts.isForOfStatement(node) || ts.isForInStatement(node)) {
      this.decisions++;
      // The ITERABLE is an ordinary expression of the enclosing callable, and
      // `for (const x of xs ?? [])` is the idiom that exposed the omission.
      this.walkExpression(node.initializer, depth);
      this.walkExpression(node.expression, depth);
      this.body(node.statement, depth);
      return;
    }
    if (ts.isWhileStatement(node) || ts.isDoStatement(node)) {
      this.decisions++;
      this.condition(node.expression);
      this.walkExpression(node.expression, depth);
      this.body(node.statement, depth);
      return;
    }
    if (ts.isSwitchStatement(node)) {
      // The switch opens one level and each clause opens another, so a
      // statement inside a clause sits at depth+2.
      this.walkExpression(node.expression, depth);
      for (const clause of node.caseBlock.clauses) {
        if (ts.isCaseClause(clause)) {
          this.decisions++;   // `default` adds nothing
          this.condition(clause.expression);
          this.walkExpression(clause.expression, depth);
        }
        for (const statement of clause.statements) {
          this.note(depth + 2);
          walkStatement(this, statement, depth + 2);
        }
      }
      return;
    }
    if (ts.isTryStatement(node)) {
      this.body(node.tryBlock, depth);
      if (node.catchClause) {
        this.decisions++;   // `catch` is a decision; `finally` is not
        this.body(node.catchClause.block, depth);
      }
      if (node.finallyBlock) this.body(node.finallyBlock, depth);
      return;
    }
    if (ts.isBlock(node)) {
      // A bare block is not attached to a control construct and opens no level.
      for (const statement of node.statements) {
        this.note(depth);
        walkStatement(this, statement, depth);
      }
      return;
    }
    if (ts.isLabeledStatement(node)) {
      walkStatement(this, node.statement, depth);
      return;
    }

    // Anything else contributes only through its expressions.
    node.forEachChild((child) => {
      if (isBoundary(child)) return;
      this.walk(child, depth);
      this.walkExpressionOnly(child, depth);
    });
  }

  // Counts operators and conditional expressions, never nesting.
  walkExpression(node, depth) {
    if (!node || isBoundary(node)) return;
    if (isLogicalOperator(node)) this.booleans++;
    if (ts.isConditionalExpression(node)) {
      this.decisions++;
      this.condition(node.condition);
    }
    node.forEachChild((child) => this.walkExpression(child, depth));
  }

  // Used from the generic branch, where walk() already descended: only count
  // the node itself so operators are not double counted.
  walkExpressionOnly(node, depth) {
    if (!node || isBoundary(node)) return;
    if (ts.isExpressionStatement(node) || ts.isVariableStatement(node)
        || ts.isReturnStatement(node) || ts.isThrowStatement(node)) {
      return;   // handled by walk()'s recursion
    }
  }
}

// A statement-level walk that also counts expressions exactly once.
function measureBody(body) {
  const measure = new Measure();
  if (!body) return measure;
  if (ts.isBlock(body)) {
    for (const statement of body.statements) {
      measure.note(0);
      walkStatement(measure, statement, 0);
    }
  } else {
    // A concise arrow body IS the expression.
    measure.walkExpression(body, 0);
  }
  return measure;
}

// walk() counts operators only inside the CONDITIONS it visits, so a statement's
// own expressions need a second pass. That pass used to run on TOP-LEVEL
// statements only, so a `??` or a ternary in a `return` inside an `if` or a
// `case` counted nowhere -- 22 of 24 boolean operators lost on one Layer-C2
// callable. Every descent into a STATEMENT now goes through here, so the two
// passes stay together wherever the statement sits.
function walkStatement(measure, statement, depth) {
  measure.walk(statement, depth);
  collectExpressions(statement).forEach((expression) => {
    measure.walkExpression(expression, depth);
  });
}

function measureStatement(measure, statement, depth) {
  walkStatement(measure, statement, depth);
}

// Expressions a statement carries that are not a control-flow condition.
function collectExpressions(statement) {
  const found = [];
  if (ts.isExpressionStatement(statement)) found.push(statement.expression);
  else if (ts.isReturnStatement(statement) && statement.expression) found.push(statement.expression);
  else if (ts.isThrowStatement(statement) && statement.expression) found.push(statement.expression);
  else if (ts.isVariableStatement(statement)) {
    for (const declaration of statement.declarationList.declarations) {
      if (declaration.initializer) found.push(declaration.initializer);
    }
  }
  return found;
}

// -------------------------------------------------------------------------
// population
// -------------------------------------------------------------------------
function isModuleScope(node) {
  let parent = node.parent;
  while (parent) {
    if (ts.isSourceFile(parent)) return true;
    if (ts.isModuleBlock(parent) || ts.isModuleDeclaration(parent) || ts.isBlock(parent)) {
      parent = parent.parent;
      continue;
    }
    if (isBoundary(parent) || ts.isObjectLiteralExpression(parent)) return false;
    parent = parent.parent;
  }
  return false;
}

function hasCallableAncestor(node) {
  let parent = node.parent;
  while (parent) {
    if (isBoundary(parent) && !ts.isClassDeclaration(parent) && !ts.isClassExpression(parent)) {
      return true;
    }
    parent = parent.parent;
  }
  return false;
}

function stableBindingName(node) {
  const parent = node.parent;
  if (parent && ts.isVariableDeclaration(parent) && parent.initializer === node
      && ts.isIdentifier(parent.name) && isModuleScope(parent)) {
    return parent.name.text;
  }
  if (parent && ts.isBinaryExpression(parent)
      && parent.operatorToken.kind === ts.SyntaxKind.EqualsToken
      && parent.right === node) {
    const target = parent.left.getText();
    if (/^module\.exports\.[A-Za-z_$][\w$]*$/.test(target)) return target.split(".").pop();
    if (/^exports\.[A-Za-z_$][\w$]*$/.test(target)) return target.split(".").pop();
    if (target === "module.exports") return "default";
  }
  return null;
}

function classIsCounted(node) {
  if (ts.isClassDeclaration(node)) return Boolean(node.name);
  if (ts.isClassExpression(node)) return stableBindingName(node) !== null;
  return false;
}

function objectLiteralBindingName(node) {
  const parent = node.parent;
  if (parent && ts.isVariableDeclaration(parent) && parent.initializer === node
      && ts.isIdentifier(parent.name) && isModuleScope(parent)) {
    return parent.name.text;
  }
  return null;
}

function memberName(member) {
  if (!member.name) return null;
  if (ts.isIdentifier(member.name) || ts.isStringLiteral(member.name)) return member.name.text;
  if (ts.isPrivateIdentifier(member.name)) return member.name.text;
  return member.name.getText();
}

// -------------------------------------------------------------------------
// span: extend back over decorators (contract section 8.1)
// -------------------------------------------------------------------------
function spanStartLine(node, sourceFile) {
  let position = node.getStart(sourceFile, /* includeJsDocComment */ false);
  const modifiers = ts.canHaveDecorators && ts.canHaveDecorators(node)
    ? ts.getDecorators(node)
    : undefined;
  if (modifiers && modifiers.length > 0) {
    position = Math.min(position, ...modifiers.map((d) => d.getStart(sourceFile)));
  }
  return sourceFile.getLineAndCharacterOfPosition(position).line + 1;
}

function parameterInfo(node, sourceFile) {
  let count = 0;
  let declaresThis = null;
  for (const parameter of node.parameters || []) {
    if (ts.isIdentifier(parameter.name) && parameter.name.text === "this") {
      declaresThis = true;   // a type-checking device, not an argument
      continue;
    }
    count++;   // a destructuring pattern is ONE; a rest parameter is ONE
  }
  return { count, declaresThis };
}

function analyze(file) {
  const source = fs.readFileSync(file, "utf8");
  const extension = path.extname(file).toLowerCase();
  const scriptKind = extension === ".ts" ? ts.ScriptKind.TS
    : extension === ".tsx" ? ts.ScriptKind.TSX
    : extension === ".jsx" ? ts.ScriptKind.JSX : ts.ScriptKind.JS;
  const sourceFile = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, scriptKind);

  const raw = source.split("\n");
  const masked = maskComments(source).split("\n");
  const records = [];

  function emit(node, name, qualifiedName, kind) {
    const body = node.body;
    if (!body) return;   // a declaration-only signature or overload
    const measure = measureBody(body);
    const startLine = spanStartLine(node, sourceFile);
    const endLine = sourceFile.getLineAndCharacterOfPosition(node.getEnd()).line + 1;
    const parameters = parameterInfo(node, sourceFile);
    records.push({
      name,
      qualified_name: qualifiedName,
      callable_kind: kind,
      start_line: startLine,
      end_line: endLine,
      nloc: countCodeLines(raw, masked, startLine, endLine),
      formal_parameter_count: parameters.count,
      declares_typescript_this_parameter: parameters.declaresThis,
      decision_point_count: measure.decisions,
      boolean_operator_count: measure.booleans,
      max_condition_operator_count: measure.maxCondition,
      max_nesting_depth: measure.maxDepth,
      cyclomatic_complexity: 1 + measure.decisions + measure.booleans,
      not_evaluable_reason: null,
    });
  }

  function ownerChain(node) {
    const parts = [];
    let parent = node.parent;
    while (parent && !ts.isSourceFile(parent)) {
      if (ts.isClassDeclaration(parent) && parent.name) parts.push(parent.name.text);
      else if (ts.isClassExpression(parent)) {
        const bound = stableBindingName(parent);
        if (bound) parts.push(bound);
      } else if (ts.isObjectLiteralExpression(parent)) {
        const bound = objectLiteralBindingName(parent);
        if (bound) parts.push(bound);
      } else if (ts.isFunctionDeclaration(parent) && parent.name) {
        parts.push(parent.name.text);
      }
      parent = parent.parent;
    }
    return parts.reverse();
  }

  function visit(node) {
    if (ts.isFunctionDeclaration(node) && node.name && node.body) {
      if (!hasCallableAncestor(node) && isModuleScope(node)) {
        emit(node, node.name.text, [...ownerChain(node), node.name.text].join("."),
             "module_function");
      }
    } else if ((ts.isFunctionExpression(node) || ts.isArrowFunction(node)) && node.body) {
      const bound = stableBindingName(node);
      if (bound) {
        emit(node, bound, [...ownerChain(node), bound].join("."), "module_function");
      }
    } else if (ts.isClassDeclaration(node) || ts.isClassExpression(node)) {
      if (classIsCounted(node)) {
        const owner = ts.isClassDeclaration(node) && node.name
          ? node.name.text : stableBindingName(node);
        for (const member of node.members) {
          if (ts.isConstructorDeclaration(member)) continue;   // secondary
          if (!member.body) continue;                          // signature only
          if (ts.isMethodDeclaration(member) || ts.isGetAccessorDeclaration(member)
              || ts.isSetAccessorDeclaration(member)) {
            const name = memberName(member);
            if (!name) continue;
            emit(member, name, [...ownerChain(node), owner, name].join("."),
                 "class_method");
          }
          // A PropertyDeclaration holding an arrow is a class-property arrow:
          // anonymous_functions in ArchLens, outside the population.
        }
      }
    } else if (ts.isObjectLiteralExpression(node)) {
      const owner = objectLiteralBindingName(node);
      if (owner) {
        for (const member of node.properties) {
          if ((ts.isMethodDeclaration(member) || ts.isGetAccessorDeclaration(member)
               || ts.isSetAccessorDeclaration(member)) && member.body) {
            const name = memberName(member);
            if (name) {
              emit(member, name, [...ownerChain(node), owner, name].join("."),
                   "class_method");
            }
          }
        }
      }
    }
    ts.forEachChild(node, visit);
  }

  visit(sourceFile);
  return {
    path: file,
    adapter_version: ADAPTER_VERSION,
    typescript_version: ts.version,
    callables: records,
  };
}

// Resolve inputs. `--list FILE` reads one UTF-8 path per line, removing any
// dependence on the command-line length limit.
function inputPaths(argv) {
  if (argv.length === 2 && argv[0] === "--list") {
    return fs.readFileSync(argv[1], "utf8")
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter((line) => line !== "");
  }
  return argv;
}

function main() {
  const files = inputPaths(process.argv.slice(2));
  const results = files.map(analyze);
  process.stdout.write(JSON.stringify(
    { adapter_version: ADAPTER_VERSION, typescript_version: ts.version, files: results },
    null, 2,
  ));
}

main();
