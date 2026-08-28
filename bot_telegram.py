import os
import re
import time
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


# =========================================================
# CẤU HÌNH
# =========================================================

# Timeout cho thao tác UI
TIMEOUT_CLICK = 12000
TIMEOUT_TABLE_LOAD = 60000
TIMEOUT_DROPDOWN = 30000
TIMEOUT_CONTENT_CHANGE = 30000

# Nghỉ rất ngắn để Angular bắt đầu xử lý sau click.
# Không dùng sleep dài cố định.
WAIT_AFTER_SELECT = 250

# Retry
MAX_RETRY_PAGE = 3
MAX_RETRY_ACTION = 2
MAX_RETRY_SCRAPE = 2

RETRY_DELAY_PAGE = 3000       # milliseconds
RETRY_DELAY_ACTION = 3        # seconds
RETRY_DELAY_SCRAPE = 10       # seconds

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


# =========================================================
# HÀM TIỆN ÍCH
# =========================================================

def log(msg):
    print(msg, flush=True)


def cho_on_dinh(page, selector, timeout=TIMEOUT_TABLE_LOAD, state="visible"):
    """
    Chờ selector xuất hiện/hiển thị.

    Trả về:
        True  -> tìm thấy
        False -> timeout

    Hàm này KHÔNG đảm bảo dữ liệu bên trong selector đã đổi.
    """
    try:
        page.wait_for_selector(
            selector,
            timeout=timeout,
            state=state
        )
        return True

    except PlaywrightTimeoutError:
        log(
            f"⚠️ Chờ '{selector}' quá "
            f"{timeout / 1000:.0f}s mà chưa thấy."
        )
        return False


def cho_danh_sach_on_dinh(page, selector, timeout=8000):
    """
    Chờ số lượng phần tử trong danh sách ổn định qua 2 lần đo liên tiếp.

    Hữu ích với Angular ng-repeat vì option có thể render dần.
    """
    end_time = time.time() + timeout / 1000
    last_count = -1
    stable_count = 0

    while time.time() < end_time:
        try:
            current_count = page.locator(selector).count()
        except Exception:
            current_count = 0

        if current_count > 0 and current_count == last_count:
            stable_count += 1
            if stable_count >= 2:
                return True
        else:
            stable_count = 0

        last_count = current_count
        page.wait_for_timeout(150)

    return last_count > 0


def cho_noi_dung_doi(
    page,
    selector,
    noi_dung_cu,
    timeout=TIMEOUT_CONTENT_CHANGE
):
    """
    Chờ innerText của selector khác nội dung cũ.

    Nếu dữ liệu mới giống hệt dữ liệu cũ thì có thể timeout.
    Trường hợp đó trả False để code phía ngoài fallback.
    """
    if noi_dung_cu is None:
        return True

    try:
        page.wait_for_function(
            """
            ({sel, old}) => {
                const el = document.querySelector(sel);
                return el && el.innerText !== old;
            }
            """,
            arg={
                "sel": selector,
                "old": noi_dung_cu
            },
            timeout=timeout
        )
        return True

    except PlaywrightTimeoutError:
        log(
            "⚠️ Nội dung chưa đổi sau khi chọn. "
            "Có thể dữ liệu giống nhau hoặc server phản hồi chậm."
        )
        return False


def goto_retry(page, url, retries=MAX_RETRY_PAGE):
    """
    Mở một trang với retry riêng.

    Không dùng networkidle vì web Angular có thể tiếp tục gọi API nền.
    """
    last_error = None

    for attempt in range(1, retries + 1):
        try:
            log(
                f"🌐 Mở trang "
                f"({attempt}/{retries}): {url}"
            )

            page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=60000
            )

            return True

        except Exception as e:
            last_error = e
            log(
                f"⚠️ Mở trang lỗi "
                f"({attempt}/{retries}): {e}"
            )

            # Dừng navigation đang treo nếu có.
            try:
                page.evaluate("window.stop()")
            except Exception:
                pass

            if attempt < retries:
                page.wait_for_timeout(RETRY_DELAY_PAGE)

    log(f"❌ Không mở được trang: {last_error}")
    return False


