# Discord Bot Usage Guide

Sau khi cấu hình **Interactions Endpoint URL** thành công trên Discord Developer Portal, bạn cần thực hiện các bước sau để sử dụng Discord bot với OpenHands.

## Step 1: Invite Bot to Your Discord Server

### Cách 1: Sử dụng OAuth2 URL Generator

1. Vào **Discord Developer Portal** → Chọn application của bạn
2. Vào **OAuth2** → **URL Generator**
3. Chọn scopes:
   - ✅ `bot`
   - ✅ `applications.commands`
4. Chọn Bot Permissions:
   - ✅ Send Messages
   - ✅ Read Message History
   - ✅ Mention Everyone
   - ✅ Use Slash Commands
   - ✅ View Channels
5. Copy generated URL và mở trong browser
6. Chọn server để add bot và click **Authorize**

### Cách 2: Sử dụng link trực tiếp

```
https://discord.com/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=2147483648&scope=bot%20applications.commands
```

Thay `YOUR_CLIENT_ID` bằng Client ID của bạn.

## Step 2: Link Discord Account với OpenHands

Để sử dụng bot, người dùng Discord cần link tài khoản Discord với tài khoản OpenHands:

1. User nhắn tin cho bot hoặc mention bot trong channel
2. Bot sẽ phản hồi với link để đăng nhập:
   ```
   🔐 Please link your Discord account to OpenHands: [Click here to Login](https://canthotouring.com/discord/login?state=...)
   ```
3. User click vào link và đăng nhập vào OpenHands
4. Sau khi đăng nhập thành công, tài khoản Discord được link với OpenHands

## Step 3: Sử dụng Bot

### Mention Bot để bắt đầu conversation

Trong Discord channel có bot, mention bot với request của bạn:

```
@YourBotName help me create a Python script that reads a CSV file
```

Bot sẽ:
1. Xác thực user (đã link account chưa)
2. Tạo conversation mới trên OpenHands
3. Reply với link đến conversation

### Slash Commands

Bot hỗ trợ các slash commands:

| Command | Mô tả |
|---------|-------|
| `/help` | Hiển thị hướng dẫn sử dụng |
| `/status` | Kiểm tra trạng thái kết nối |

### Example Usage

```
User: @OpenHandsBot create a simple REST API using FastAPI
Bot: I'm working on your request! You can follow the conversation here: https://canthotouring.com/conversation/abc123
```

## Step 4: Follow-up Messages

Sau khi tạo conversation, bạn có thể tiếp tục chat với bot:

```
User: @OpenHandsBot add error handling to the API
Bot: [Processes the request and updates the conversation]
```

## Architecture Flow

```
┌─────────────┐     Mention/Command     ┌──────────────────┐
│   Discord   │ ──────────────────────> │  Discord API     │
│   User      │                         │                  │
└─────────────┘                         └────────┬─────────┘
                                                 │ Webhook
                                                 ▼
                                        ┌──────────────────┐
                                        │  Apache Proxy    │
                                        │  /discord/*      │
                                        └────────┬─────────┘
                                                 │
                                                 ▼
                                        ┌──────────────────┐
                                        │  OpenHands SaaS  │
                                        │  Server (port    │
                                        │  3000)           │
                                        └────────┬─────────┘
                                                 │
                    ┌────────────────────────────┼────────────────────────────┐
                    │                            │                            │
                    ▼                            ▼                            ▼
           ┌──────────────┐            ┌──────────────┐            ┌──────────────┐
           │  Discord     │            │  OpenHands   │            │  LLM         │
           │  Manager     │            │  Core        │            │  Provider    │
           └──────────────┘            └──────────────┘            └──────────────┘
```

## Discord Event Types Handling

| Event Type | Code | Description |
|------------|------|-------------|
| PING | 1 | Endpoint verification - returns PONG |
| MESSAGE_CREATE | 0 | Bot mention in message |
| APPLICATION_COMMAND | 2 | Slash command |
| MESSAGE_COMPONENT | 3 | Button click, select menu |

## User Authentication Flow

```
1. User mentions bot
       ↓
2. Bot checks if Discord user is linked to OpenHands
       ↓
   NOT LINKED → Bot returns login link
       ↓
   User clicks link → OAuth flow → Account linked
       ↓
3. User mentions bot again
       ↓
4. Bot creates/updates OpenHands conversation
       ↓
5. Bot returns conversation URL
```

## Database Tables

### DiscordUser Table

```sql
CREATE TABLE discord_users (
    id SERIAL PRIMARY KEY,
    user_id UUID,
    org_id UUID,
    discord_user_id VARCHAR NOT NULL,      -- Discord snowflake ID
    discord_username VARCHAR NOT NULL,
    discord_discriminator VARCHAR,          -- Legacy #1234
    keycloak_user_id VARCHAR,              -- OpenHands user ID
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);
```

## Configuration Checklist

- [x] Discord Application created
- [x] Bot token obtained
- [x] Public key obtained
- [x] Interactions Endpoint URL configured (`https://canthotouring.com/discord/on-event`)
- [x] Bot invited to Discord server
- [x] SaaS server running with Discord environment variables
- [x] Apache proxy configured for `/discord` routes
- [ ] User accounts linked (users need to login via Discord link)

## Testing the Bot

### 1. Check Bot is Online

Trong Discord server, bot nên hiển thị như "Online" trong member list.

### 2. Test Mention

```
@YourBotName hello
```

Expected response (if not linked):
```
🔐 Please link your Discord account to OpenHands: [Click here to Login](https://...)
```

### 3. Test Slash Command

```
/help
```

Expected response:
```
🤖 **OpenHands Discord Bot**

Mention me in a channel to start a conversation!

Commands:
• `/help` - Show this help message
• `/status` - Check your account status
```

## Troubleshooting

### Bot không phản hồi

1. **Check server is running:**
   ```bash
   curl https://canthotouring.com/discord/health
   ```

2. **Check bot has permissions:**
   - Send Messages
   - Read Message History
   - View Channels

3. **Check environment variables:**
   ```bash
   echo $DISCORD_BOT_TOKEN
   echo $DISCORD_PUBLIC_KEY
   echo $DISCORD_CLIENT_ID
   ```

### User nhận được login link liên tục

- **Cause:** User account not properly linked
- **Solution:** Check `discord_users` table in database
  ```sql
  SELECT * FROM discord_users WHERE discord_user_id = 'USER_SNOWFLAKE_ID';
  ```

### Slash commands không hoạt động

- **Cause:** Commands not registered globally
- **Solution:** Register commands via Discord API or wait for propagation (up to 1 hour)

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/discord/on-event` | POST | Main webhook for Discord events |
| `/discord/health` | GET | Health check |
| `/discord/install` | GET | OAuth login redirect |
| `/discord/install-callback` | GET | OAuth callback |
| `/discord/login` | GET | User login page |
| `/discord/send-message` | POST | Internal API to send messages |

## Security Notes

1. **Signature Verification:** All Discord webhooks are verified using Ed25519
2. **User Authentication:** Users must link Discord to OpenHands account
3. **Rate Limiting:** Consider implementing rate limiting for production
4. **Token Security:** Never expose bot token in client-side code
