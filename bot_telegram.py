import os
import re
import time
import requests
from datetime import datetime, timedelta, timezone
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# ============ CẤU HÌNH THỜI GIAN CHỜ (chỉnh 1 chỗ, áp dụng toàn bộ) ============
TIMEOUT_CLICK = 8000          # chờ click 1 phần tử
TIMEOUT_TABLE_LOAD = 45000    # chờ bảng dữ liệu load (web trường hay chậm -> để cao)
TIMEOUT_DROPDOWN = 20000      # chờ dropdown xuất hiện
WAIT_AFTER_SELECT = 800       # nghỉ ngắn sau khi bấm chọn option, cho UI kịp render
MAX_RETRY_SCRAPE = 2          # số lần thử lại TOÀN BỘ quá trình nếu gặp lỗi mạng/timeout
VN_TZ = timezone(timedelta(hours=7))


def log(msg):
    print(msg, flush=True)


def cho_on_dinh(page, selector, timeout=TIMEOUT_TABLE_LOAD, state='visible'):
    """
    Chờ 1 selector xuất hiện thay vì sleep cứng.
    - Web nhanh -> chạy tiếp ngay, không phí thời gian chờ vô ích.
    - Web chậm -> vẫn chờ tới tối đa `timeout` thay vì fail sớm.
    Trả về True/False, KHÔNG raise exception để code gọi tự quyết định bước tiếp theo.
    LƯU Ý: hàm này chỉ chờ phần tử XUẤT HIỆN/HIỂN THỊ, không đảm bảo NỘI DUNG bên trong
    đã là dữ liệu mới nhất (ví dụ bảng cũ vẫn đang "visible" trong lúc chờ Angular load
    dữ liệu mới). Nếu cần chờ nội dung đổi, dùng cho_noi_dung_doi() bên dưới.
    """
    try:
        page.wait_for_selector(selector, timeout=timeout, state=state)
        return True
    except PlaywrightTimeoutError:
        log(f"⚠️ Chờ '{selector}' quá {timeout / 1000:.0f}s mà chưa thấy, bỏ qua.")
        return False


def cho_danh_sach_on_dinh(page, selector, timeout=8000):
    """
    Chờ số lượng phần tử trong 1 danh sách (vd: các option của dropdown) không đổi
    qua 2 lần đo liên tiếp. Angular ng-repeat có thể render dần dần, nếu đọc danh sách
    quá sớm sẽ bị thiếu hoặc sai phần tử.
    """
    end_time = time.time() + timeout / 1000
    last_count = -1
    while time.time() < end_time:
        current_count = page.locator(selector).count()
        if current_count > 0 and current_count == last_count:
            return True
        last_count = current_count
        page.wait_for_timeout(150)
    return last_count > 0


def cho_noi_dung_doi(page, selector, noi_dung_cu, timeout=TIMEOUT_TABLE_LOAD):
    """
    Chờ cho tới khi innerText của `selector` KHÁC với `noi_dung_cu`.
    Dùng sau khi chọn 1 option mới (vd: đổi tuần) để chắc chắn bảng đã load dữ liệu
    mới thật sự, tránh tình trạng chụp ảnh nhầm dữ liệu CŨ còn sót lại trên màn hình
    (đây chính là nguyên nhân gây ra lỗi "chụp nhầm tuần").
    """
    if noi_dung_cu is None:
        return True
    try:
        page.wait_for_function(
            """({sel, old}) => {
                const el = document.querySelector(sel);
                return el && el.innerText !== old;
            }""",
            arg={"sel": selector, "old": noi_dung_cu},
            timeout=timeout
        )
        return True
    except PlaywrightTimeoutError:
        log("⚠️ Nội dung bảng không đổi sau khi chọn lại (có thể do dữ liệu 2 tuần trùng nhau).")
        return False