def retry_action(name, action, retries=MAX_RETRY_ACTION):
    """
    Retry riêng một chức năng như:
        - lịch học
        - lịch thi
        - học phí

    Nếu thử hết vẫn lỗi thì raise lỗi cuối cùng để tầng retry toàn bộ xử lý.
    """
    last_error = None

    for attempt in range(1, retries + 1):
        try:
            log(f"🔁 {name}: lần {attempt}/{retries}")
            return action()

        except Exception as e:
            last_error = e
            log(
                f"⚠️ {name} lỗi "
                f"({attempt}/{retries}): {e}"
            )

            if attempt < retries:
                time.sleep(RETRY_DELAY_ACTION)

    raise RuntimeError(
        f"{name} thất bại sau {retries} lần: {last_error}"
    )


def tach_khoang_tuan(text):
    """
    Đọc chuỗi dạng:
        Tuần ... (24/08/2026 - 30/08/2026)

    Trả về:
        (start_date, end_date)
    hoặc:
        None
    """
    match = re.search(
        r"\((\d{1,2}/\d{1,2}/\d{4})"
        r"\s*-\s*"
        r"(\d{1,2}/\d{1,2}/\d{4})\)",
        text or ""
    )

    if not match:
        return None

    try:
        start_str, end_str = match.groups()

        start_date = datetime.strptime(
            start_str,
            "%d/%m/%Y"
        ).date()

        end_date = datetime.strptime(
            end_str,
            "%d/%m/%Y"
        ).date()

        return start_date, end_date

    except ValueError:
        return None


# =========================================================
# DROPDOWN
# =========================================================

def chon_dropdown(
    page,
    index,
    wait_selector_after,
    text_loc=None,
    index_option=None
):
    """
    Mở dropdown thứ `index`, chọn option rồi chờ dữ liệu cập nhật.

    Có thể chọn bằng:
        text_loc="Học kỳ chính"

    hoặc:
        index_option=0
    """
    try:
        dropdowns = page.locator(
            ".page-content .ui-select-match"
        )

        if dropdowns.count() <= index:
            log(
                f"⚠️ Không tồn tại dropdown #{index}."
            )
            return False

        # Lưu nội dung cũ của vùng dữ liệu.
        try:
            old_content = page.locator(
                wait_selector_after
            ).first.inner_text()
        except Exception:
            old_content = None

        dropdown = dropdowns.nth(index)
        dropdown.click(timeout=TIMEOUT_CLICK)

        page.wait_for_timeout(WAIT_AFTER_SELECT)

        option_selector = (
            ".ui-select-container.open "
            ".ui-select-choices-row"
        )

        if not cho_danh_sach_on_dinh(
            page,
            option_selector,
            timeout=TIMEOUT_DROPDOWN
        ):
            log(
                f"⚠️ Dropdown #{index} không load option."
            )

            try:
                page.keyboard.press("Escape")
            except Exception:
                pass

            return False

        options = page.locator(option_selector)

        if text_loc is not None:
            opt = options.filter(
                has_text=text_loc
            ).first

        elif index_option is not None:
            opt = options.nth(index_option)

        else:
            log("⚠️ Không có text_loc hoặc index_option.")
            page.keyboard.press("Escape")
            return False

        if opt.count() == 0:
            page.keyboard.press("Escape")
            return False

        try:
            if not opt.is_visible():
                page.keyboard.press("Escape")
                return False
        except Exception:
            page.keyboard.press("Escape")
            return False

        opt.click(timeout=TIMEOUT_CLICK)

        # Chờ vùng dữ liệu tồn tại.
        if not cho_on_dinh(
            page,
            wait_selector_after,
            timeout=TIMEOUT_TABLE_LOAD
        ):
            return False

        # Nếu đã có dữ liệu cũ, chờ nội dung thay đổi.
        # Nếu giống nhau thật thì fallback, không coi là lỗi chết.
        if old_content is not None:
            changed = cho_noi_dung_doi(
                page,
                wait_selector_after,
                old_content,
                timeout=TIMEOUT_CONTENT_CHANGE
            )

            if not changed:
                page.wait_for_timeout(700)

        return True

    except Exception as e:
        log(
            f"⚠️ Lỗi khi chọn dropdown #{index}: {e}"
        )

        try:
            page.keyboard.press("Escape")
        except Exception:
            pass

        return False


# =========================================================
# ĐĂNG NHẬP
# =========================================================

