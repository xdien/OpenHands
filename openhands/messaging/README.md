# Messaging Interface & Telegram Bot Integration

This module provides a generic messaging interface that allows OpenHands to communicate with users via external messaging platforms like Telegram, Discord, Slack, etc.

## Features

- **Generic Messaging Interface**: Abstract base class for implementing messaging integrations
- **Telegram Bot Integration**: Official implementation for Telegram
- **Action Confirmations**: Request user approval for dangerous actions via inline keyboards
- **Task Notifications**: Receive task completion updates directly in your messaging app
- **Multi-User Support**: Each user has their own conversation context

## Quick Start

### Prerequisites

1. **Telegram Bot Token**: Create a new bot via [@BotFather](https://t.me/BotFather) on Telegram
   - Send `/newbot` to @BotFather
   - Follow the instructions to get your bot token
   - Save the token (it looks like: `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`)

2. **Your Telegram Chat ID**: Get your chat ID to authorize yourself
   - Start a chat with your new bot
   - Send a message to the bot
   - Visit: `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`
   - Look for `"chat":{"id":123456789,...}` in the response

### Configuration

#### Environment Variables

Set the following environment variables before starting OpenHands:

```bash
# Enable messaging interface
OH_MESSAGING_ENABLED=true
OH_MESSAGING_PROVIDER=telegram

# Telegram configuration
OH_MESSAGING_ALLOWED_USER_IDS=123456789  # Your Telegram chat ID (comma-separated for multiple users)
OH_MESSAGING_PROVIDER_CONFIG='{"bot_token": "123456789:ABCdefGHIjklMNOpqrsTUVwxyz", "poll_interval": 1}'

# Optional: Webhook mode (default is polling)
# OH_MESSAGING_PROVIDER_CONFIG='{"bot_token": "...", "webhook_url": "https://your-domain.com/telegram/webhook"}'
```

#### Configuration via Config File

Alternatively, you can configure messaging in your OpenHands config file:

```toml
# config.toml

[messaging]
enabled = true
provider = "telegram"
allowed_user_ids = ["123456789"]

[messaging.provider_config]
bot_token = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
poll_interval = 1
# webhook_url = "https://your-domain.com/telegram/webhook"  # Optional for webhook mode
# max_message_length = 4096  # Optional
```

### Running OpenHands

Start OpenHands with the messaging interface enabled:

```bash
make run
```

The Telegram bot will start automatically and begin polling for messages.

## Usage

### Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Start the bot and see welcome message |
| `/help` | Show help message with available commands |
| `/status` | Check current task status |
| `/cancel` | Cancel the current task |

### Sending Tasks

Simply send a text message to the bot with your task:

```
Fix the CSS on the homepage
```

The bot will forward your task to OpenHands and execute it.

### Action Confirmations

When OpenHands needs to execute a potentially dangerous action (like running shell commands), it will send you a confirmation request with inline keyboard buttons:

```
⚠️ Action Confirmation Required

Action Type: CmdRunAction
Description: Run shell command

Details:
```
rm -rf /tmp/test
```

⏰ This request will expire in 5 minutes.

[✅ Approve] [❌ Reject]
```

Tap the appropriate button to confirm or reject the action.

### Task Completion

When a task is completed, you'll receive a notification:

```
✅ Task completed successfully!
```

Or in case of errors:

```
❌ Task encountered an error:
RuntimeError: Something went wrong
```

## Architecture

### Module Structure

```
openhands/messaging/
├── __init__.py                 # Module exports
├── base.py                     # BaseMessagingIntegration abstract class
├── config.py                   # MessagingConfig, TelegramConfig models
├── messaging_service.py        # MessagingService orchestrator
├── stores/
│   ├── __init__.py
│   ├── conversation_store.py   # User <-> Conversation mappings
│   └── confirmation_store.py   # Pending confirmations
└── telegram/
    ├── __init__.py
    ├── telegram_integration.py  # Telegram implementation
    └── telegram_callback_processor.py  # Event callback processor
```

### Key Components

1. **BaseMessagingIntegration**: Abstract base class defining the interface for all messaging integrations

2. **MessagingService**: Main service that orchestrates:
   - Integration lifecycle (start/stop)
   - Conversation mappings
   - Confirmation requests
   - Message routing

3. **ConversationStore**: Manages mappings between external user IDs and OpenHands conversation IDs

4. **ConfirmationStore**: Manages pending action confirmations with expiration

5. **TelegramIntegration**: Official Telegram Bot implementation supporting:
   - Polling and webhook modes
   - Inline keyboard buttons for confirmations
   - Command handlers (/start, /help, /status, /cancel)

## Extending with New Providers

To add a new messaging provider (e.g., Discord, Slack):

1. Create a new module under `openhands/messaging/<provider>/`

2. Implement the `BaseMessagingIntegration` interface:

```python
from openhands.messaging.base import BaseMessagingIntegration

class DiscordIntegration(BaseMessagingIntegration):
    @property
    def name(self) -> str:
        return "discord"
    
    @property
    def provider_type(self) -> MessagingProviderType:
        return MessagingProviderType.DISCORD
    
    async def start(self) -> None:
        # Initialize Discord bot
        pass
    
    async def stop(self) -> None:
        # Cleanup Discord bot
        pass
    
    async def send_message(self, external_user_id: str, message: str, **kwargs) -> bool:
        # Send message to Discord user
        pass
    
    async def request_confirmation(self, ...) -> ConfirmationStatus:
        # Request confirmation via Discord
        pass
    
    async def handle_incoming_message(self, external_user_id: str, message_text: str, metadata: dict) -> None:
        # Handle incoming Discord messages
        pass
```

3. Add the provider type to `MessagingProviderType` enum in `config.py`

4. Register the integration in `MessagingService.initialize()`

## Troubleshooting

### Bot doesn't respond

1. Check if messaging is enabled: `OH_MESSAGING_ENABLED=true`
2. Verify your chat ID is in `OH_MESSAGING_ALLOWED_USER_IDS`
3. Check bot token is correct
4. Look for errors in the OpenHands logs

### Confirmation requests not working

1. Ensure inline keyboard buttons are enabled in Telegram
2. Check if the bot has permission to send messages
3. Verify the confirmation timeout is appropriate (default: 5 minutes)

### Webhook mode issues

1. Ensure your server is accessible from the internet
2. Set up HTTPS for the webhook URL
3. Configure the webhook URL correctly: `https://your-domain.com/telegram/webhook`

## Security Considerations

1. **Bot Token**: Keep your bot token secret. Never commit it to version control.

2. **User Authorization**: Only authorized chat IDs can interact with the bot. Configure `allowed_user_ids` carefully.

3. **Action Confirmations**: Always review action details before approving. The bot shows full command content for verification.

4. **Rate Limiting**: Telegram has API rate limits. The default polling interval (1 second) is safe, but don't set it too low.

## Future Enhancements

- [ ] Discord integration
- [ ] Slack integration
- [ ] Matrix protocol support
- [ ] Webhook mode for Telegram
- [ ] Multi-language support for bot messages
- [ ] Advanced notification settings
- [ ] Conversation history in messaging app

## License

Same as OpenHands main project.
