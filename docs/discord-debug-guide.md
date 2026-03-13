# Discord Bot Debug Guide

## Vấn đề: Bot không phản hồi khi mention

### Kiểm tra nhanh:

```bash
# 1. Xem log Apache mới nhất
tail -f /var/log/apache2/access.log | grep Discord

# 2. Kiểm tra server đang chạy
ps aux | grep saas_server

# 3. Test health endpoint
curl https://canthotouring.com/discord/health
```

## Các nguyên nhân có thể:

### 1. Discord Intents chưa bật ⚠️ (Phổ biến nhất)

Discord yêu cầu bật **Message Content Intent** để bot đọc được nội dung message.

**Cách kiểm tra và bật:**

1. Vào [Discord Developer Portal](https://discord.com/developers/applications)
2. Chọn application của bạn
3. Vào **Bot** → **Privileged Gateway Intents**
4. Bật các intents sau:
   - ✅ **Message Content Intent** (QUAN TRỌNG)
   - ✅ **Server Members Intent** (nếu cần)
   - ✅ **Presence Intent** (nếu cần)
5. Click **Save Changes**

**Sau khi bật, cần:**
- Kick bot ra khỏi server
- Invite lại bot vào server

### 2. Bot chưa được invite đúng cách

**Kiểm tra bot có trong server không:**

1. Mở Discord server
2. Xem trong member list bên phải
3. Bot nên hiển thị với status Online (xanh lá)

**Nếu bot không có trong server, invite lại:**

```
https://discord.com/oauth2/authorize?client_id=1480907233733640274&permissions=2147483648&scope=bot%20applications.commands
```

### 3. Bot không có permission đọc message

**Kiểm tra permissions:**

1. Vào Server Settings → Roles
2. Tìm role của bot
3. Đảm bảo có các permissions:
   - ✅ View Channels
   - ✅ Send Messages
   - ✅ Read Message History
   - ✅ Mention Everyone

### 4. Webhook URL chưa được cập nhật

**Kiểm tra trong Discord Developer Portal:**

1. Vào **General Information**
2. Kiểm tra **Interactions Endpoint URL**
3. Phải là: `https://canthotouring.com/discord/on-event`

### 5. Signature Verification Failed

Nếu log Apache hiển thị 403 liên tục:

```bash
# Kiểm tra public key khớp không
# Trong Discord Developer Portal → General Information → Public Key
# So sánh với biến môi trường
echo $DISCORD_PUBLIC_KEY
```

## Debug Steps:

### Step 1: Xem log Apache real-time

```bash
tail -f /var/log/apache2/access.log | grep -i discord
```

Sau đó mention bot trong Discord. Nếu thấy request mới xuất hiện, webhook đang hoạt động.

### Step 2: Kiểm tra log SaaS server

Server đang chạy trong terminal background. Để xem log:

```bash
# Tìm process ID
ps aux | grep saas_server

# Log được ghi ra terminal đang chạy process đó
# Hoặc restart server với log file
```

### Step 3: Restart server với logging

```bash
# Dừng server cũ
./scripts/stop-discord-server.sh

# Khởi động lại với log
cd /home/xdien/workspace/OpenHands/enterprise
export DISCORD_BOT_TOKEN=<YOUR_BOT_TOKEN>
export DISCORD_PUBLIC_KEY=<YOUR_PUBLIC_KEY>
export DISCORD_CLIENT_ID=<YOUR_CLIENT_ID>
export DISCORD_WEBHOOKS_ENABLED=true
export POSTHOG_CLIENT_KEY=dummy_key_for_testing

# Chạy với log ra file
nohup poetry run uvicorn saas_server:app --host 0.0.0.0 --port 3000 > /tmp/saas.log 2>&1 &

# Xem log
tail -f /tmp/saas.log
```

### Step 4: Test webhook thủ công

```bash
# Test với signature đúng (Discord gửi signature thật)
# Xem log Apache để biết Discord gửi request như thế nào
tail -100 /var/log/apache2/access.log | grep Discord
```

## Checklist Debug:

- [ ] Bot đã được invite vào server chưa?
- [ ] Bot hiển thị Online trong member list?
- [ ] Message Content Intent đã bật trong Developer Portal?
- [ ] Bot có permission Read Message History?
- [ ] Interactions Endpoint URL đúng?
- [ ] Log Apache có request từ Discord khi mention?
- [ ] DISCORD_PUBLIC_KEY khớp với trong Developer Portal?

## Liên hệ hỗ trợ:

Nếu đã kiểm tra tất cả mà vẫn không hoạt động:
1. Chụp màn hình Bot settings trong Developer Portal
2. Chụp màn hình log Apache khi mention bot
3. Kiểm tra bot có trong server không

## Common Issues:

### Issue: Log Apache không có request mới khi mention

**Nguyên nhân:** Discord không gửi webhook
**Giải pháp:** Kiểm tra Message Content Intent

### Issue: Log Apache có request nhưng trả về 403

**Nguyên nhân:** Signature verification failed
**Giải pháp:** Kiểm tra DISCORD_PUBLIC_KEY

### Issue: Log Apache có request 200 nhưng bot không phản hồi

**Nguyên nhân:** User chưa link account hoặc bot token invalid
**Giải pháp:**
1. Kiểm tra user đã login chưa
2. Kiểm tra DISCORD_BOT_TOKEN valid
