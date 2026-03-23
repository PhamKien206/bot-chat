import os
import requests
from playwright.sync_api import sync_playwright

def scrape_lich_hoc_to_image():
    msv = os.environ.get('MSV')
    password = os.environ.get('PASS_TRUONG')

    if not msv or not password:
        print("❌ Lỗi: Không tìm thấy MSV hoặc PASS_TRUONG!")
        return None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Thiết lập kích thước màn hình siêu rộng để bảng lịch học không bị co dúm
        context = browser.new_context(viewport={'width': 1920, 'height': 1080})
        page = context.new_page()

        try:
            print("🚀 BƯỚC 1: Truy cập trang đăng nhập...")
            page.goto('https://sinhvien1.tlu.edu.vn/#/login', timeout=60000)

            print("🔑 BƯỚC 2: Điền tài khoản và mật khẩu...")
            page.fill('#username', msv)
            page.fill('#password', password)
            
            print("🖱️ BƯỚC 3: Bấm nút Đăng nhập...")
            page.click('button:has-text("Đăng nhập")') 
            page.wait_for_load_state('networkidle')

            print("📅 BƯỚC 4: Vào trang Profile...")
            page.goto('https://sinhvien1.tlu.edu.vn/#/student/profile', timeout=60000)

            print("🔍 BƯỚC 5: Chuyển sang tab Bảng và chọn tuần hiện tại...")
            page.wait_for_timeout(5000) # Đợi web load khung ngoài
            page.click('a:has-text("Bảng")')
            
            # --- ĐOẠN CODE MỚI: CHỌN TUẦN HIỆN TẠI ---
            # Web trường thường có nút "Tuần này" hoặc một dropdown menu để chọn tuần.
            # Bạn hãy kiểm tra trên web trường xem có nút nào tên là "Tuần này" không nhé.
            
            # GIẢ SỬ WEB CÓ NÚT "TUẦN NÀY":
            try:
                # Tìm và click vào nút có chữ "Tuần này". 
                # Nếu web trường không có nút này, bạn hãy thay bằng ID hoặc Selector của ô chọn tuần.
                print("⏳ Đang tìm và bấm nút 'Tuần này'...")
                # selector phổ biến: text="Tuần này" hoặc .btn-current-week
                page.click('button:has-text("Tuần này")')
                # Sau khi click, cho web nghỉ 3 giây để vẽ lại bảng mới
                page.wait_for_timeout(3000)
                print("✅ Đã chọn xong tuần hiện tại.")
            except Exception:
                # Nếu không tìm thấy nút, in ra cảnh báo nhưng vẫn tiếp tục để chụp ảnh (có thể bị sai tuần)
                print("⚠️ Cảnh báo: Không tìm thấy nút 'Tuần này'. Bot sẽ chụp tuần mặc định.")
            # ----------------------------------------
            
            # Khóa mục tiêu vào cái bảng lịch học đang hiển thị
            table_locator = page.locator('.table-bordered:visible').first
            page.wait_for_selector('.table-bordered:visible', timeout=15000)
            
            print("📸 BƯỚC 6: Đang chụp ảnh lịch học...")
            # Chụp riêng cái bảng đó và lưu thành file anh_lich_hoc.png
            image_path = "anh_lich_hoc.png"
            table_locator.screenshot(path=image_path)
            
            print("✅ Xong! Đã chụp ảnh thành công.")
            browser.close()
            return image_path

        except Exception as e:
            print(f"❌ LỖI: {e}")
            browser.close()
            return None

def send_telegram_photo(photo_path):
    print("🚀 BẮT ĐẦU GỬI ẢNH QUA TELEGRAM...")
    bot_token = os.environ.get('TELE_BOT_TOKEN')
    chat_id = os.environ.get('TELE_CHAT_ID')

    if not bot_token or not chat_id:
        print("❌ Lỗi thiếu Token hoặc Chat ID.")
        return

    # Dùng API sendPhoto thay vì sendMessage
    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    
    try:
        # Mở file ảnh vừa chụp để gửi
        with open(photo_path, 'rb') as photo:
            payload = {
                "chat_id": chat_id,
                "caption": "📌 Lịch học chuẩn tuần này của sếp đây! Chúc code vui vẻ nhé! 💻"
            }
            files = {
                "photo": photo
            }
            response = requests.post(url, data=payload, files=files)
            
            if response.status_code == 200:
                print("🎉 TING TING! Đã gửi ẢNH lịch học qua Telegram thành công!")
            else:
                print(f"❌ Lỗi từ Telegram: {response.text}")
    except Exception as e:
        print(f"❌ Lỗi gửi ảnh: {e}")

if __name__ == "__main__":
    # Lấy đường dẫn file ảnh vừa chụp
    saved_image_path = scrape_lich_hoc_to_image()
    
    # Nếu chụp thành công thì gửi qua Tele
    if saved_image_path:
        send_telegram_photo(saved_image_path)
    else:
        print("❌ Không có ảnh để gửi do quá trình cào web bị lỗi.")
