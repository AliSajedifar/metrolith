// G2-A independent callable enumeration for JS/TS.
// ESLint core `complexity` at threshold 0 reports EVERY function, because
// cyclomatic complexity is >= 1 by construction. Identity therefore comes from
// the same parser SonarJS uses, and never from the cognitive metric.
const path = require("path");
const { ESLint } = require("eslint");
const tsParser = require("@typescript-eslint/parser");
const files = process.argv.slice(2);
const base = path.dirname(path.resolve(files[0]));
(async () => {
  const engine = new ESLint({
    cwd: base, overrideConfigFile: true, ignore: false,
    overrideConfig: [{
      files: ["**/*.ts", "**/*.tsx", "**/*.js", "**/*.jsx"],
      languageOptions: { parser: tsParser, ecmaVersion: "latest", sourceType: "module" },
      rules: { complexity: ["warn", 0] },
    }],
  });
  const results = await engine.lintFiles(files);
  for (const r of results) {
    for (const m of r.messages) {
      console.log(JSON.stringify({line: m.line, msg: m.message}));
    }
  }
})().catch(e => { console.error("DRIVER ERROR:", e.message); process.exit(1); });