def chon_dropdown(page, index, wait_selector_after, text_loc=None, index_option=None):
    """
    Hàm dùng chung: click mở dropdown thứ `index`, chọn option (theo text hoặc theo vị trí),
    rồi chờ bảng dữ liệu load lại. Trả về True nếu chọn + load thành công.
    Thay thế cho việc copy-paste logic dropdown 3 lần như bản cũ.
    """
    try:
        dropdowns = page.locator('.page-content .ui-select-match')
        dropdowns.nth(index).click(timeout=TIMEOUT_CLICK)
        page.wait_for_timeout(WAIT_AFTER_SELECT)

        cho_danh_sach_on_dinh(page, '.ui-select-container.open .ui-select-choices-row', timeout=5000)
        options = page.locator('.ui-select-container.open .ui-select-choices-row')

        opt = options.filter(has_text=text_loc).first if text_loc else options.nth(index_option)

        if opt.count() == 0 or not opt.is_visible():
            page.keyboard.press('Escape')
            return False

        opt.click(timeout=TIMEOUT_CLICK)
        cho_on_dinh(page, wait_selector_after, timeout=TIMEOUT_TABLE_LOAD)
        return True
    except Exception as e:
        log(f"⚠️ Lỗi khi chọn dropdown #{index}: {e}")
        try:
            page.keyboard.press('Escape')
        except Exception:
            pass
        return False


def dang_nhap(page, msv, password):
    log("🚀 BƯỚC 1: Đăng nhập hệ thống...")
    page.goto('https://sinhvien1.tlu.edu.vn/#/login', timeout=60000)
    page.fill('#username', msv)
    page.fill('#password', password)
    page.click('button:has-text("Đăng nhập")')
    page.wait_for_load_state('networkidle', timeout=60000)


def tuan_co_lich_hoc(page):
    """
    Kiểm tra bảng lịch học đang hiển thị có tiết học nào không.
    Cấu trúc bảng: cột đầu tiên mỗi dòng là nhãn "Tiết X (giờ...)" nên LUÔN có chữ.
    Các cột từ Thứ hai -> Chủ nhật chỉ có chữ khi có môn học được xếp lịch, còn không
    thì trống. => Nếu tất cả các cột đó (từ cột thứ 2 trở đi) đều trống ở mọi dòng,
    nghĩa là tuần này không có lịch học.
    """
    try:
        rows = page.locator('.table-bordered:visible tbody tr').all()
        if not rows:
            return False
        for row in rows:
            cells = row.locator('td')
            so_cot = cells.count()
            for i in range(1, so_cot):  # bỏ qua cột đầu (cột "Tiết")
                if cells.nth(i).inner_text().strip():
                    return True
        return False
    except Exception as e:
        log(f"⚠️ Lỗi khi kiểm tra bảng có lịch học không: {e}")
        return True  # lỗi thì coi như CÓ lịch để không báo nhầm "được nghỉ"


