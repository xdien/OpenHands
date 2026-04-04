import { QueryCache, MutationCache, QueryClient } from "@tanstack/react-query";
import i18next from "i18next";
import { AxiosError } from "axios";
import { I18nKey } from "./i18n/declaration";
import { retrieveAxiosErrorMessage } from "./utils/retrieve-axios-error-message";
import { displayErrorToast } from "./utils/custom-toast-handlers";

const handle401Error = (error: AxiosError, queryClient: QueryClient) => {
  if (error?.response?.status === 401 || error?.status === 401) {
    // Clear the invalid session cookie
    document.cookie = 'keycloak_auth=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;';

    // Invalidate auth queries
    queryClient.invalidateQueries({ queryKey: ["user", "authenticated"] });

    // Clear all cached data
    queryClient.clear();

    // Redirect to login page
    const currentPath = window.location.pathname;
    const currentSearch = window.location.search;

    // Don't redirect if already on login page
    if (!currentPath.includes('/login') && !currentPath.includes('/discord/login')) {
      // Store the current URL to redirect back after login
      const returnUrl = encodeURIComponent(currentPath + currentSearch);
      window.location.href = `/discord/login?return_to=${returnUrl}`;
    }
  }
};

const shownErrors = new Set<string>();
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Don't retry on 4xx errors (client errors)
      retry: (failureCount, error) => {
        // Don't retry at all for 4xx errors
        if (error instanceof AxiosError) {
          const status = error.response?.status || error.status;
          if (status && status >= 400 && status < 500) {
            return false;
          }
        }
        // Retry up to 3 times for other errors (network issues, 5xx)
        return failureCount < 3;
      },
      // Don't refetch on window focus by default
      refetchOnWindowFocus: false,
      // Stale time to prevent unnecessary refetches
      staleTime: 1000 * 30, // 30 seconds
    },
  },
  queryCache: new QueryCache({
    onError: (error, query) => {
      const isAuthQuery =
        query.queryKey[0] === "user" && query.queryKey[1] === "authenticated";
      if (!isAuthQuery) {
        handle401Error(error, queryClient);
      }

      if (!query.meta?.disableToast) {
        const errorMessage = retrieveAxiosErrorMessage(error);

        if (!shownErrors.has(errorMessage || "")) {
          displayErrorToast(errorMessage || i18next.t(I18nKey.ERROR$GENERIC));
          shownErrors.add(errorMessage);

          setTimeout(() => {
            shownErrors.delete(errorMessage);
          }, 3000);
        }
      }
    },
  }),
  mutationCache: new MutationCache({
    onError: (error, _, __, mutation) => {
      handle401Error(error, queryClient);

      if (!mutation?.meta?.disableToast) {
        const message = retrieveAxiosErrorMessage(error);
        displayErrorToast(message || i18next.t(I18nKey.ERROR$GENERIC));
      }
    },
  }),
});
