import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router";

import { AppRoutes } from "../App";
import { AppProviders } from "../AppProviders";
import { session } from "../auth/session";
import { makeQueryClient } from "../lib/queryClient";

/** Render the real application routes at `path`, optionally already signed in. */
export function renderApp(path: string, { signedIn = true }: { signedIn?: boolean } = {}) {
  session.setToken(signedIn ? "test-token" : null);
  const queryClient = makeQueryClient();
  // Fail fast in tests: no retries, no background refetch surprises.
  queryClient.setDefaultOptions({ queries: { retry: false, staleTime: 0, refetchOnWindowFocus: false } });
  return {
    queryClient,
    ...render(
      <AppProviders queryClient={queryClient}>
        <MemoryRouter initialEntries={[path]}>
          <AppRoutes />
        </MemoryRouter>
      </AppProviders>,
    ),
  };
}
