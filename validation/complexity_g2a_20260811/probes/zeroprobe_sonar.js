// G2-A zero-suppression probe driver for eslint-plugin-sonarjs S3776.
// Threshold is swept, including negative, to establish whether ANY setting
// makes a genuine zero observable.
const path = require("path");
const { ESLint } = require("eslint");
const tsParser = require("@typescript-eslint/parser");
const sonarjs = require("eslint-plugin-sonarjs");
const files = process.argv.slice(3);
const threshold = Number(process.argv[2]);
const base = path.dirname(path.resolve(files[0]));
(async () => {
  const engine = new ESLint({
    cwd: base, overrideConfigFile: true, ignore: false,
    overrideConfig: [{
      files: ["**/*.ts", "**/*.tsx", "**/*.js", "**/*.jsx"],
      languageOptions: { parser: tsParser, ecmaVersion: "latest", sourceType: "module" },
      plugins: { sonarjs },
      rules: { "sonarjs/cognitive-complexity": ["warn", threshold] },
    }],
  });
  const results = await engine.lintFiles(files);
  let count = 0;
  for (const r of results) {
    for (const m of r.messages) {
      count++;
      console.log(JSON.stringify({rule: m.ruleId, line: m.line, msg: m.message}));
    }
  }
  console.log(`# threshold=${threshold} messages=${count}`);
})().catch(e => { console.error("DRIVER ERROR:", e.message); process.exit(1); });
