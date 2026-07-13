import { dirname } from "path";
import { fileURLToPath } from "url";
import js from "@eslint/js";
import tseslint from "typescript-eslint";
import { FlatCompat } from "@eslint/eslintrc";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const compat = new FlatCompat({ baseDirectory: __dirname });

export default [
  {
    ignores: [
      "out/**",
      ".next/**",
      "node_modules/**",
      "next-env.d.ts",
      "scripts/**",
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  ...compat.extends("next/core-web-vitals"),
  {
    languageOptions: {
      ecmaVersion: 2023,
    },
    rules: {
      // Stylistic-only; the dashboard uses literal quotes/apostrophes in copy.
      "react/no-unescaped-entities": "off",
      "no-useless-escape": "off",
      // In-app navigation is owned by react-router (client SPA), not next/link;
      // plain <a> anchors are intentional and correct here.
      "@next/next/no-html-link-for-pages": "off",
      // React-compiler advisory rules (eslint-plugin-react-hooks v7). These
      // flag pre-existing, intentional patterns across the ported dashboard
      // (sync setState in effects, ref/immutability heuristics). They are
      // performance/style advisories, not correctness bugs, and are demoted so
      // the parity port keeps behavior identical rather than being rewritten.
      // rules-of-hooks + exhaustive-deps remain enabled.
      "react-hooks/set-state-in-effect": "off",
      "react-hooks/static-components": "off",
      "react-hooks/refs": "off",
      "react-hooks/immutability": "off",
      "react-hooks/purity": "off",
      "react-hooks/preserve-manual-memoization": "off",
      "react-hooks/incompatible-library": "off",
    },
  },
];
