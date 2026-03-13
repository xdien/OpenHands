# Hướng dẫn Link Discord Account với OpenHands

## Tổng quan

Để sử dụng đầy đủ Discord bot integration, người dùng cần link Discord account với OpenHands user account. Quá trình này sử dụng Discord OAuth2 để xác thực.

---

## Cách 1: Link qua Web Interface (Khuyến nghị)

### Bước 1: Truy cập Discord Install URL

Mở browser và truy cập:

```
https://canthotouring.com/discord/install
```

URL này sẽ redirect đến Discord OAuth2 authorization page.

### Bước 2: Authorize trên Discord

1. Discord sẽ hiển thị trang authorization:
   ```
   OpenHands wants to:
   - Identify your Discord account
   - View your username and discriminator
   ```

2. Click **Authorize** để đồng ý

### Bước 3: Đăng nhập OpenHands (nếu chưa đăng nhập)

Sau khi authorize Discord, bạn sẽ được redirect về:
```
https://canthotouring.com/discord/install-callback?code=YOUR_CODE
```

Lúc này:
- Nếu đã đăng nhập OpenHands: Account sẽ được tự động link
- Nếu chưa đăng nhập: Cần đăng nhập OpenHands trước

### Bước 4: Kiểm tra kết quả

Sau khi hoàn tất, bạn sẽ thấy thông báo:
```json
{"success": true, "message": "Discord account linked!"}
```

---

## Cách 2: Link qua Bot Mention (Tự động)

### Bước 1: Mention Bot trong Discord

Trong Discord channel có bot, nhắn:
```
@OpenHandsBot hello
```

### Bước 2: Click Link Đăng Nhập

Bot sẽ trả lời:
```
🔐 Please link your Discord account to OpenHands: [Click here to Login](https://canthotouring.com/discord/login?state=...)
```

Click vào link **Click here to Login**.

### Bước 3: Đăng nhập OpenHands

1. Nhập email và password OpenHands của bạn
2. Sau khi đăng nhập thành công, Discord account sẽ được tự động link

### Bước 4: Test lại

Quay lại Discord và mention bot lần nữa:
```
@OpenHandsBot help me create a Python script
```

Bot sẽ nhận diện bạn và tạo conversation.

---

## Cách 3: Link thủ công qua Database (Admin)

Dành cho admin muốn link tài khoản thủ công.

### Bước 1: Lấy Discord User ID

1. Bật Developer Mode trong Discord:
   - User Settings → Advanced → Developer Mode: ON

2. Right-click vào user trong Discord → Copy ID

### Bước 2: Lấy OpenHands User ID

```bash
docker exec ak_prod_postgres psql -U postgres -d openhands -c "SELECT id, email FROM users LIMIT 10;"
```

### Bước 3: Insert vào Discord Users Table

```sql
INSERT INTO discord_users (
    user_id,
    discord_user_id,
    discord_username,
    keycloak_user_id,
    created_at,
    updated_at
) VALUES (
    'openhands-user-uuid-here',
    'discord-snowflake-id-here',
    'discord-username',
    'keycloak-user-id',
    NOW(),
    NOW()
);
```

---

## Kiểm tra Account đã Link

### Query Database

```bash
docker exec ak_prod_postgres psql -U postgres -d openhands -c "
SELECT
    du.discord_username,
    du.discord_user_id,
    u.email,
    du.created_at
FROM discord_users du
JOIN users u ON du.user_id = u.id
ORDER BY du.created_at DESC
LIMIT 10;
"
```

### Kết quả mẫu:

```
 discord_username | discord_user_id   | email              | created_at
------------------+-------------------+--------------------+---------------------
 xdienw           | 123456789012345678| xdien@example.com  | 2026-03-11 12:00:00
```

---

## Troubleshooting

### Lỗi: "Discord integration not configured"

**Nguyên nhân:** Thiếu `DISCORD_CLIENT_ID`

**Giải pháp:**
```bash
export DISCORD_CLIENT_ID=1480907233733640274
```

### Lỗi: "Invalid redirect_uri"

**Nguyên nhân:** Redirect URI chưa được cấu hình trong Discord Developer Portal

**Giải pháp:**
1. Vào Discord Developer Portal → Your App → OAuth2
2. Thêm redirect URI: `https://canthotouring.com/discord/install-callback`
3. Save changes

### Lỗi: "User already has a linked Discord account"

**Nguyên nhân:** Discord đã được link với user khác

**Giải pháp:**
```sql
-- Unlink Discord account
DELETE FROM discord_users WHERE discord_user_id = 'DISCORD_ID';

-- Sau đó link lại
```

---

## OAuth2 Configuration trong Discord Developer Portal

### Required Settings:

1. **Redirect URIs:**
   ```
   https://canthotouring.com/discord/install-callback
   ```

2. **Scopes:**
   - ✅ `identify` - Để lấy thông tin user cơ bản

3. **Bot Permissions:**
   - ✅ Send Messages
   - ✅ Read Message History
   - ✅ Use Slash Commands

### Lấy Client ID và Secret:

1. Vào [Discord Developer Portal](https://discord.com/developers/applications)
2. Chọn application
3. **General Information:**
   - Client ID
   - Client Secret (click "Reset Secret" nếu cần)

---

## Flow Chi tiết

```
┌─────────────┐
│ User click  │
│ "Link Discord"│
└──────┬──────┘
       │
       ▼
┌─────────────────────────────────┐
│ canthotouring.com/discord/install│
│ → Redirect to Discord OAuth2    │
└──────┬──────────────────────────┘
       │
       ▼
┌─────────────────────────────────┐
│ Discord Authorization Page      │
│ User clicks "Authorize"         │
└──────┬──────────────────────────┘
       │
       ▼
┌─────────────────────────────────┐
│ canthotouring.com/discord/      │
│ install-callback?code=XXX       │
│ Exchange code for access token  │
└──────┬──────────────────────────┘
       │
       ▼
┌─────────────────────────────────┐
│ Get Discord user info           │
│ Store in discord_users table    │
│ Link with current OpenHands user│
└──────┬──────────────────────────┘
       │
       ▼
┌─────────────┐
│ Success!    │
│ Account linked│
└─────────────┘
```

---

## Environment Variables Required

```bash
# Discord OAuth2 Configuration
DISCORD_CLIENT_ID=1480907233733640274
DISCORD_CLIENT_SECRET=your_client_secret_here
DISCORD_BOT_TOKEN=your_bot_token

# Application URL
HOST_URL=https://canthotouring.com
```

---

## Sau khi Link thành công

User có thể:

1. **Mention bot để tạo conversation:**
   ```
   @OpenHandsBot create a Python script
   ```

2. **Theo dõi conversation trên web:**
   - Bot trả về URL conversation
   - Click vào URL để xem chi tiết

3. **Nhận thông báo từ OpenHands:**
   - Khi conversation hoàn thành
   - Khi có lỗi hoặc cần thêm thông tin

---

## Security Notes

1. **Client Secret:** Không bao giờ commit vào git
2. **Bot Token:** Lưu trong environment variable hoặc secrets manager
3. **HTTPS:** Luôn sử dụng HTTPS cho production
4. **State Parameter:** Sử dụng để chống CSRF attacks
