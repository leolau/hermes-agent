"use client";

import { BrowserRouter } from "react-router-dom";
import App from "@/App";
import { SystemActionsProvider } from "@/contexts/SystemActions";
import { I18nProvider } from "@/i18n";
import { exposePluginSDK } from "@/plugins";
import { ThemeProvider } from "@/themes";
import { HERMES_BASE_PATH } from "@/lib/api";

// Expose the plugin SDK before rendering so plugins loaded via <script>
// can access React, components, etc. immediately.
exposePluginSDK();

export default function AppRoot() {
  return (
    <BrowserRouter basename={HERMES_BASE_PATH || undefined}>
      <I18nProvider>
        <ThemeProvider>
          <SystemActionsProvider>
            <App />
          </SystemActionsProvider>
        </ThemeProvider>
      </I18nProvider>
    </BrowserRouter>
  );
}
