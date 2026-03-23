import json
from datetime import datetime
import time
from playwright.sync_api import sync_playwright

def get_smart_schedule():
    # Đọc file dữ liệu lịch học
    with open('lich_hoc.json', 'r', encoding='utf-8') as f:
        all_periods = json.load(f)
    
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    weekday = str(now.weekday() + 2) # Python đếm Thứ 2 là 0, nên cộng 2
    
    current_schedule = None
    for p in all_periods:
        if p['start'] <= today_str <= p['end']:
            current_schedule = p['schedule']
            break
            
    if not current_schedule:
        return "Nghỉ hè rồi hoặc đang trong kỳ nghỉ lễ, không có lịch đâu! 🎉"

    day_data = current_schedule.get(weekday, [])
    
    if not day_data:
        return f"📅 Hôm nay (Thứ {weekday}): Không có lịch học. Thoải mái code nhé!"

    msg = f"📌 Lịch học Thứ {weekday} ({now.strftime('%d/%m')}):\n\n"
    for item in day_data:
        msg += f"⏰ {item['gio']}: {item['mon']}\n📍 {item['phong']}\n"
        msg += "------------------\n"
    return msg

def send_zalo_msg(message):
    print("Đang khởi động trình duyệt...")
    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context('./zalo_user_data', headless=True)
        page = browser.new_page()
        page.goto('https://chat.zalo.me/')

        print("Trình duyệt đã mở! Vui lòng quét mã QR đăng nhập (Bot sẽ kiên nhẫn chờ tối đa 1 phút)...")
        
        # Đổi tên người nhận ở đây
        nguoi_nhan = 'cc' 
        
        try:
            # Kỹ thuật mới: Chờ đến khi ô tìm kiếm xuất hiện (tức là đã đăng nhập thành công)
            # Dùng ID #contact-search-input chuẩn xác hơn
            page.wait_for_selector('#contact-search-input', timeout=60000) 
            page.fill('#contact-search-input', nguoi_nhan)
            time.sleep(1)
            page.keyboard.press('Enter')
            
            print(f"Đã tìm thấy {nguoi_nhan}, chuẩn bị gửi tin...")
            time.sleep(2) # Đợi một chút để Zalo load khung chat
            
            # Chờ đến khi khung soạn thảo tin nhắn xuất hiện
            page.wait_for_selector('#richInput', timeout=10000)
            page.fill('#richInput', message)
            time.sleep(1)
            page.keyboard.press('Enter')
            
            print("🎉 Đã chốt đơn! Gửi tin nhắn thành công.")
            time.sleep(5) # Dừng lại 5 giây để bạn ngắm thành quả trước khi tắt
            
        except Exception as e:
            print("❌ Lỗi rồi, Zalo Web không phản hồi như dự kiến. Chi tiết lỗi:")
            print(e)

        browser.close()

# Bắt đầu chạy
noidung = get_smart_schedule()
print("Nội dung sẽ gửi:\n", noidung)
send_zalo_msg(noidung)