// eslint-plugin-sonarjs S3776 driver for Cognitive Complexity differential
// validation, plus the ESLint-core enumerator that establishes identity.
//
// TWO MODES, and they are deliberately in one file so they can never drift onto
// different parsers or different file lists:
//
//   metric      sonarjs/cognitive-complexity at threshold 0 -- reports every
//               callable scoring >= 1. A genuine zero is NOT reported, and no
//               legal threshold makes it so (the rule schema rejects a negative
//               threshold outright).
//   enumerate   ESLint core `complexity` at threshold 0 -- cyclomatic
//               complexity is >= 1 for every function by construction, so this
//               reports EVERY callable the parser saw. It answers one question
//               only: did the parser see a callable here? It is never a second
//               opinion about any value.
//
// Paths arrive through a listing file, never on the command line: a real
// subject exceeds the Windows command-line limit and fails as an opaque
// WinError 206 from inside the process spawn.

const fs = require("fs");
const path = require("path");
const { ESLint } = require("eslint");

const mode = process.argv[2];
const listingPath = process.argv[3];
const cwd = process.argv[4];

if (!["metric", "enumerate"].includes(mode)) {
  console.error(`unknown mode ${mode}; expected metric or enumerate`);
  process.exit(2);
}

const files = fs
  .readFileSync(listingPath, "utf8")
  .split("\n")
  .map((line) => line.trim())
  .filter((line) => line.length > 0);

if (files.length === 0) {
  console.error("empty file listing: refused rather than reported as zero rows");
  process.exit(2);
}

// The cognitive value is only available in the message text. Parsing it is
// unavoidable, so the pattern is anchored and a non-match is an error rather
// than a dropped row -- a silently skipped message would look like a
// suppressed zero, which is the one thing this study must never confuse.
const COGNITIVE = /Cognitive Complexity from (\d+) to the \d+ allowed/;
const CYCLOMATIC = /has a complexity of (\d+)/;

// The rule whose messages ARE the measurement. Everything else ESLint emits is
// not ours to parse.
//
// Real subjects carry inline `eslint-disable` directives naming rules from
// plugins this study does not load, and ESLint reports each one as a problem:
//   "Definition for rule 'react-hooks/exhaustive-deps' was not found."
// Those messages have a null ruleId and no value in them. Discriminating on
// ruleId rather than on message SHAPE keeps the refusal exactly where it
// belongs -- an unparsable message FROM OUR RULE is still a hard error, because
// dropping one would look identical to a suppressed zero -- while a message
// from somewhere else is counted and set aside rather than allowed to abort a
// 146-file subject.
const TARGET_RULE = mode === "metric" ? "sonarjs/cognitive-complexity" : "complexity";

(async () => {
  const overrideConfig = [
    {
      files: ["**/*.ts", "**/*.tsx", "**/*.js", "**/*.jsx", "**/*.mjs", "**/*.cjs"],
      languageOptions: {
        parser: require("@typescript-eslint/parser"),
        ecmaVersion: "latest",
        sourceType: "module",
      },
      ...(mode === "metric"
        ? {
            plugins: { sonarjs: require("eslint-plugin-sonarjs") },
            rules: { "sonarjs/cognitive-complexity": ["warn", 0] },
          }
        : { rules: { complexity: ["warn", 0] } }),
    },
  ];

  const engine = new ESLint({
    cwd,
    overrideConfigFile: true,
    ignore: false,
    overrideConfig,
  });

  const results = await engine.lintFiles(files);
  const rows = [];
  const unreadable = [];
  const ignoredByRule = {};

  for (const result of results) {
    const relative = path.relative(cwd, result.filePath).split(path.sep).join("/");
    if (result.fatalErrorCount > 0) {
      unreadable.push({
        relative_path: relative,
        reason: (result.messages.find((m) => m.fatal) || {}).message || "fatal parse error",
      });
      continue;
    }
    for (const message of result.messages) {
      if (message.fatal) {
        unreadable.push({ relative_path: relative, reason: message.message });
        continue;
      }
      if (message.ruleId !== TARGET_RULE) {
        // Not our rule, so not a measurement. Counted rather than dropped: a
        // sudden change in this tally means the linter configuration moved.
        const key = message.ruleId === null ? "<directive or config problem>" : message.ruleId;
        ignoredByRule[key] = (ignoredByRule[key] || 0) + 1;
        continue;
      }
      const pattern = mode === "metric" ? COGNITIVE : CYCLOMATIC;
      const found = pattern.exec(message.message || "");
      if (!found) {
        // Refuse rather than skip. An unparsed message FROM OUR RULE is an
        // unknown, and an unknown dropped here reappears downstream as an
        // absent row, which the harness would read as a suppressed zero.
        console.error(
          `unrecognized ${mode} message shape at ${relative}:${message.line}: ${message.message}`
        );
        process.exit(3);
      }
      rows.push({
        relative_path: relative,
        start_line: message.line,
        end_line: message.endLine === undefined ? message.line : message.endLine,
        value: Number(found[1]),
      });
    }
  }

  process.stdout.write(
    JSON.stringify({
      mode,
      rule: TARGET_RULE,
      rows,
      unreadable_files: unreadable,
      ignored_messages_by_rule: ignoredByRule,
    })
  );
})().catch((error) => {
  console.error(`driver failure: ${error && error.message ? error.message : error}`);
  process.exit(1);
});