def dang_nhap(page, msv, password):
    log("🚀 BƯỚC 1: Đăng nhập hệ thống...")

    url = "https://sinhvien1.tlu.edu.vn/#/login"

    if not goto_retry(page, url):
        raise RuntimeError(
            "Không mở được trang đăng nhập."
        )

    if not cho_on_dinh(
        page,
        "#username",
        timeout=TIMEOUT_TABLE_LOAD
    ):
        raise RuntimeError(
            "Không thấy ô tài khoản."
        )

    page.fill("#username", msv)
    page.fill("#password", password)

    page.click(
        'button:has-text("Đăng nhập")',
        timeout=TIMEOUT_CLICK
    )

    # Không dùng networkidle.
    # Chờ URL rời route /login.
    try:
        page.wait_for_function(
            """
            () => !location.hash.includes('/login')
            """,
            timeout=60000
        )

    except PlaywrightTimeoutError:
        raise RuntimeError(
            "Đăng nhập timeout hoặc tài khoản/mật khẩu không đúng."
        )

    log("✅ Đăng nhập thành công!")


# =========================================================
# LỊCH HỌC
# =========================================================

def tuan_co_lich_hoc(page):
    """
    Kiểm tra bảng lịch học có môn hay không.

    Tối ưu bằng JavaScript trong browser thay vì gọi inner_text()
    cho từng ô từ Python.
    """
    try:
        tbody = page.locator(
            ".table-bordered:visible tbody"
        ).first

        if tbody.count() == 0:
            return False

        return tbody.evaluate(
            """
            tbody => {
                const rows = [
                    ...tbody.querySelectorAll('tr')
                ];

                return rows.some(row => {
                    const cells = [
                        ...row.querySelectorAll('td')
                    ].slice(1);

                    return cells.some(
                        cell => cell.innerText.trim().length > 0
                    );
                });
            }
            """
        )

    except Exception as e:
        log(
            f"⚠️ Lỗi khi kiểm tra bảng lịch học: {e}"
        )

        # Nếu lỗi thì coi như có lịch để tránh báo nhầm "được nghỉ".
        return True