def cao_lich_hoc(page):
    """
    Trả về tuple (duong_dan_anh, tin_nhan):
    - Nếu tuần này CÓ lịch học -> (path_anh, None)
    - Nếu tuần này KHÔNG có lịch học -> (None, "tin nhắn báo nghỉ")
    - Nếu lỗi không load được bảng -> (None, None)
    """
    log("📅 BƯỚC 2: Vào trang Lịch học...")
    page.goto('https://sinhvien1.tlu.edu.vn/#/student/profile', timeout=60000)

    cho_on_dinh(page, 'a:has-text("Bảng")', timeout=TIMEOUT_TABLE_LOAD)
    page.click('a:has-text("Bảng")')
    page.wait_for_timeout(WAIT_AFTER_SELECT)

    tuan_dang_xem = None  # (start_date, end_date) của tuần mà mình chủ động chọn

    try:
        dropdown_tuan = page.locator('label').filter(has_text="Tuần").locator('..').locator('.ui-select-match')
        dropdown_tuan.click(timeout=TIMEOUT_CLICK)
        cho_danh_sach_on_dinh(page, '.ui-select-choices-row', timeout=TIMEOUT_DROPDOWN)

        # So sánh theo NGÀY thuần túy (bỏ giờ/phút) cho đơn giản và chắc chắn
        today = datetime.now(VN_TZ).date()
        rows = page.locator('.ui-select-choices-row').all()
        found_week = False

        # Lưu nội dung bảng HIỆN TẠI trước khi đổi tuần, để lát nữa biết khi nào
        # bảng đã thực sự load dữ liệu mới (đây là điểm sửa quan trọng nhất).
        try:
            noi_dung_cu = page.locator('.table-bordered:visible').first.inner_text()
        except Exception:
            noi_dung_cu = None

        for row in rows:
            text = row.inner_text()
            match = re.search(r'\((\d{1,2}/\d{1,2}/\d{4})\s*-\s*(\d{1,2}/\d{1,2}/\d{4})\)', text)
            if not match:
                continue

            start_str, end_str = match.groups()
            start_date = datetime.strptime(start_str, '%d/%m/%Y').date()
            end_date = datetime.strptime(end_str, '%d/%m/%Y').date()

            if start_date <= today <= end_date:
                row.click()
                found_week = True
                tuan_dang_xem = (start_date, end_date)
                # ĐIỂM SỬA QUAN TRỌNG: chờ bảng đổi nội dung thật sự rồi mới đi tiếp,
                # tránh chụp nhầm dữ liệu tuần cũ còn hiển thị trên màn hình.
                cho_noi_dung_doi(page, '.table-bordered', noi_dung_cu, timeout=TIMEOUT_TABLE_LOAD)
                break

        if not found_week:
            log("⚠️ Không tìm thấy đúng tuần hiện tại trong danh sách, dùng tuần mặc định đang hiển thị.")
            page.keyboard.press('Escape')
    except Exception as e:
        log(f"⚠️ Không chọn được tuần hiện tại, dùng tuần mặc định: {e}")

    ok = cho_on_dinh(page, '.table-bordered:visible', timeout=TIMEOUT_TABLE_LOAD)
    if not ok:
        log("❌ Không load được bảng lịch học.")
        return None, None

    if not tuan_co_lich_hoc(page):
        if tuan_dang_xem:
            s, e = tuan_dang_xem
            khoang = f" ({s.strftime('%d/%m')} - {e.strftime('%d/%m')})"
        else:
            khoang = ""
        log(f"🎉 Tuần này{khoang} không có lịch học - được nghỉ!")
        return None, f"🎉 Tuần này{khoang} không có lịch học nào. Chúc bạn nghỉ ngơi vui vẻ!"

    path = "anh_lich_hoc.png"
    page.locator('.table-bordered:visible').first.screenshot(path=path)
    log("✅ Đã chụp xong lịch học!")
    return path, None


def kiem_tra_co_lich_tuong_lai(page, hom_nay):
    try:
        so_dong = page.locator('.page-content table tbody tr').count()
        if so_dong == 0:
            return False

        chu_trong_bang = page.locator('.page-content table').inner_text()
        if "Không tìm thấy" in chu_trong_bang or "Không có" in chu_trong_bang:
            return False

        cac_ngay_thi = re.findall(r'(\d{1,2}/\d{1,2}/\d{4})', chu_trong_bang)
        for ngay_str in cac_ngay_thi:
            try:
                ngay_thi = datetime.strptime(ngay_str, '%d/%m/%Y').replace(tzinfo=VN_TZ)
                if ngay_thi >= hom_nay:
                    log(f"👉 Chốt đơn môn thi sắp tới: {ngay_str}")
                    return True
            except ValueError:
                pass
        log("⚠️ Bảng này toàn lịch thi cũ trong quá khứ, vứt!")
    except Exception as e:
        log(f"⚠️ Lỗi khi kiểm tra bảng lịch thi: {e}")
    return False


