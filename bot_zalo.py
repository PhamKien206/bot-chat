import json
import os
import zipfile
import shutil
from datetime import datetime, timedelta, timezone
from playwright.sync_api import sync_playwright

def get_smart_schedule():
    file_path = "lich_hoc.json"

    if not os.path.exists(file_path):
        return "Không tìm thấy file lich_hoc.json"

    with open(file_path, "r", encoding="utf-8") as f:
        all_periods = json.load(f)

    vn_tz = timezone(timedelta(hours=7))
    now = datetime.now(vn_tz)

    today_str = now.strftime("%Y-%m-%d")
    weekday = str(now.weekday() + 2)

    current_schedule = None
    for p in all_periods:
        start = p.get("start")
        end = p.get("end")
        if start and end and start <= today_str <= end:
            current_schedule = p.get("schedule", {})
            break

    if not current_schedule:
        return "Hiện tại không nằm trong giai đoạn học tập. Nghỉ ngơi thôi! 🎉"

    day_data = current_schedule.get(weekday, [])

    if not day_data:
        return f"📅 Hôm nay (Thứ {weekday}, {now.strftime('%d/%m')}): Không có lịch học. Thoải mái nhé!"

    msg = f"📌 Lịch học Thứ {weekday} ({now.strftime('%d/%m')}):\n\n"
    for item in day_data:
        gio = item.get("gio", "Không rõ giờ")
        mon = item.get("mon", "Không rõ môn")
        phong = item.get("phong", "Không rõ phòng")
        msg += f"⏰ {gio}: {mon}\n📍 {phong}\n------------------\n"

    return msg


def send_zalo_msg(message):
    print("🚀 Bắt đầu quy trình gửi Zalo...")

    zip_name = "zalo_user_data.zip"
    extract_dir = "./zalo_user_data"

    if not os.path.exists(zip_name):
        print(f"⚠️ Không tìm thấy {zip_name}")
        return

    if os.path.exists(extract_dir):
        shutil.rmtree(extract_dir)

    print(f"📦 Đang giải nén {zip_name}...")
    with zipfile.ZipFile(zip_name, "r") as zip_ref:
        zip_ref.extractall(extract_dir)
    print("✅ Giải nén xong!")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            extract_dir,
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage"
            ]
        )

        page = context.pages[0] if context.pages else context.new_page()
        nguoi_nhan = "Cloud của tôi"

        try:
            print("🌐 Đang truy cập Zalo Web...")
            page.goto("https://chat.zalo.me/", timeout=60000)

            print(f"🔍 Đang tìm kiếm: {nguoi_nhan}...")
            page.wait_for_selector("#contact-search-input", timeout=60000)
            page.fill("#contact-search-input", nguoi_nhan)
            page.wait_for_timeout(3000)

            page.keyboard.press("Enter")

            print("✍️ Đang soạn tin nhắn...")
            page.wait_for_selector("#richInput", timeout=30000)
            page.click("#richInput")
            page.keyboard.type(message)

            page.wait_for_timeout(1000)
            page.keyboard.press("Enter")

            print("✅ Đã gửi thành công.")
            page.wait_for_timeout(3000)

        except Exception as e:
            print(f"❌ Thất bại: {e}")
            page.screenshot(path="debug_zalo.png")
            print("📸 Đã chụp ảnh màn hình lỗi: debug_zalo.png")

        finally:
            context.close()


if __name__ == "__main__":
    noidung = get_smart_schedule()
    print("--- Nội dung dự kiến ---\n", noidung)
    send_zalo_msg(noidung)