def cao_lich_hoc(page):
    """
    Chỉ xử lý ĐÚNG tuần chứa ngày hôm nay.

    Quy tắc quan trọng:
    - Tuyệt đối không dùng tuần mặc định nếu tuần mặc định là tuần sau/tuần trước.
    - Nếu danh sách tuần không có tuần chứa hôm nay, coi tuần hiện tại là chưa có lịch học.
    - Sau khi chọn tuần, xác minh lại dropdown thật sự đang ở đúng tuần hiện tại trước khi chụp.

    Trả về:
        (path_anh, None)          -> tuần hiện tại có lịch
        (None, tin_nhan_nghi)     -> tuần hiện tại không có lịch

    Lỗi tải trang/dropdown thật sự sẽ raise để retry_action chạy lại.
    """
    log("📅 BƯỚC 2: Vào trang Lịch học...")

    url = (
        "https://sinhvien1.tlu.edu.vn/"
        "#/student/profile"
    )

    if not goto_retry(page, url):
        raise RuntimeError(
            "Không mở được trang lịch học."
        )

    if not cho_on_dinh(
        page,
        'a:has-text("Bảng")',
        timeout=TIMEOUT_TABLE_LOAD
    ):
        raise RuntimeError(
            "Không thấy tab Bảng."
        )

    page.locator(
        'a:has-text("Bảng")'
    ).first.click(
        timeout=TIMEOUT_CLICK
    )

    # -----------------------------------------------------
    # Xác định TUẦN HIỆN TẠI theo giờ Việt Nam.
    # Monday = 0 -> Sunday = 6.
    # Ví dụ 28/08/2026 -> 24/08/2026 - 30/08/2026.
    # -----------------------------------------------------
    today = datetime.now(VN_TZ).date()
    current_week_start = today - timedelta(days=today.weekday())
    current_week_end = current_week_start + timedelta(days=6)

    log(
        "🕒 Ngày hiện tại theo giờ Việt Nam: "
        f"{today.strftime('%d/%m/%Y')}"
    )
    log(
        "📆 Tuần cần kiểm tra: "
        f"{current_week_start.strftime('%d/%m/%Y')} - "
        f"{current_week_end.strftime('%d/%m/%Y')}"
    )

    khoang = (
        f" ({current_week_start.strftime('%d/%m')} - "
        f"{current_week_end.strftime('%d/%m')})"
    )

    def bao_tuan_nghi():
        log(
            f"🎉 Tuần này{khoang} không có lịch học."
        )
        return (
            None,
            (
                f"Tuần này{khoang} không có lịch học nào. "
                "Nghỉ"
            )
        )

    # -----------------------------------------------------
    # Chờ dropdown tuần.
    # -----------------------------------------------------
    dropdown_tuan = (
        page.locator("label")
        .filter(has_text="Tuần")
        .locator("..")
        .locator(".ui-select-match")
    )

    try:
        dropdown_tuan.wait_for(
            state="visible",
            timeout=TIMEOUT_DROPDOWN
        )
    except PlaywrightTimeoutError:
        raise RuntimeError(
            "Không load được dropdown Tuần."
        )

    # -----------------------------------------------------
    # BƯỚC A: Kiểm tra tuần web đang hiển thị sẵn.
    # Chỉ chấp nhận nếu ngày hôm nay thật sự nằm trong range đó.
    # -----------------------------------------------------
    try:
        selected_text = dropdown_tuan.inner_text().strip()
        selected_range = tach_khoang_tuan(selected_text)
    except Exception:
        selected_text = ""
        selected_range = None

    if selected_range:
        s, e = selected_range

        if s <= today <= e:
            log(
                "✅ Web đang hiển thị đúng tuần hiện tại: "
                f"{s.strftime('%d/%m')} - {e.strftime('%d/%m')}"
            )

            if not cho_on_dinh(
                page,
                ".table-bordered:visible",
                timeout=TIMEOUT_TABLE_LOAD
            ):
                raise RuntimeError(
                    "Không load được bảng lịch học tuần hiện tại."
                )

            if not tuan_co_lich_hoc(page):
                return bao_tuan_nghi()

            path = "anh_lich_hoc.png"
            page.locator(
                ".table-bordered:visible"
            ).first.screenshot(path=path)

            log("✅ Đã chụp đúng lịch học tuần hiện tại!")
            return path, None

        log(
            "⚠️ Web đang mặc định ở tuần khác: "
            f"{s.strftime('%d/%m')} - {e.strftime('%d/%m')}. "
            "Sẽ KHÔNG dùng bảng này."
        )

    # -----------------------------------------------------
    # BƯỚC B: Web không ở đúng tuần -> mở dropdown và tìm
    # OPTION có khoảng ngày chứa chính xác ngày hôm nay.
    # -----------------------------------------------------
    try:
        dropdown_tuan.click(timeout=TIMEOUT_CLICK)

        option_selector = (
            ".ui-select-container.open "
            ".ui-select-choices-row"
        )

        if not cho_danh_sach_on_dinh(
            page,
            option_selector,
            timeout=TIMEOUT_DROPDOWN
        ):
            raise RuntimeError(
                "Danh sách tuần không load."
            )

        rows = page.locator(option_selector)
        row_tuan_hien_tai = None
        range_tuan_hien_tai = None

        for i in range(rows.count()):
            row = rows.nth(i)

            try:
                text = row.inner_text().strip()
            except Exception:
                continue

            week_range = tach_khoang_tuan(text)

            if not week_range:
                continue

            start_date, end_date = week_range

            if start_date <= today <= end_date:
                row_tuan_hien_tai = row
                range_tuan_hien_tai = (
                    start_date,
                    end_date
                )
                break

        # -------------------------------------------------
        # Không hề có option chứa hôm nay.
        # Ví dụ hôm nay 28/08 nhưng option đầu tiên là
        # 31/08 - 06/09 => tuần 24/08 - 30/08 chưa có lịch.
        # TUYỆT ĐỐI KHÔNG chụp tuần 31/08.
        # -------------------------------------------------
        if row_tuan_hien_tai is None:
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass

            log(
                "✅ Dropdown không có tuần chứa ngày hôm nay. "
                "Coi tuần hiện tại là không có lịch học."
            )
            return bao_tuan_nghi()

        # Lưu nội dung bảng cũ trước khi đổi tuần.
        try:
            old_content = page.locator(
                ".table-bordered:visible"
            ).first.inner_text()
        except Exception:
            old_content = None

        start_date, end_date = range_tuan_hien_tai

        log(
            "👉 Chọn đúng tuần hiện tại: "
            f"{start_date.strftime('%d/%m/%Y')} - "
            f"{end_date.strftime('%d/%m/%Y')}"
        )

        row_tuan_hien_tai.click(
            timeout=TIMEOUT_CLICK
        )

        # Đợi UI có thời gian bắt đầu cập nhật.
        page.wait_for_timeout(WAIT_AFTER_SELECT)

        # Chờ bảng tồn tại.
        if not cho_on_dinh(
            page,
            ".table-bordered:visible",
            timeout=TIMEOUT_TABLE_LOAD
        ):
            raise RuntimeError(
                "Không load được bảng sau khi chọn tuần hiện tại."
            )

        # Chờ bảng đổi nếu trước đó có dữ liệu tuần khác.
        if old_content is not None:
            changed = cho_noi_dung_doi(
                page,
                ".table-bordered",
                old_content,
                timeout=TIMEOUT_CONTENT_CHANGE
            )

            # Nội dung có thể giống nhau, nên đây không phải điều kiện duy nhất.
            if not changed:
                page.wait_for_timeout(700)

        # -------------------------------------------------
        # BƯỚC C QUAN TRỌNG:
        # Xác minh dropdown SAU CLICK đang đúng tuần hiện tại.
        # Nếu vẫn là 31/08 hoặc tuần khác -> không chụp nhầm.
        # -------------------------------------------------
        try:
            selected_after = dropdown_tuan.inner_text().strip()
            selected_after_range = tach_khoang_tuan(selected_after)
        except Exception:
            selected_after = ""
            selected_after_range = None

        if not selected_after_range:
            raise RuntimeError(
                "Không xác minh được tuần sau khi chọn."
            )

        selected_start, selected_end = selected_after_range

        if not (
            selected_start <= today <= selected_end
        ):
            raise RuntimeError(
                "Web không chuyển sang đúng tuần hiện tại; "
                f"đang hiển thị {selected_start.strftime('%d/%m')} - "
                f"{selected_end.strftime('%d/%m')}. "
                "Hủy chụp để tránh gửi nhầm tuần."
            )

        log(
            "✅ Đã xác minh dropdown đang ở đúng tuần: "
            f"{selected_start.strftime('%d/%m')} - "
            f"{selected_end.strftime('%d/%m')}"
        )

    except RuntimeError:
        raise

    except Exception as e:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass

        raise RuntimeError(
            f"Lỗi khi chọn tuần hiện tại: {e}"
        )

    # -----------------------------------------------------
    # BƯỚC D: Chỉ tới đây khi đã xác minh chắc chắn
    # bảng đang là tuần chứa hôm nay.
    # -----------------------------------------------------
    if not tuan_co_lich_hoc(page):
        return bao_tuan_nghi()

    path = "anh_lich_hoc.png"

    page.locator(
        ".table-bordered:visible"
    ).first.screenshot(
        path=path
    )

    log("✅ Đã chụp đúng lịch học tuần hiện tại!")

    return path, None


