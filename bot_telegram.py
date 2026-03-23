import os
import requests
import re
from datetime import datetime, timedelta, timezone
from playwright.sync_api import sync_playwright

def scrape_lich_hoc_to_image():
    msv = os.environ.get('MSV')
    password = os.environ.get('PASS_TRUONG')

    if not msv or not password:
        print("❌ Lỗi: Không tìm thấy MSV hoặc PASS_TRUONG!")
        return None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
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
            page.wait_for_timeout(5000) 
            page.click('a:has-text("Bảng")')
            page.wait_for_timeout(2000)
            
            # --- BỘ NÃO TÌM TUẦN HIỆN TẠI ---
            print("🧠 Bot đang tính toán ngày tháng để chọn đúng tuần...")
            try:
                # 1. Bấm vào cái ô chọn tuần để nó sổ danh sách ra
                page.click('.ui-select-match')
                page.wait_for_selector('.ui-select-choices-row', timeout=5000)
                
                # 2. Lấy thời gian hiện tại ở Việt Nam
                vn_tz = timezone(timedelta(hours=7))
                today = datetime.now(vn_tz)
                
                # 3. Đọc từng tuần và tìm xem hôm nay nằm ở tuần nào
                rows = page.locator('.ui-select-choices-row').all()
                found_week = False
                
                for row in rows:
                    text = row.inner_text()
                    # Tìm cái đoạn ngày tháng kiểu (1/9/2025 - 7/9/2025)
                    match = re.search(r'\((\d{1,2}/\d{1,2}/\d{4})\s*-\s*(\d{1,2}/\d{1,2}/\d{4})\)', text)
                    if match:
                        start_str, end_str = match.groups()
                        # Chuyển chữ thành dạng thời gian để so sánh
                        start_date = datetime.strptime(start_str, '%d/%m/%Y').replace(tzinfo=vn_tz)
                        end_date = datetime.strptime(end_str, '%d/%m/%Y').replace(tzinfo=vn_tz)
                        end_date = end_date.replace(hour=23, minute=59, second=59) # Căn đến cuối ngày của tuần đó
                        
                        # Nếu hôm nay nằm trong tuần này -> Bấm chọn luôn!
                        if start_date <= today <= end_date:
                            print(f"✅ Đã tìm thấy tuần hiện tại: {text.replace(chr(10), ' ')}")
                            row.click()
                            found_week = True
                            page.wait_for_timeout(3000) # Đợi web load lịch mới
                            break
                
                if not found_week:
                    print("⚠️ Không tìm thấy ngày hôm nay trong danh sách. Có thể đang nghỉ hè/lễ. Chụp tuần mặc định.")
                    page.keyboard.press('Escape') # Ấn Esc để đóng menu thả xuống
            
            except Exception as e:
                print(f"⚠️ Lỗi lúc chọn tuần (nhưng vẫn sẽ chụp): {e}")
            # ---------------------------------
            
            print("📸 BƯỚC 6: Đang chụp ảnh lịch học...")
            table_locator = page.locator('.table-bordered:visible').first
            page.wait_for_selector('.table-bordered:visible', timeout=15000)
            
            image_path = "anh_lich_hoc.png"
            table_locator.screenshot(path=image_path)
            
            print("✅ Xong! Đã chụp ảnh thành công.")
            browser.close()
            return image_path

        except Exception as e:
            print(f"❌ LỖI TRONG QUÁ TRÌNH CÀO WEB: {e}")
            browser.close()
            return None

def send_telegram_photo(photo_path):
    print("🚀 BẮT ĐẦU GỬI ẢNH QUA TELEGRAM...")
    bot_token = os.environ.get('TELE_BOT_TOKEN')
    chat_id = os.environ.get('TELE_CHAT_ID')

    if not bot_token or not chat_id:
        print("❌ Lỗi thiếu Token hoặc Chat ID.")
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    
    try:
        with open(photo_path, 'rb') as photo:
            payload = {
                "chat_id": chat_id,
                "caption": "📌 Lịch học chuẩn đét tuần này của sếp đây! Chúc code vui vẻ nhé! 💻"
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
    saved_image_path = scrape_lich_hoc_to_image()
    if saved_image_path:
        send_telegram_photo(saved_image_path)
    else:
        print("❌ Không có ảnh để gửi do quá trình cào web bị lỗi.")
