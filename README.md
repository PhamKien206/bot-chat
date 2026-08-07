# 🤖 Bot Lịch Học TLU Telegram

Bot tự động đăng nhập web trường Thủy Lợi, chụp ảnh **Lịch học**, **Lịch thi** và **Học phí** gửi qua Telegram.

## 🔐 Cài đặt Key (GitHub Secrets)

Vào **Settings** > **Secrets and variables** > **Actions** > Bấm **New repository secret** và thêm 4 key sau:

| Name | Giá trị |
| :--- | :--- |
| `MSV` | Mã sinh viên |
| `PASS_TRUONG` | Mật khẩu web trường |
|`Ở Telegram`| `search`|
| `TELE_BOT_TOKEN` | Token lấy từ `@BotFather` |
| `TELE_CHAT_ID` | Chat ID lấy từ `@userinfobot` |

## 🚀 Khởi chạy
- **Tự động:** Chạy lúc 5:00 sáng Thứ 2 hàng tuần.
- **Thủ công:** Vào tab **Actions** > Chọn workflow > Bấm **Run workflow**.
 - git clone [https://github.com/PhamKien206/bot-chat.git](https://github.com/PhamKien206/bot-chat.git)
 - cd bot-chat