# =========================================================
# LỊCH THI
# =========================================================

def kiem_tra_co_lich_tuong_lai(page, hom_nay):
    """
    Kiểm tra bảng hiện tại có ngày thi >= hôm nay hay không.

    hom_nay là datetime.date.
    """
    try:
        rows = page.locator(
            ".page-content table tbody tr"
        ).all_inner_texts()

        if not rows:
            return False

        chu_trong_bang = "\n".join(rows)

        if (
            "Không tìm thấy" in chu_trong_bang
            or "Không có" in chu_trong_bang
        ):
            return False

        cac_ngay_thi = re.findall(
            r"(\d{1,2}/\d{1,2}/\d{4})",
            chu_trong_bang
        )

        for ngay_str in cac_ngay_thi:
            try:
                ngay_thi = datetime.strptime(
                    ngay_str,
                    "%d/%m/%Y"
                ).date()

                if ngay_thi >= hom_nay:
                    log(
                        "👉 Có môn thi sắp tới: "
                        f"{ngay_str}"
                    )
                    return True

            except ValueError:
                pass

        log(
            "⚠️ Bảng hiện tại chỉ có lịch thi cũ."
        )

    except Exception as e:
        log(
            f"⚠️ Lỗi khi kiểm tra bảng lịch thi: {e}"
        )

    return False


