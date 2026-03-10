#!/usr/bin/env python3
"""Script to cleanup Discord conversation mapping from store.

Usage:
    python scripts/cleanup_discord_conversation.py <channel_id> [thread_id]

Example:
    python scripts/cleanup_discord_conversation.py 1054780511689650196
    python scripts/cleanup_discord_conversation.py 1054780511689650196 123456789
"""

import asyncio
import sys

# Add enterprise to path
sys.path.insert(0, 'enterprise')

from storage.discord_conversation_store import DiscordConversationStore


async def cleanup_conversation(channel_id: str, thread_id: str | None = None):
    """Remove Discord conversation mapping from store."""
    store = DiscordConversationStore()

    # Get existing conversation
    existing = await store.get_discord_conversation(channel_id, thread_id)
    if existing:
        print(f"Found conversation: {existing.conversation_id}")
        print(f"Channel: {existing.channel_id}, Thread: {existing.thread_id}")

        # Remove the mapping
        await store.remove_discord_conversation(channel_id, thread_id)
        print(f"Removed conversation mapping for channel {channel_id}, thread {thread_id}")
    else:
        print(f"No conversation found for channel {channel_id}, thread {thread_id}")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    channel_id = sys.argv[1]
    thread_id = sys.argv[2] if len(sys.argv) > 2 else None

    asyncio.run(cleanup_conversation(channel_id, thread_id))
