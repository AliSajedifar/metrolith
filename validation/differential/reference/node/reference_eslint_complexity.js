// ESLint core `complexity` as the EXTERNAL cyclomatic reference for TypeScript.
//
// Lizard is NOT usable for TypeScript: it reports overlapping, wrong line spans
// for `.ts` (recorded in C4_STATUS.md), which a correct reader cannot produce.
// The pre-authorized per-language substitution therefore applies, and only for
// TypeScript.
//
// ESLint is not a primary adapter and not ground truth. It implements its OWN
// cyclomatic definition -- it counts a default parameter value as a decision
// point, which Complexity Contract 1.0.0 section 7.1 does not -- so a
// difference here is a property of two definitions until adjudicated, and is
// never on its own grounds for changing ArchLens.
//
// Three things this driver refuses to do quietly:
//
//   * **Drop a file.** `ignore: false` disables ESLint's default ignore
//     patterns, so a selected file is never silently skipped; and every input
//     path is accounted for in the output, including ones ESLint returned no
//     result for.
//   * **Read a parse failure as zero complexity.** A fatal message makes the
//     file `linted: false` with the reason, not a file with no functions.
//   * **Take paths on the command line.** `--list FILE`, one UTF-8 path per
//     line, because a real subject exceeds the Windows command-line limit.
//
// The rule runs at threshold 0 so it reports EVERY function with its computed
// value rather than only the ones over a limit.

"use strict";

const fs = require("fs");
const path = require("path");

// Versioned independently of ESLint and the parser: this driver's contract can
// change while the tools stay pinned, and a reader must be able to tell which
// moved.
const DRIVER_VERSION = "1.0.0";

// "Function 'name' has a complexity of 7. Maximum allowed is 0."
// "Arrow function has a complexity of 3. Maximum allowed is 0."
const COMPLEXITY_MESSAGE = /has a complexity of (\d+)\./;
const NAMED = /^(?:Function|Method|Async function|Async method|Getter|Setter|Static method|Async arrow function|Arrow function|Generator function|Async generator function)\s+'([^']+)'/;

function inputPaths(argv) {
  if (argv.length === 2 && argv[0] === "--list") {
    return fs.readFileSync(argv[1], "utf8")
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter((line) => line !== "");
  }
  return argv;
}

// A flat config's `files` patterns are matched relative to ESLint's base
// directory, which defaults to the process cwd. Linting an absolute path that
// lies OUTSIDE that base matches no configuration, and ESLint then returns no
// result for it at all -- a selected file silently contributing nothing rather
// than failing. Measured on ESLint 9.15.0: two files outside the cwd produced
// zero results, and the same two under a cwd covering them produced two. The
// base is therefore derived from the inputs themselves.
function commonBase(files) {
  const resolved = files.map((file) => path.resolve(file));
  const roots = new Set(resolved.map((file) => path.parse(file).root));
  if (roots.size > 1) {
    throw new Error(
      `selected files span more than one filesystem root (${[...roots].join(", ")}); ` +
      "a single ESLint base directory cannot cover them, and linting a file " +
      "outside the base would silently return no result",
    );
  }
  const split = resolved.map((file) => path.dirname(file).split(path.sep));
  let shared = split[0];
  for (const parts of split.slice(1)) {
    let index = 0;
    while (index < shared.length && index < parts.length && shared[index] === parts[index]) {
      index += 1;
    }
    shared = shared.slice(0, index);
  }
  return shared.length > 0 ? shared.join(path.sep) + path.sep : [...roots][0];
}

// ESLint reports the path it resolved, which on Windows can differ from the
// path handed in: a temporary directory arrives as an 8.3 short name and comes
// back as the long form. Comparing the two strings directly made a perfectly
// readable file look like one ESLint returned no result for -- a silent drop.
// Both sides are canonicalized through the real path before they are compared.
function canonical(candidate) {
  const resolved = path.resolve(candidate);
  let real = resolved;
  try {
    real = fs.realpathSync.native(resolved);
  } catch (error) {
    real = resolved;
  }
  return process.platform === "win32" ? real.toLowerCase() : real;
}

async function main() {
  const files = inputPaths(process.argv.slice(2));
  const { ESLint, Linter } = require("eslint");
  const tsParser = require("@typescript-eslint/parser");

  const base = commonBase(files);
  const engine = new ESLint({
    // Derived from the inputs, so every selected file lies inside the base and
    // none can fall outside every configuration.
    cwd: base,
    // `true` means: use ONLY the configuration supplied here. No configuration
    // file from the subject repository can change what is measured.
    overrideConfigFile: true,
    overrideConfig: [
      {
        files: ["**/*.ts", "**/*.tsx", "**/*.mts", "**/*.cts"],
        languageOptions: {
          parser: tsParser,
          ecmaVersion: "latest",
          sourceType: "module",
          parserOptions: { ecmaFeatures: { jsx: true } },
        },
        linterOptions: { reportUnusedDisableDirectives: "off" },
        rules: { complexity: ["warn", 0] },
      },
    ],
    // A selected file is never dropped by an ignore pattern this study did not
    // choose.
    ignore: false,
    errorOnUnmatchedPattern: false,
    warnIgnored: false,
  });

  const results = await engine.lintFiles(files);
  const byPath = new Map();
  for (const result of results) {
    byPath.set(canonical(result.filePath), result);
  }

  const emitted = files.map((file) => {
    const result = byPath.get(canonical(file));
    if (result === undefined) {
      return {
        path: file,
        linted: false,
        reason: "ESLint returned no result for this path",
        callables: [],
      };
    }
    const fatal = result.messages.find((message) => message.fatal);
    if (fatal !== undefined) {
      return {
        path: file,
        linted: false,
        reason: `parse error: ${fatal.message} (line ${fatal.line})`,
        callables: [],
      };
    }
    const callables = [];
    for (const message of result.messages) {
      if (message.ruleId !== "complexity") continue;
      const value = COMPLEXITY_MESSAGE.exec(message.message);
      if (value === null) {
        // An unrecognized message shape is reported, never guessed at.
        callables.push({
          qualified_name: null,
          unparsed_message: message.message,
          start_line: message.line,
          end_line: message.endLine === undefined ? null : message.endLine,
          cyclomatic_complexity: null,
        });
        continue;
      }
      const named = NAMED.exec(message.message);
      callables.push({
        qualified_name: named === null ? null : named[1],
        eslint_message: message.message,
        signature_discriminator: null,
        start_line: message.line,
        end_line: message.endLine === undefined ? null : message.endLine,
        cyclomatic_complexity: Number(value[1]),
      });
    }
    return { path: file, linted: true, callables };
  });

  // A result ESLint produced for a path that was never asked for would mean
  // the mapping is wrong; it is reported rather than discarded.
  const requested = new Set(files.map(canonical));
  const unmatched = results
    .map((result) => result.filePath)
    .filter((filePath) => !requested.has(canonical(filePath)));

  process.stdout.write(JSON.stringify({
    driver_version: DRIVER_VERSION,
    eslint_version: ESLint.version || new Linter().version,
    rule: "complexity",
    threshold: 0,
    base_directory: base,
    unmatched_results: unmatched,
    files: emitted,
  }));
}

main().catch((error) => {
  process.stderr.write(`${error && error.stack ? error.stack : error}\n`);
  process.exit(1);
});