def cao_lich_thi(page):
    log("📝 BƯỚC 3: Kiểm tra Lịch thi...")
    page.goto('https://sinhvien1.tlu.edu.vn/#/search_exam_room_student/listing', timeout=60000)
    cho_on_dinh(page, '.page-content', timeout=TIMEOUT_TABLE_LOAD)

    hom_nay = datetime.now(VN_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    co_lich_thi = kiem_tra_co_lich_tuong_lai(page, hom_nay)

    if not co_lich_thi:
        if cho_on_dinh(page, '.page-content .ui-select-match', timeout=TIMEOUT_DROPDOWN):
            dropdowns = page.locator('.page-content .ui-select-match')
            so_luong_o = dropdowns.count()
            log(f"👉 Tìm thấy {so_luong_o} ô chọn trên màn hình!")

            if so_luong_o >= 2:
                for i in range(4):  # lùi tối đa 4 năm học
                    if co_lich_thi:
                        break
                    log(f"🔍 Đang lục lọi Năm học thứ {i + 1}...")

                    if not chon_dropdown(page, 0, '.page-content table', index_option=i):
                        break  # hết năm học để lùi

                    for loai_ten in ["Học kỳ chính", "Học kỳ hè"]:
                        if co_lich_thi:
                            break
                        log(f"   👉 Bắn tỉa mục tiêu: '{loai_ten}'")

                        if not chon_dropdown(page, 1, '.page-content table', text_loc=loai_ten):
                            continue

                        dropdowns = page.locator('.page-content .ui-select-match')
                        if dropdowns.count() >= 3:
                            log("   👉 Đang cố định Đợt thi mới nhất...")
                            chon_dropdown(page, 2, '.page-content table', index_option=0)

                        if kiem_tra_co_lich_tuong_lai(page, hom_nay):
                            co_lich_thi = True
                            log("✅ HOÀN HẢO! Đã tìm ra lịch thi chuẩn!")
                            break
            else:
                log("⚠️ Không đếm đủ ô dropdown, web có thể đang lag.")

    if not co_lich_thi:
        log("✅ Đã kiểm tra không bỏ sót ngóc ngách nào. Không có lịch thi sắp tới.")
        return None

    log("🚨 Đang chụp ảnh Lịch thi...")
    vung_chup = page.locator('.portlet-body').last
    if not vung_chup.is_visible():
        vung_chup = page.locator('.page-content').first

    path = "anh_lich_thi.png"
    vung_chup.screenshot(path=path)
    return path


def kiem_tra_hoc_phi(page):
    log("💰 BƯỚC 4: Tra cứu Học phí...")
    page.goto('https://sinhvien1.tlu.edu.vn/#/student_voucher_receive_pay/listing', timeout=60000)

    found = cho_on_dinh(page, 'strong.font-red', timeout=TIMEOUT_TABLE_LOAD)
    if not found:
        log("✅ Không thấy khoản nợ nào hiển thị. Có thể đã đóng đủ tiền!")
        return None, ""

    try:
        chuoi_tien_no = page.locator('strong.font-red').first.inner_text().strip()
        so_tien = int(chuoi_tien_no.replace(',', '').replace('.', ''))
    except Exception as e:
        log(f"⚠️ Không đọc được số tiền nợ: {e}")
        return None, ""

    if so_tien <= 0:
        log("✅ Số nợ = 0. Đã đóng đủ tiền!")
        return None, ""

    log(f"🚨 CẢNH BÁO: Đang nợ {chuoi_tien_no} VNĐ!")
    path = "anh_hoc_phi.png"
    page.locator('.portlet-body').first.screenshot(path=path)
    return path, f"🚨 CẢNH BÁO HỌC PHÍ: {chuoi_tien_no} VNĐ"


def scrape_data_once(msv, password):
    ket_qua = {
        "anh_lich_hoc": None,
        "tin_nhan_lich_hoc": "",
        "anh_lich_thi": None,
        "anh_hoc_phi": None,
        "tin_nhan_hoc_phi": ""
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1920, 'height': 1080})
        page = context.new_page()
        page.set_default_navigation_timeout(60000)

        try:
            dang_nhap(page, msv, password)

            anh_lh, msg_lh = cao_lich_hoc(page)
            ket_qua["anh_lich_hoc"] = anh_lh
            ket_qua["tin_nhan_lich_hoc"] = msg_lh or ""

            ket_qua["anh_lich_thi"] = cao_lich_thi(page)

            anh_hp, msg_hp = kiem_tra_hoc_phi(page)
            ket_qua["anh_hoc_phi"] = anh_hp
            ket_qua["tin_nhan_hoc_phi"] = msg_hp
        finally:
            browser.close()

    return ket_qua


