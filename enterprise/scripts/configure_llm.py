import sys
import asyncio
from uuid import UUID
from sqlalchemy import select
from storage.database import a_session_maker
from storage.org_member import OrgMember
from server.logger import logger

async def configure(user_id, model, api_key, base_url):
    async with a_session_maker() as session:
        # User ID is the Keycloak UUID we found earlier
        uid = UUID(user_id)
        result = await session.execute(
            select(OrgMember).filter(OrgMember.user_id == uid)
        )
        member = result.scalars().first()
        
        if not member:
            print(f"Error: OrgMember record not found for user_id {user_id}")
            print("Hint: Make sure the user has successfully linked their Discord account first.")
            return
        
        member.llm_model = model
        member.llm_api_key = api_key
        member.llm_base_url = base_url
        
        await session.commit()
        print("-" * 50)
        print("✅ SUCCESS: LLM Configuration Updated!")
        print(f"User ID:  {user_id}")
        print(f"Model:    {model}")
        print(f"Base URL: {base_url}")
        print("-" * 50)
        print("You can now mention the bot in Discord to start a task.")

if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python configure_llm.py <user_id> <model> <api_key> [base_url]")
        print("Example: python configure_llm.py 76baa890... openai/google/gemini-2.0-flash-001 sk-or-v1-...")
        sys.exit(1)
    
    u_id = sys.argv[1]
    u_model = sys.argv[2]
    u_key = sys.argv[3]
    # Default to OpenRouter if not specified
    u_url = sys.argv[4] if len(sys.argv) > 4 else "https://openrouter.ai/api/v1"
    
    asyncio.run(configure(u_id, u_model, u_key, u_url))
