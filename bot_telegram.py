import os
import requests
from playwright.sync_api import sync_playwright

def scrape_lich_hoc():
    msv = os.environ.get('MSV')
    password = os.environ.get('PASS_TRUONG')

    with sync_playwright() as p:
        # Mở trình duyệt ẩn (headless=True)
        browser = p.chromium.launch(headless=True)
        # Thiết lập kích thước màn hình lớn để bảng hiện đầy đủ
        context = browser.new_context(viewport={'width': 1920, 'height': 1080})
        page = context.new_page()

        try:
            print("🚀 Truy cập trang chủ sinh viên...")
            page.goto('https://sinhvien1.tlu.edu.vn/#/login', timeout=60000)

            # Đăng nhập (Thủy Lợi dùng login ẩn hoặc popup, điền mã theo id nếu có)
            # Tạm thời điền theo selector phổ biến của hệ thống này
            print("🔑 Đang đăng nhập...")
            page.fill('input[name="2451271131"]', msv)
            page.fill('input[name="038206016725"]', password)
            page.click('button[type="submit"]') # Hoặc id nút đăng nhập của trường
            
            # Đợi đăng nhập thành công và chuyển hướng
            page.wait_for_load_state('networkidle')

            # Truy cập trực tiếp trang Profile nơi có lịch học (như trong ảnh bạn chụp)
            print("📅 Đang lấy lịch học từ Profile...")
            page.goto('https://sinhvien1.tlu.edu.vn/#/student/profile', timeout=60000)

            # Chờ cái bảng xuất hiện (Dựa trên ảnh: class .table-bordered)
            # Chúng ta sẽ chờ thẻ chứa "Thông tin chi tiết" load xong
            page.wait_for_selector('.table-bordered', timeout=30000)
            
            # Lấy toàn bộ nội dung text của bảng lịch học
            # Bạn nên chọn tab "Bảng" để lấy text sạch hơn, hoặc cào trực tiếp ở đây
            lich_raw = page.locator('.portlet-body').inner_text()
            
            print("✅ Đã cào được dữ liệu!")
            browser.close()
            
            return f"📌 LỊCH HỌC MỚI NHẤT:\n\n{lich_raw[:1000]}..." # Cắt bớt nếu quá dài

        except Exception as e:
            print(f"❌ Lỗi: {e}")
            page.screenshot(path="debug_web.png")
            browser.close()
            return f"❌ Lỗi lấy lịch: {e}"

def send_telegram_msg(message):
    bot_token = os.environ.get('TELE_BOT_TOKEN')
    chat_id = os.environ.get('TELE_CHAT_ID')

    if not bot_token or not chat_id:
        print("❌ Lỗi: Chưa cài TELE_BOT_TOKEN hoặc TELE_CHAT_ID!")
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message
    }

    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            print("🎉 Ting Ting! Đã gửi lịch học qua Telegram thành công!")
        else:
            print(f"❌ Lỗi gửi Telegram: {response.text}")
    except Exception as e:
        print(f"❌ Lỗi kết nối Telegram: {e}")

if __name__ == "__main__":
    noidung_lich = scrape_lich_hoc()
    send_telegram_msg(noidung_lich)