def cao_lich_thi(page):
    log("📝 BƯỚC 3: Kiểm tra Lịch thi...")

    url = (
        "https://sinhvien1.tlu.edu.vn/"
        "#/search_exam_room_student/listing"
    )

    if not goto_retry(page, url):
        raise RuntimeError(
            "Không mở được trang lịch thi."
        )

    if not cho_on_dinh(
        page,
        ".page-content",
        timeout=TIMEOUT_TABLE_LOAD
    ):
        raise RuntimeError(
            "Trang lịch thi không load."
        )

    hom_nay = datetime.now(VN_TZ).date()

    # Kiểm tra dữ liệu mặc định trước.
    co_lich_thi = kiem_tra_co_lich_tuong_lai(
        page,
        hom_nay
    )

    # Nếu mặc định chưa có thì duyệt các dropdown.
    if not co_lich_thi:

        if cho_on_dinh(
            page,
            ".page-content .ui-select-match",
            timeout=TIMEOUT_DROPDOWN
        ):
            dropdowns = page.locator(
                ".page-content .ui-select-match"
            )

            so_luong_o = dropdowns.count()

            log(
                f"👉 Tìm thấy {so_luong_o} ô chọn."
            )

            if so_luong_o >= 2:

                # Lùi tối đa 4 năm học.
                for i in range(4):

                    if co_lich_thi:
                        break

                    log(
                        f"🔍 Đang kiểm tra Năm học "
                        f"thứ {i + 1}..."
                    )

                    if not chon_dropdown(
                        page,
                        0,
                        ".page-content table",
                        index_option=i
                    ):
                        break

                    for loai_ten in [
                        "Học kỳ chính",
                        "Học kỳ hè"
                    ]:
                        if co_lich_thi:
                            break

                        log(
                            "   👉 Kiểm tra: "
                            f"'{loai_ten}'"
                        )

                        if not chon_dropdown(
                            page,
                            1,
                            ".page-content table",
                            text_loc=loai_ten
                        ):
                            continue

                        dropdowns = page.locator(
                            ".page-content .ui-select-match"
                        )

                        # Nếu có dropdown Đợt thi:
                        if dropdowns.count() >= 3:
                            log(
                                "   👉 Chọn Đợt thi mới nhất..."
                            )

                            chon_dropdown(
                                page,
                                2,
                                ".page-content table",
                                index_option=0
                            )

                        if kiem_tra_co_lich_tuong_lai(
                            page,
                            hom_nay
                        ):
                            co_lich_thi = True

                            log(
                                "✅ Đã tìm thấy lịch thi sắp tới!"
                            )

                            break

            else:
                log(
                    "⚠️ Không đủ dropdown, "
                    "web có thể chưa load xong."
                )

    if not co_lich_thi:
        log(
            "✅ Không có lịch thi sắp tới."
        )
        return None

    log("🚨 Đang chụp ảnh Lịch thi...")

    vung_chup = page.locator(
        ".portlet-body"
    ).last

    try:
        if not vung_chup.is_visible():
            vung_chup = page.locator(
                ".page-content"
            ).first
    except Exception:
        vung_chup = page.locator(
            ".page-content"
        ).first

    path = "anh_lich_thi.png"

    vung_chup.screenshot(
        path=path
    )

    log("✅ Đã chụp xong lịch thi!")

    return path


# =========================================================
# HỌC PHÍ
# =========================================================

