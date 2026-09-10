// ESLint for the workstation, and deliberately NOTHING but the hooks rules (`B43`).
//
// Why this exists: `B43` was a `useState` placed after an early `return`, so accepting a
// call or saving a wrap-up changed the number of hooks React saw and unmounted the whole
// workstation into a blank screen. TypeScript compiles that happily, `vite build` ships it,
// and a regex scan written to catch it was proven blind by reverting the fix and watching
// it still report "clean". Only a real parser sees a `return` inside an `if (...) {` block.
//
// Kept to the hooks rules on purpose. A full style ruleset turned on three days before a
// pitch would bury the one rule that has actually caught a crash under hundreds of
// warnings about quotes. Add rules when they catch a bug, not before.
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

export default [
  { ignores: ["dist/**", "node_modules/**"] },
  {
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: { parser: tseslint.parser },
    plugins: { "react-hooks": reactHooks },
    rules: {
      // The one that would have caught `B43`. An error, not a warning: a hook order that
      // depends on state is a crash waiting for the state to change.
      "react-hooks/rules-of-hooks": "error",
      // A warning: missing deps are the `B33` family (stale closures), but the workstation
      // deliberately holds some callbacks in refs to dodge its once-a-second re-render,
      // and each of those would otherwise be flagged.
      "react-hooks/exhaustive-deps": "warn",
    },
  },
];
