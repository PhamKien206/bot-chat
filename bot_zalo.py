import json
import os
import zipfile
import time
from datetime import datetime
from playwright.sync_api import sync_playwright

def get_smart_schedule():
    with open('lich_hoc.json', 'r', encoding='utf-8') as f:
        all_periods = json.load(f)
    
    vn_tz = timezone(timedelta(hours=7))
    now = datetime.now(vn_tz)
    
    today_str = now.strftime("%Y-%m-%d")
    weekday = str(now.weekday() + 2) 
    
    current_schedule = None
    for p in all_periods:
        if p['start'] <= today_str <= p['end']:
            current_schedule = p['schedule']
            break
            
    if not current_schedule:
        return "Nghỉ hè rồi hoặc đang trong kỳ nghỉ lễ, không có lịch đâu! 🎉"

    day_data = current_schedule.get(weekday, [])
    
    if not day_data:
        return f"📅 Hôm nay (Thứ {weekday}): Không có lịch học"

    msg = f"📌 Lịch học Thứ {weekday} ({now.strftime('%d/%m')}):\n\n"
    for item in day_data:
        msg += f"⏰ {item['gio']}: {item['mon']}\n📍 {item['phong']}\n"
        msg += "------------------\n"
    return msg

def send_zalo_msg(message):
    print("Bắt đầu quy trình gửi Zalo...")
    
    # --- ĐOẠN CODE GIẢI NÉN BẮT BUỘC PHẢI CÓ ---
    zip_name = 'zalo_user_data.zip'
    extract_dir = './zalo_user_data'
    
    if os.path.exists(zip_name):
        print(f"Đang giải nén file {zip_name}...")
        with zipfile.ZipFile(zip_name, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)
        print("Đã giải nén xong!")
    else:
        print(f"Cảnh báo: Không tìm thấy file {zip_name}! Quá trình đăng nhập có thể thất bại.")
    # ------------------------------------------

    with sync_playwright() as p:
        # Sử dụng thư mục vừa giải nén
        browser = p.chromium.launch_persistent_context(
            extract_dir, 
            headless=True, # Bắt buộc True khi chạy trên GitHub
            args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'] # Lệnh chống sập trên Linux
        )
        page = browser.new_page()
        page.goto('https://chat.zalo.me/')

        # Đổi lại tên người nhận thành Cloud của tôi (hoặc đổi thành tên nhóm của bạn)
        nguoi_nhan = 'cc' 
        
        try:
            print(f"Đang chờ Zalo Web load để tìm: {nguoi_nhan}...")
            page.wait_for_selector('#contact-search-input', timeout=60000) 
            page.fill('#contact-search-input', nguoi_nhan)
            time.sleep(2)
            page.keyboard.press('Enter')
            
            print(f"Đang gõ tin nhắn...")
            page.wait_for_selector('#richInput', timeout=15000)
            page.fill('#richInput', message)
            time.sleep(2)
            page.keyboard.press('Enter')
            
            print("🎉 Bắn tin nhắn thành công!")
            time.sleep(5) 
            
        except Exception as e:
            print("❌ Lỗi rồi, Zalo Web không phản hồi. Chi tiết:")
            print(e)

        browser.close()

# Bắt đầu chạy
if __name__ == "__main__":
    noidung = get_smart_schedule()
    print("Nội dung sẽ gửi:\n", noidung)
    send_zalo_msg(noidung)