def kiem_tra_hoc_phi(page):
    log("💰 BƯỚC 4: Tra cứu Học phí...")

    url = (
        "https://sinhvien1.tlu.edu.vn/"
        "#/student_voucher_receive_pay/listing"
    )

    if not goto_retry(page, url):
        raise RuntimeError(
            "Không mở được trang học phí."
        )

    if not cho_on_dinh(
        page,
        ".page-content",
        timeout=TIMEOUT_TABLE_LOAD
    ):
        raise RuntimeError(
            "Trang học phí không load."
        )

    # strong.font-red có thể không tồn tại khi không nợ.
    found = cho_on_dinh(
        page,
        "strong.font-red",
        timeout=15000
    )

    if not found:
        log(
            "✅ Không thấy khoản nợ hiển thị. "
            "Có thể đã đóng đủ tiền."
        )
        return None, ""

    try:
        chuoi_tien_no = (
            page.locator("strong.font-red")
            .first
            .inner_text()
            .strip()
        )

        # Chỉ giữ chữ số để tránh lỗi dấu . , hoặc ký hiệu tiền.
        digits = re.sub(
            r"[^\d]",
            "",
            chuoi_tien_no
        )

        if not digits:
            raise ValueError(
                f"Không có số hợp lệ: {chuoi_tien_no}"
            )

        so_tien = int(digits)

    except Exception as e:
        raise RuntimeError(
            f"Không đọc được số tiền nợ: {e}"
        )

    if so_tien <= 0:
        log("✅ Số nợ = 0. Đã đóng đủ tiền!")
        return None, ""

    log(
        f"🚨 CẢNH BÁO: Đang nợ "
        f"{chuoi_tien_no} VNĐ!"
    )

    path = "anh_hoc_phi.png"

    vung_chup = page.locator(
        ".portlet-body"
    ).first

    if vung_chup.count() == 0:
        vung_chup = page.locator(
            ".page-content"
        ).first

    vung_chup.screenshot(
        path=path
    )

    return (
        path,
        (
            f"🚨 CẢNH BÁO HỌC PHÍ: "
            f"{chuoi_tien_no} VNĐ"
        )
    )


# =========================================================
# PLAYWRIGHT - MỘT LẦN CÀO
# =========================================================

def scrape_data_once(msv, password):
    ket_qua = {
        "anh_lich_hoc": None,
        "tin_nhan_lich_hoc": "",
        "anh_lich_thi": None,
        "anh_hoc_phi": None,
        "tin_nhan_hoc_phi": ""
    }

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )

        context = browser.new_context(
            viewport={
                "width": 1920,
                "height": 1080
            },
            locale="vi-VN",
            timezone_id="Asia/Ho_Chi_Minh"
        )

        page = context.new_page()

        page.set_default_navigation_timeout(
            60000
        )

        page.set_default_timeout(
            30000
        )

        try:
            # ---------------------------------------------
            # LOGIN
            # ---------------------------------------------
            dang_nhap(
                page,
                msv,
                password
            )

            # ---------------------------------------------
            # LỊCH HỌC
            # ---------------------------------------------
            lh_result = retry_action(
                "Lịch học",
                lambda: cao_lich_hoc(page)
            )

            if lh_result:
                anh_lh, msg_lh = lh_result

                ket_qua[
                    "anh_lich_hoc"
                ] = anh_lh

                ket_qua[
                    "tin_nhan_lich_hoc"
                ] = msg_lh or ""

            # ---------------------------------------------
            # LỊCH THI
            # ---------------------------------------------
            ket_qua[
                "anh_lich_thi"
            ] = retry_action(
                "Lịch thi",
                lambda: cao_lich_thi(page)
            )

            # ---------------------------------------------
            # HỌC PHÍ
            # ---------------------------------------------
            hp_result = retry_action(
                "Học phí",
                lambda: kiem_tra_hoc_phi(page)
            )

            if hp_result:
                anh_hp, msg_hp = hp_result

                ket_qua[
                    "anh_hoc_phi"
                ] = anh_hp

                ket_qua[
                    "tin_nhan_hoc_phi"
                ] = msg_hp or ""

        finally:
            browser.close()

    return ket_qua


# =========================================================
# RETRY TOÀN BỘ
# =========================================================

def scrape_data():
    msv = os.environ.get("MSV")
    password = os.environ.get("PASS_TRUONG")

    if not msv or not password:
        log(
            "❌ Lỗi: Không tìm thấy "
            "MSV hoặc PASS_TRUONG!"
        )
        return None

    last_error = None

    for attempt in range(
        1,
        MAX_RETRY_SCRAPE + 1
    ):
        try:
            log("")
            log(
                "======================================"
            )
            log(
                f"===== LẦN THỬ TOÀN BỘ "
                f"{attempt}/{MAX_RETRY_SCRAPE} ====="
            )
            log(
                "======================================"
            )

            return scrape_data_once(
                msv,
                password
            )

        except Exception as e:
            last_error = e

            log(
                f"❌ LỖI TOÀN BỘ "
                f"(lần {attempt}): {e}"
            )

            if attempt < MAX_RETRY_SCRAPE:
                log(
                    f"⏳ Thử lại toàn bộ sau "
                    f"{RETRY_DELAY_SCRAPE}s..."
                )

                time.sleep(
                    RETRY_DELAY_SCRAPE
                )

    log(
        f"❌ Đã thử "
        f"{MAX_RETRY_SCRAPE} lần "
        f"đều thất bại: {last_error}"
    )

    return None


