import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { AuthProvider } from "./auth/AuthContext";
import "./auth/session";
import { FeedbackProvider } from "./components/FeedbackProvider";

/** Everything the app needs above the router. Shared by the app and the tests. */
export function AppProviders({ queryClient, children }: { queryClient: QueryClient; children: ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <FeedbackProvider>{children}</FeedbackProvider>
      </AuthProvider>
    </QueryClientProvider>
  );
}
