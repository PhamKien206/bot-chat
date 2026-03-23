import json
import os
import zipfile
import time
from datetime import datetime
from playwright.sync_api import sync_playwright

def get_smart_schedule():
    with open('lich_hoc.json', 'r', encoding='utf-8') as f:
        all_periods = json.load(f)
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    weekday = str(now.weekday() + 2) 
    
    current_schedule = None
    for p in all_periods:
        if p['start'] <= today_str <= p['end']:
            current_schedule = p['schedule']
            break
    if not current_schedule: return "Hôm nay nghỉ ngơi thôi! 🎉"
    day_data = current_schedule.get(weekday, [])
    if not day_data: return f"📅 Thứ {weekday}: Không có lịch học."

    msg = f"📌 Lịch học Thứ {weekday} ({now.strftime('%d/%m')}):\n\n"
    for item in day_data:
        msg += f"⏰ {item['gio']}: {item['mon']}\n📍 {item['phong']}\n------------------\n"
    return msg

def send_zalo_msg(message):
    # Khớp với tên file zalo_user_data.zip trong repo của bạn
    zip_name = 'zalo_user_data.zip'
    if os.path.exists(zip_name):
        print(f"Đang giải nén {zip_name}...")
        with zipfile.ZipFile(zip_name, 'r') as zip_ref:
            zip_ref.extractall('./zalo_user_data')
        print("Đã giải nén xong!")

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            './zalo_user_data', 
            headless=True,
            args=['--no-sandbox', '--disable-setuid-sandbox']
        )
        page = browser.new_page()
        page.goto('https://chat.zalo.me/')
        
        try:
            page.wait_for_selector('#contact-search-input', timeout=60000)
            page.fill('#contact-search-input', 'Cloud của tôi')
            page.keyboard.press('Enter')
            time.sleep(5)
            
            page.wait_for_selector('#richInput', timeout=20000)
            page.fill('#richInput', message)
            page.keyboard.press('Enter')
            time.sleep(5)
            print("✅ Gửi tin nhắn thành công!")
        except Exception as e:
            print(f"❌ Lỗi: {e}")
        
        browser.close()

if __name__ == "__main__":
    content = get_smart_schedule()
    print(f"Nội dung: {content}")
    send_zalo_msg(content)