# =========================================================
# TELEGRAM
# =========================================================

def send_telegram_photo(
    photo_path,
    caption,
    bot_token,
    chat_id
):
    url = (
        f"https://api.telegram.org/"
        f"bot{bot_token}/sendPhoto"
    )

    try:
        if not os.path.exists(photo_path):
            log(
                f"❌ Không tìm thấy ảnh: "
                f"{photo_path}"
            )
            return False

        with open(
            photo_path,
            "rb"
        ) as photo:

            payload = {
                "chat_id": chat_id,
                "caption": caption
            }

            files = {
                "photo": photo
            }

            response = requests.post(
                url,
                data=payload,
                files=files,
                timeout=30
            )

        if response.ok:
            log(
                f"🎉 Đã gửi ảnh: "
                f"{caption[:30]}..."
            )
            return True

        log(
            f"❌ Telegram lỗi "
            f"{response.status_code}: "
            f"{response.text}"
        )

    except requests.RequestException as e:
        log(
            f"❌ Lỗi kết nối Telegram: {e}"
        )

    except Exception as e:
        log(
            f"❌ Lỗi khi gửi ảnh Telegram: {e}"
        )

    return False


def send_telegram_message(
    text,
    bot_token,
    chat_id
):
    """
    Gửi tin nhắn văn bản thuần.
    """
    url = (
        f"https://api.telegram.org/"
        f"bot{bot_token}/sendMessage"
    )

    try:
        payload = {
            "chat_id": chat_id,
            "text": text
        }

        response = requests.post(
            url,
            data=payload,
            timeout=30
        )

        if response.ok:
            log(
                f"🎉 Đã gửi tin nhắn: "
                f"{text[:30]}..."
            )
            return True

        log(
            f"❌ Telegram lỗi "
            f"{response.status_code}: "
            f"{response.text}"
        )

    except requests.RequestException as e:
        log(
            f"❌ Lỗi kết nối Telegram: {e}"
        )

    except Exception as e:
        log(
            f"❌ Lỗi khi gửi tin nhắn Telegram: {e}"
        )

    return False


# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":

    bot_token = os.environ.get(
        "TELE_BOT_TOKEN"
    )

    chat_id = os.environ.get(
        "TELE_CHAT_ID"
    )

    if not bot_token or not chat_id:
        log(
            "❌ Lỗi: Không tìm thấy "
            "TELE_BOT_TOKEN hoặc TELE_CHAT_ID!"
        )

    else:
        du_lieu = scrape_data()

        if du_lieu:

            # ---------------------------------------------
            # LỊCH HỌC
            # ---------------------------------------------
            if du_lieu.get(
                "anh_lich_hoc"
            ):
                send_telegram_photo(
                    du_lieu[
                        "anh_lich_hoc"
                    ],
                    "📌 Lịch học tuần này",
                    bot_token,
                    chat_id
                )

            elif du_lieu.get(
                "tin_nhan_lich_hoc"
            ):
                send_telegram_message(
                    du_lieu[
                        "tin_nhan_lich_hoc"
                    ],
                    bot_token,
                    chat_id
                )

            # ---------------------------------------------
            # LỊCH THI
            # ---------------------------------------------
            if du_lieu.get(
                "anh_lich_thi"
            ):
                send_telegram_photo(
                    du_lieu[
                        "anh_lich_thi"
                    ],
                    "🚨 ĐÃ CÓ LỊCH THI MỚI",
                    bot_token,
                    chat_id
                )

            # ---------------------------------------------
            # HỌC PHÍ
            # ---------------------------------------------
            if du_lieu.get(
                "anh_hoc_phi"
            ):
                send_telegram_photo(
                    du_lieu[
                        "anh_hoc_phi"
                    ],
                    du_lieu[
                        "tin_nhan_hoc_phi"
                    ],
                    bot_token,
                    chat_id
                )
