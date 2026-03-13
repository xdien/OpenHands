
import asyncio
import sys
import os

# Add enterprise to path so we can import internal modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from dotenv import load_dotenv
load_dotenv(os.path.abspath(os.path.join(os.path.dirname(__file__), '../.env')))

from server.auth.keycloak_manager import get_keycloak_admin
from server.auth.constants import KEYCLOAK_REALM_NAME

async def reset_password(username, new_password):
    """Resets the password for a specific user in the configured realm."""
    try:
        admin = get_keycloak_admin()
        # admin is initialized with realm='master', but get_keycloak_admin() 
        # calls change_current_realm(KEYCLOAK_REALM_NAME)
        
        users = admin.get_users({"username": username})
        if not users:
            # Try searching by email if username search failed
            users = admin.get_users({"email": username})
            
        if not users:
            print(f"❌ User '{username}' not found in realm {KEYCLOAK_REALM_NAME}")
            return

        user_id = users[0]['id']
        actual_username = users[0]['username']
        admin.set_user_password(user_id, new_password, temporary=False)
        print(f"✅ Password successfully reset for user '{actual_username}' (ID: {user_id})")
        
    except Exception as e:
        print(f"❌ Error resetting password: {e}")

async def main():
    if len(sys.argv) < 3:
        print("Usage: poetry run python enterprise/scripts/keycloak_manage_users.py <username_or_email> <new_password>")
        return
    
    username = sys.argv[1]
    password = sys.argv[2]
    await reset_password(username, password)

if __name__ == "__main__":
    asyncio.run(main())
