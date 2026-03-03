# Messaging Interface & Telegram Bot Integration - Implementation Plan

## Overview

This document outlines the implementation plan for adding a Messaging Interface and Telegram Bot Integration to OpenHands, as described in GitHub issue #13113.

## Problem Statement

While setting up and running OpenHands on a remote VPS/Server is relatively easy, accessing the Web UI from outside the server is incredibly difficult and frustrating because OpenHands utilizes multiple dynamic ports during its operation. The proposed solution is to build a Messaging Interface that allows the agent to communicate externally via chat platforms, with Telegram Bot Integration as the first official implementation.

## Implementation Status

### ✅ Phase 1: Foundation

Created the messaging module structure with the following files:

| File | Description |
|------|-------------|
| `openhands/messaging/__init__.py` | Module exports |
| `openhands/messaging/base.py` | BaseMessagingIntegration abstract class defining the interface for all messaging integrations |
| `openhands/messaging/config.py` | MessagingConfig, TelegramConfig, MessagingProviderType models |
| `openhands/messaging/stores/__init__.py` | Store exports |
| `openhands/messaging/stores/conversation_store.py` | UserConversationMapping, ConversationStore, InMemoryConversationStore |
| `openhands/messaging/stores/confirmation_store.py` | PendingConfirmation, ConfirmationStatus, ConfirmationStore, InMemoryConfirmationStore |

### Phase 2: Telegram Integration

Implemented Telegram Bot integration:

| File | Description |
|------|-------------|
| `openhands/messaging/telegram/__init__.py` | Telegram module exports |
| `openhands/messaging/telegram/telegram_integration.py` | TelegramIntegration implementation with inline keyboard buttons, command handlers, callback query handler |
| `openhands/messaging/telegram/telegram_callback_processor.py` | TelegramCallbackProcessor for event callback system |
| `openhands/messaging/messaging_service.py` | MessagingService orchestrator + MessagingServiceInjector |

**Features Implemented:**
- Inline keyboard buttons (✅ Approve / ❌ Reject)
- Command handlers (/start, /help, /status, /cancel)
- Callback query handler for confirmations
- Polling and Webhook modes support

### Phase 3: App Server Integration

Updated App Server configuration:

| File | Changes |
|------|---------|
| `openhands/app_server/config.py` | Added MessagingConfig field, MessagingServiceInjector field, environment variable parsing, helper functions |

**Added Functions:**
- `get_messaging_service()` - Get messaging service instance
- `depends_messaging_service()` - Dependency injection for messaging service

### Phase 5: Documentation

Created comprehensive documentation:

| File | Description |
|------|-------------|
| `openhands/messaging/README.md` | Complete setup guide with quick start, configuration, usage, architecture, troubleshooting |

---

## Remaining Tasks

### ✅ Phase 4: Agent Controller Integration

The Agent Controller has been integrated with the messaging service to enable:
- Sending tasks from Telegram to OpenHands
- Receiving confirmation requests on Telegram
- Receiving task completion notifications

| Task | Description | Status |
|------|-------------|--------|
| Add messaging_service parameter to AgentController | Add optional messaging_service parameter to AgentController constructor | Completed |
| Hook into confirmation flow | When agent requests confirmation, send request to Telegram and wait for response | Completed |
| Hook into task completion flow | When task completes, send result notification to Telegram | Completed |

**Implementation Details:**
- Added `messaging_service` and `external_user_id` parameters to AgentController constructor
- Added `_send_confirmation_request()` method to send confirmation requests via messaging service
- Added `_send_task_completion_notification()` method to send task result notifications
- Hooked into the confirmation flow in `_step()` when action has `AWAITING_CONFIRMATION` state
- Hooked into task completion flow in `_handle_action()` for `AgentFinishAction` and `AgentRejectAction`
- Hooked into error handling in `_react_to_exception()` for ERROR state notifications

### ✅ Phase 5: Testing

| Task | Description | Status |
|------|-------------|--------|
| Write unit tests for messaging classes | Test ConversationStore, ConfirmationStore, MessagingService | Completed |
| Write integration tests for AgentController | Test AgentController messaging integration | Completed |

### 🔲 Phase 6: Future Extensions (Optional)

| Task | Description | Status |
|------|-------------|--------|
| Design Discord integration interface | Create DiscordIntegration class | Pending |
| Design Slack integration interface | Create SlackIntegration class | Pending |
| Add webhook support for Telegram | Complete webhook mode implementation | Pending |
| Add multi-language support for bot messages | i18n for bot messages | Pending |

---

## Configuration Guide

### Environment Variables

```bash
# Enable messaging interface
OH_MESSAGING_ENABLED=true
OH_MESSAGING_PROVIDER=telegram

# Telegram configuration
OH_MESSAGING_ALLOWED_USER_IDS=123456789  # Your Telegram chat ID (comma-separated for multiple users)
OH_MESSAGING_PROVIDER_CONFIG='{"bot_token": "123456789:ABCdefGHIjklMNOpqrsTUVwxyz", "poll_interval": 1}'
```

### Config File (TOML)

```toml
[messaging]
enabled = true
provider = "telegram"
allowed_user_ids = ["123456789"]

[messaging.provider_config]
bot_token = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
poll_interval = 1
# webhook_url = "https://your-domain.com/telegram/webhook"  # Optional for webhook mode
```

---

## Architecture

### Module Structure

```
openhands/messaging/
├── __init__.py                 # Module exports
├── base.py                     # BaseMessagingIntegration abstract class
├── config.py                   # MessagingConfig, TelegramConfig models
├── messaging_service.py        # MessagingService orchestrator
├── README.md                   # User documentation
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

1. **BaseMessagingIntegration** - Abstract base class defining the interface
2. **MessagingService** - Main orchestrator service
3. **ConversationStore** - Manages user <-> conversation mappings
4. **ConfirmationStore** - Manages pending confirmations with expiration
5. **TelegramIntegration** - Official Telegram Bot implementation

---

## Usage

### Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Start the bot and see welcome message |
| `/help` | Show help message with available commands |
| `/status` | Check current task status |
| `/cancel` | Cancel the current task |

### Workflow

1. **Send Task**: User sends a task message to the Telegram bot
2. **Execute**: OpenHands agent executes the task in its workspace
3. **Confirm** (if needed): If action requires confirmation, bot sends inline keyboard
4. **Approve/Reject**: User taps button to approve or reject
5. **Result**: Bot sends task completion result to user

---

## Dependencies

- `python-telegram-bot` - Required for Telegram integration
  ```bash
  pip install python-telegram-bot
  ```

---

## Next Steps

1. **Complete Phase 4**: Integrate messaging with Agent Controller
2. **Add Tests**: Write unit and integration tests
3. **Test End-to-End**: Verify full workflow with real Telegram bot

---

*Last Updated: 2026-03-02*
*Related Issue: #13113*
