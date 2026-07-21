"use strict";

/**
 * Babel plugin that injects `data-component="ComponentName"` onto the root
 * DOM element returned by every React component in the dashboard source.
 *
 * Rationale: the repo standard requires every component root to carry a
 * `data-component` attribute so a deployed dashboard is inspectable in browser
 * DevTools without a local dev server. Doing it at build time keeps the source
 * clean and guarantees complete coverage instead of relying on hand-edited
 * attributes across dozens of components.
 *
 * Rules implemented:
 *   - Applies to function/arrow/class components whose name is PascalCase.
 *   - Adds the attribute to the outermost JSX element the component returns.
 *   - If the component returns a fragment, the attribute goes on the first
 *     child element instead (fragments cannot hold attributes).
 *   - Never overwrites an existing `data-component`.
 *   - node_modules are excluded by Next's Babel pipeline, so third-party
 *     components are untouched.
 */

const ATTR = "data-component";

function isPascalCase(name) {
  return typeof name === "string" && /^[A-Z][A-Za-z0-9]*$/.test(name);
}

module.exports = function dataComponentPlugin({ types: t }) {
  function hasAttr(openingElement) {
    return openingElement.attributes.some(
      (attr) =>
        t.isJSXAttribute(attr) &&
        t.isJSXIdentifier(attr.name, { name: ATTR }),
    );
  }

  function addAttr(openingElement, componentName) {
    if (hasAttr(openingElement)) return;
    openingElement.attributes.unshift(
      t.jsxAttribute(t.jsxIdentifier(ATTR), t.stringLiteral(componentName)),
    );
  }

  // Given a JSX expression that a component returns, annotate the root DOM
  // element. Fragments delegate to their first element child.
  function annotateRoot(node, componentName) {
    if (t.isJSXElement(node)) {
      addAttr(node.openingElement, componentName);
      return;
    }
    if (t.isJSXFragment(node)) {
      for (const child of node.children) {
        if (t.isJSXElement(child)) {
          addAttr(child.openingElement, componentName);
          return;
        }
      }
    }
  }

  // Walk a function body's return statements (skipping nested functions) and
  // annotate any JSX roots. Handles early returns / conditional roots too.
  function annotateFunctionBody(path, componentName) {
    if (t.isJSXElement(path.node.body) || t.isJSXFragment(path.node.body)) {
      annotateRoot(path.node.body, componentName);
      return;
    }
    path.traverse({
      Function(inner) {
        inner.skip();
      },
      ReturnStatement(ret) {
        const arg = ret.node.argument;
        if (!arg) return;
        if (t.isJSXElement(arg) || t.isJSXFragment(arg)) {
          annotateRoot(arg, componentName);
        } else if (t.isConditionalExpression(arg)) {
          annotateRoot(arg.consequent, componentName);
          annotateRoot(arg.alternate, componentName);
        } else if (t.isLogicalExpression(arg)) {
          annotateRoot(arg.right, componentName);
        }
      },
    });
  }

  return {
    name: "data-component",
    visitor: {
      // Only annotate first-party dashboard source. Next also runs Babel over
      // some node_modules ESM (e.g. transpilePackages / .mjs interop); walking
      // those large/minified files is pointless and can blow the stack.
      Program(path, state) {
        const filename = (state && state.filename) || "";
        if (filename.includes("node_modules")) path.stop();
      },
      FunctionDeclaration(path, state) {
        if (((state && state.filename) || "").includes("node_modules")) return;
        const name = path.node.id && path.node.id.name;
        if (isPascalCase(name)) annotateFunctionBody(path, name);
      },
      VariableDeclarator(path) {
        const id = path.node.id;
        const init = path.node.init;
        if (
          t.isIdentifier(id) &&
          isPascalCase(id.name) &&
          (t.isArrowFunctionExpression(init) || t.isFunctionExpression(init))
        ) {
          const fnPath = path.get("init");
          annotateFunctionBody(fnPath, id.name);
        }
      },
      ClassDeclaration(path) {
        const name = path.node.id && path.node.id.name;
        if (!isPascalCase(name)) return;
        path.traverse({
          ClassMethod(method) {
            if (
              t.isIdentifier(method.node.key, { name: "render" }) &&
              !method.node.static
            ) {
              annotateFunctionBody(method, name);
            }
          },
        });
      },
    },
  };
};
