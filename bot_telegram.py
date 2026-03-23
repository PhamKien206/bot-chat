import json
import os
import requests
from datetime import datetime, timedelta, timezone

def get_smart_schedule():
    # Đọc dữ liệu lịch học
    with open('lich_hoc.json', 'r', encoding='utf-8') as f:
        all_periods = json.load(f)
    
    # Cài múi giờ VN (UTC+7)
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
        return f"📅 Hôm nay (Thứ {weekday}, {now.strftime('%d/%m')}): Không có lịch học. Thoải mái code nhé!"

    msg = f"📌 Lịch học Thứ {weekday} ({now.strftime('%d/%m')}):\n\n"
    for item in day_data:
        msg += f"⏰ {item['gio']}: {item['mon']}\n📍 {item['phong']}\n"
        msg += "------------------\n"
    return msg

def send_telegram_msg(message):
    # Lấy Token và ID từ GitHub Secrets bảo mật
    bot_token = os.environ.get('TELE_BOT_TOKEN')
    chat_id = os.environ.get('TELE_CHAT_ID')

    if not bot_token or not chat_id:
        print("❌ Lỗi: Chưa cài đặt TELE_BOT_TOKEN hoặc TELE_CHAT_ID trong GitHub Secrets!")
        return

    # API chính thức của Telegram, gọi cái là tin nhắn bay đến ngay
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message
    }

    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            print("🎉 Ting Ting! Đã gửi tin nhắn Telegram thành công!")
        else:
            print(f"❌ Lỗi gửi tin: {response.text}")
    except Exception as e:
        print(f"❌ Lỗi kết nối: {e}")

if __name__ == "__main__":
    noidung = get_smart_schedule()
    print("Nội dung sẽ gửi:\n", noidung)
    send_telegram_msg(noidung)
