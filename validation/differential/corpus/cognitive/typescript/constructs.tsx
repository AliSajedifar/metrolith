// TypeScript JSX fragment of the cognitive-complexity micro-corpus (G1-A).
//
// Separate from constructs.ts because `.ts` and `.tsx` are different parsers to
// ArchLens, and JSX is legal only in the latter.
//
// This file exists for one divergence: eslint-plugin-sonarjs excludes JSX
// short-circuit expressions from its boolean-sequence rule entirely
// (getJsxShortCircuitNodes in its shipped source). The frozen rule table has no
// such exemption -- an `&&` inside JSX is an `&&` -- so ArchLens counts it.
// React codebases are full of this shape, which is why it is pinned rather than
// left to be discovered on a real subject.

declare const React: { createElement: (...args: unknown[]) => unknown };

export function jsxShortCircuit(show: boolean, label: string) {
  return <div>{show && <span>{label}</span>}</div>;
}

export function jsxConditionalRender(show: boolean, ready: boolean) {
  if (show && ready) {
    return <div>ok</div>;
  }
  return null;
}
