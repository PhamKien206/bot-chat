import json
import os
import zipfile
import time
from datetime import datetime, timedelta, timezone
from playwright.sync_api import sync_playwright

def get_smart_schedule():
    # 1. Đọc file dữ liệu lịch học
    with open('lich_hoc.json', 'r', encoding='utf-8') as f:
        all_periods = json.load(f)
    
    # 2. Thiết lập múi giờ Việt Nam (UTC+7) để chạy đúng trên server Quốc tế
    vn_tz = timezone(timedelta(hours=7))
    now = datetime.now(vn_tz)
    
    today_str = now.strftime("%Y-%m-%d")
    weekday = str(now.weekday() + 2) # Chuyển đổi: Thứ 2 là 2, Chủ nhật là 8
    
    current_schedule = None
    for p in all_periods:
        if p['start'] <= today_str <= p['end']:
            current_schedule = p['schedule']
            break
            
    if not current_schedule:
        return "Hiện tại không nằm trong giai đoạn học tập. Nghỉ ngơi thôi! 🎉"

    day_data = current_schedule.get(weekday, [])
    
    if not day_data:
        return f"📅 Hôm nay (Thứ {weekday}, {now.strftime('%d/%m')}): Không có lịch học. Thoải mái nhé!"

    msg = f"📌 Lịch học Thứ {weekday} ({now.strftime('%d/%m')}):\n\n"
    for item in day_data:
        msg += f"⏰ {item['gio']}: {item['mon']}\n📍 {item['phong']}\n"
        msg += "------------------\n"
    return msg

def send_zalo_msg(message):
    print("🚀 Bắt đầu quy trình gửi Zalo...")
    
    # Giải nén dữ liệu phiên đăng nhập
    zip_name = 'zalo_user_data.zip'
    extract_dir = './zalo_user_data'
    
    if os.path.exists(zip_name):
        print(f"📦 Đang giải nén {zip_name}...")
        with zipfile.ZipFile(zip_name, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)
        print("✅ Giải nén xong!")
    else:
        print(f"⚠️ Cảnh báo: Không tìm thấy {zip_name}!")

    with sync_playwright() as p:
        # Khởi động trình duyệt với bộ nhớ cũ
        browser = p.chromium.launch_persistent_context(
            extract_dir, 
            headless=True,
            args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
        )
        page = browser.new_page()
        
        # Tên người nhận (Phải khớp chính xác tên trên Zalo của bạn)
        nguoi_nhan = 'Cloud của tôi' 
        
        try:
            print("🌐 Đang truy cập Zalo Web...")
            page.goto('https://chat.zalo.me/', timeout=60000)

            # Chờ ô tìm kiếm xuất hiện
            print(f"🔍 Đang tìm kiếm: {nguoi_nhan}...")
            page.wait_for_selector('#contact-search-input', timeout=60000) 
            page.fill('#contact-search-input', nguoi_nhan)
            
            # Đợi kết quả tìm kiếm hiện ra (quan trọng)
            time.sleep(5) 
            page.keyboard.press('Enter')
            
            # Đợi khung soạn thảo hiện ra
            print("✍️ Đang soạn tin nhắn...")
            page.wait_for_selector('#richInput', timeout=20000)
            page.fill('#richInput', message)
            
            # Đợi tin nhắn được điền đầy đủ
            time.sleep(3) 
            page.keyboard.press('Enter')
            
            print("✅ Đã chốt đơn! Gửi thành công.")
            time.sleep(5) 
            
        except Exception as e:
            print(f"❌ Thất bại: {e}")
            # Chụp ảnh màn hình để debug
            page.screenshot(path="debug_zalo.png")
            print("📸 Đã chụp ảnh màn hình lỗi (debug_zalo.png)")

        browser.close()

if __name__ == "__main__":
    noidung = get_smart_schedule()
    print("--- Nội dung dự kiến ---\n", noidung)
    send_zalo_msg(noidung)
