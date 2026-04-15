/**
 * Generates a URL to redirect to for OAuth authentication
 * @param identityProvider The identity provider to use (e.g., "github", "gitlab", "bitbucket", "azure_devops")
 * @param requestUrl The URL of the request
 * @param authUrl The custom auth URL from backend config (optional)
 * @returns The URL to redirect to for OAuth
 */
export const generateAuthUrl = (
  identityProvider: string,
  requestUrl: URL,
  authUrl?: string | null,
) => {
  // Use HTTPS protocol unless the host is localhost
  const protocol =
    requestUrl.hostname === "localhost" ? requestUrl.protocol : "https:";

  // Determine callback path based on provider
  const callbackPath =
    identityProvider === "enterprise_sso"
      ? "/oauth/enterprise/callback"
      : "/oauth/keycloak/callback";

  const redirectUri = `${protocol}//${requestUrl.host}${callbackPath}`;

  let finalAuthUrl: string;

  if (authUrl) {
    // Ensure https:// is prepended and remove any accidental duplicate slashes
    finalAuthUrl = `https://${authUrl.replace(/^https?:\/\//, "")}`;
  } else {
    finalAuthUrl = requestUrl.hostname
      .replace(/(^|\.)staging\.all-hands\.dev$/, "$1auth.staging.all-hands.dev")
      .replace(/(^|\.)app\.all-hands\.dev$/, "auth.app.all-hands.dev")
      .replace(/(^|\.)localhost$/, "auth.staging.all-hands.dev");

    // If no replacements matched, prepend "auth." (excluding localhost)
    if (
      finalAuthUrl === requestUrl.hostname &&
      requestUrl.hostname !== "localhost"
    ) {
      finalAuthUrl = `auth.${requestUrl.hostname}`;
    }

    finalAuthUrl = `https://${finalAuthUrl}`;
  }

  const scope = "openid email profile"; // OAuth scope - not user-facing
  const separator = requestUrl.search ? "&" : "?";
  const cleanHref = requestUrl.href.replace(/\/$/, "");
  const state = `${cleanHref}${separator}login_method=${identityProvider}`;

  // Check if using custom OAuth endpoint (not Keycloak)
  // Custom OAuth endpoints typically don't have /realms/ in the path
  const isCustomOAuth = authUrl && !authUrl.includes("/realms/");

  if (isCustomOAuth) {
    // Custom OAuth format: {authUrl}/oauth/authorize
    return `${finalAuthUrl}/oauth/authorize?client_id=allhands&response_type=code&redirect_uri=${encodeURIComponent(redirectUri)}&scope=${encodeURIComponent(scope)}&state=${encodeURIComponent(state)}`;
  }

  // Default Keycloak format
  return `${finalAuthUrl}/realms/allhands/protocol/openid-connect/auth?client_id=allhands&kc_idp_hint=${identityProvider}&response_type=code&redirect_uri=${encodeURIComponent(redirectUri)}&scope=${encodeURIComponent(scope)}&state=${encodeURIComponent(state)}`;
};
