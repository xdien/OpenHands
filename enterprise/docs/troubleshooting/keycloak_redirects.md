# Troubleshooting: Keycloak Localhost Redirection Issue

## Symptom
After logging into Keycloak via a public domain (e.g., `https://canthotouring.com`), the user is redirected back to `http://localhost:8080` instead of the intended public callback URL.

## Root Cause
Keycloak uses a `frontendUrl` attribute at the Realm level to determine the base URL for generating absolute links (like redirects, OIDC discovery, etc.). If this is not set, or set to `localhost:8080`, Keycloak may default to internal/local addresses even when accessed from the outside.

In this specific case, the `allhands` realm had its `frontendUrl` attribute set to `https://localhost:8080`.

## Solution
The `frontendUrl` must be updated to match the public facing domain. This can be done via the Keycloak Admin Console or the Admin API.

### Fix via API (using python-keycloak)
Run the following logic to update the realm attribute:

```python
from server.auth.keycloak_manager import get_keycloak_admin
from server.auth.constants import KEYCLOAK_REALM_NAME

admin = get_keycloak_admin()
realm_name = KEYCLOAK_REALM_NAME

# Get current realm info
realm = admin.get_realm(realm_name)
attributes = realm.get('attributes', {})

# Update frontendUrl
attributes['frontendUrl'] = 'https://canthotouring.com'

admin.update_realm(realm_name, payload={'attributes': attributes})
```

## Verification
You can verify the change by checking the OIDC configuration endpoint:
`https://<your-domain>/realms/<realm-name>/.well-known/openid-configuration`

Ensure that `authorization_endpoint` and other URLs now use the correct public domain instead of `localhost`.