def scrape_data():
    msv = os.environ.get('MSV')
    password = os.environ.get('PASS_TRUONG')

    if not msv or not password:
        log("❌ Lỗi: Không tìm thấy MSV hoặc PASS_TRUONG!")
        return None

    last_error = None
    for attempt in range(1, MAX_RETRY_SCRAPE + 1):
        try:
            log(f"===== LẦN THỬ {attempt}/{MAX_RETRY_SCRAPE} =====")
            return scrape_data_once(msv, password)
        except Exception as e:
            last_error = e
            log(f"❌ LỖI TRONG QUÁ TRÌNH CÀO WEB (lần {attempt}): {e}")
            if attempt < MAX_RETRY_SCRAPE:
                log("⏳ Web có thể đang lag, thử lại sau 10s...")
                time.sleep(10)

    log(f"❌ Đã thử {MAX_RETRY_SCRAPE} lần đều thất bại: {last_error}")
    return None


def send_telegram_photo(photo_path, caption, bot_token, chat_id):
    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    try:
        with open(photo_path, 'rb') as photo:
            payload = {"chat_id": chat_id, "caption": caption}
            files = {"photo": photo}
            response = requests.post(url, data=payload, files=files, timeout=30)
            if response.status_code == 200:
                log(f"🎉 Đã gửi ảnh: {caption[:20]}...")
            else:
                log(f"❌ Lỗi gửi ảnh: {response.text}")
    except Exception as e:
        log(f"❌ Lỗi kết nối Telegram: {e}")


def send_telegram_message(text, bot_token, chat_id):
    """Gửi tin nhắn văn bản thuần (dùng khi không có ảnh để gửi, vd: báo tuần nghỉ)."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        payload = {"chat_id": chat_id, "text": text}
        response = requests.post(url, data=payload, timeout=30)
        if response.status_code == 200:
            log(f"🎉 Đã gửi tin nhắn: {text[:30]}...")
        else:
            log(f"❌ Lỗi gửi tin nhắn: {response.text}")
    except Exception as e:
        log(f"❌ Lỗi kết nối Telegram: {e}")


if __name__ == "__main__":
    bot_token = os.environ.get('TELE_BOT_TOKEN')
    chat_id = os.environ.get('TELE_CHAT_ID')

    if not bot_token or not chat_id:
        log("❌ Lỗi: Không tìm thấy TELE_BOT_TOKEN hoặc TELE_CHAT_ID!")
    else:
        du_lieu = scrape_data()

        if du_lieu:
            if du_lieu.get("anh_lich_hoc"):
                send_telegram_photo(du_lieu["anh_lich_hoc"], "📌 Lịch học tuần này", bot_token, chat_id)
            elif du_lieu.get("tin_nhan_lich_hoc"):
                send_telegram_message(du_lieu["tin_nhan_lich_hoc"], bot_token, chat_id)

            if du_lieu.get("anh_lich_thi"):
                send_telegram_photo(du_lieu["anh_lich_thi"], "🚨 ĐÃ CÓ LỊCH THI MỚI", bot_token, chat_id)

            if du_lieu.get("anh_hoc_phi"):
                send_telegram_photo(du_lieu["anh_hoc_phi"], du_lieu["tin_nhan_hoc_phi"], bot_token, chat_id)
