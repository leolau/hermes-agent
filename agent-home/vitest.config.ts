import { defineConfig } from "vitest/config";
import path from "path";

export default defineConfig({
  // The render test imports the JSX shell component. tsconfig sets
  // `jsx: "preserve"` for Next's own build; override Oxc's JSX handling here so
  // vitest transpiles JSX with the automatic runtime instead of leaving it raw.
  oxc: { jsx: { runtime: "automatic" } },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
      // `server-only` throws when imported outside a React Server environment;
      // stub it so the server-side seam modules are testable under Node.
      "server-only": path.resolve(__dirname, "./test/stubs/server-only.ts"),
    },
  },
  test: {
    // Render tests use react-dom/server (no DOM needed); the Postgres RLS
    // integration test opens a real socket and skips itself when no throwaway
    // PG is available.
    environment: "node",
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
