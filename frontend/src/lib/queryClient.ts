import { QueryClient } from "@tanstack/react-query";

import { ApiError } from "../api/client";

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 15_000,
        // Retrying a 4xx cannot succeed (bad input, not found, not allowed);
        // only transient failures are worth a second try.
        retry: (count, error) => count < 2 && !(error instanceof ApiError && error.status >= 400 && error.status < 500),
        refetchOnWindowFocus: false,
      },
    },
  });
}
