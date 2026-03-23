import os
import requests
from playwright.sync_api import sync_playwright

def scrape_lich_hoc():
    # 1. LẤY CHÌA KHÓA TỪ GITHUB SECRETS (Tuyệt đối không điền số thật vào đây)
    msv = os.environ.get('MSV')
    password = os.environ.get('PASS_TRUONG')

    if not msv or not password:
        return "❌ Lỗi: Không tìm thấy MSV hoặc PASS_TRUONG trong GitHub Secrets!"

    with sync_playwright() as p:
        # Mở trình duyệt ẩn
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1920, 'height': 1080})
        page = context.new_page()

        try:
            print("🚀 BƯỚC 1: Truy cập trang đăng nhập sinh viên...")
            page.goto('https://sinhvien1.tlu.edu.vn/#/login', timeout=60000)

            print("🔑 BƯỚC 2: Điền tài khoản và mật khẩu...")
            # Dùng dấu # để gọi chính xác ID của ô nhập
            page.fill('#username', msv)
            page.fill('#password', password)
            
            print("🖱️ BƯỚC 3: Bấm nút Đăng nhập...")
            # Bảo Playwright tìm đúng cái nút có chứa chữ "Đăng nhập" rồi bấm vào
            page.click('button:has-text("Đăng nhập")') 
            
            # Đợi web xử lý đăng nhập xong
            page.wait_for_load_state('networkidle')

            print("📅 BƯỚC 4: Chuyển hướng vào trang Lịch học (Profile)...")
            page.goto('https://sinhvien1.tlu.edu.vn/#/student/profile', timeout=60000)

            print("⏳ Đang chờ web tải dữ liệu...")
            # Web trường dùng Angular tải dữ liệu hơi chậm, cho bot nghỉ 5 giây để đợi
            page.wait_for_timeout(5000)

            print("🔍 BƯỚC 5: Đang tìm vùng chứa lịch học...")
            # Dùng :visible để báo bot chỉ lấy khung nào đang ĐƯỢC HIỂN THỊ trên màn hình
            page.wait_for_selector('.portlet-body:visible', timeout=30000)
            
            print("✂️ BƯỚC 6: Đang cào dữ liệu text...")
            # Cào toàn bộ chữ trong khung đó
            lich_raw = page.locator('.portlet-body:visible').first.inner_text()
            
            print("✅ Xong! Đã cào được dữ liệu thành công, chuẩn bị đóng trình duyệt.")
            browser.close()
            
            # Cắt bớt nếu text quá dài (Telegram giới hạn 4096 ký tự/tin nhắn)
            if len(lich_raw) > 3500:
                lich_raw = lich_raw[:3500] + "\n\n...(Dữ liệu quá dài, đã cắt bớt)..."

            return f"📌 LỊCH HỌC MỚI NHẤT TỪ WEB TRƯỜNG:\n\n{lich_raw}"

        except Exception as e:
            print(f"❌ CÓ LỖI XẢY RA: {e}")
            page.screenshot(path="debug_web.png")
            print("📸 Đã chụp ảnh màn hình lỗi lưu vào file debug_web.png")
            browser.close()
            return f"❌ Lỗi lấy lịch từ web trường. Chi tiết lỗi:\n{e}"

def send_telegram_msg(message):
    print("🚀 BẮT ĐẦU GỬI TELEGRAM...")
    bot_token = os.environ.get('TELE_BOT_TOKEN')
    chat_id = os.environ.get('TELE_CHAT_ID')

    if not bot_token or not chat_id:
        print("❌ Lỗi: Chưa cài TELE_BOT_TOKEN hoặc TELE_CHAT_ID trong Secrets!")
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message
    }

    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            print("🎉 TING TING! Đã gửi tin nhắn qua Telegram thành công!")
        else:
            print(f"❌ Lỗi từ Telegram: {response.text}")
    except Exception as e:
        print(f"❌ Lỗi mạng khi kết nối tới Telegram: {e}")

if __name__ == "__main__":
    noidung_lich = scrape_lich_hoc()
    send_telegram_msg(noidung_lich)
