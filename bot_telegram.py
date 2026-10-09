"""
Bot Telegram báo Lịch học / Lịch thi / Học phí từ cổng sinh viên TLU.

Chạy bởi GitHub Actions mỗi sáng thứ Hai (xem .github/workflows/run_bot.yml).

Biến môi trường cần có:
    TELE_BOT_TOKEN, TELE_CHAT_ID, MSV, PASS_TRUONG
Tùy chọn:
    BOT_STATE_DIR   thư mục lưu trạng thái giữa các lần chạy (mặc định .bot_state)
    TLU_BASE_URL    đổi địa chỉ web (dùng khi test)
"""

import hashlib
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright


# =========================================================
# CẤU HÌNH
# =========================================================

BASE_URL = os.environ.get("TLU_BASE_URL", "https://sinhvien1.tlu.edu.vn").rstrip("/")
URL_LOGIN = f"{BASE_URL}/#/login"
URL_LICH_HOC = f"{BASE_URL}/#/student/profile"
URL_LICH_THI = f"{BASE_URL}/#/search_exam_room_student/listing"
URL_HOC_PHI = f"{BASE_URL}/#/student_voucher_receive_pay/listing"

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

# Timeout (ms)
TIMEOUT_NAV = 60_000
TIMEOUT_ELEMENT = 30_000
TIMEOUT_CLICK = 12_000
TIMEOUT_DOI_NOI_DUNG = 10_000   # chờ bảng đổi sau khi chọn dropdown
TIMEOUT_HOC_PHI = 15_000        # chờ ô tiền nợ (không có = không nợ)

# Retry
MAX_RETRY_SCRAPE = 2            # thử lại cả phiên (mở browser + đăng nhập)
MAX_RETRY_MUC = 2               # thử lại từng mục (lịch học / thi / học phí)
MAX_RETRY_TELEGRAM = 3

# Lịch thi: chỉ xét năm học mới nhất và năm liền trước.
# Năm học cũ hơn không thể có lịch thi tương lai.
SO_NAM_HOC_THI = 2
LOAI_HOC_KY = ("Học kỳ chính", "Học kỳ hè")

# Ảnh rõ hơn khi xem trên điện thoại.
DEVICE_SCALE = 1.5

STATE_DIR = Path(os.environ.get("BOT_STATE_DIR", ".bot_state"))
STATE_FILE = STATE_DIR / "state.json"

# Selector dùng chung
SEL_DROPDOWN = ".page-content .ui-select-match"
SEL_OPTION = ".ui-select-container.open .ui-select-choices-row"
CSS_BANG_LICH_HOC = ".table-bordered"
CSS_BANG_THI = ".page-content table"


class LoiDangNhap(Exception):
    """Sai tài khoản/mật khẩu: không retry để tránh bị khóa tài khoản."""


# =========================================================
# TIỆN ÍCH CHUNG
# =========================================================

def log(msg=""):
    print(msg, flush=True)


def tach_khoang_ngay(text):
    """'Tuần 5 (24/08/2026 - 30/08/2026)' -> (date(2026,8,24), date(2026,8,30)) hoặc None."""
    m = re.search(
        r"\((\d{1,2}/\d{1,2}/\d{4})\s*-\s*(\d{1,2}/\d{1,2}/\d{4})\)",
        text or "",
    )
    if not m:
        return None
    try:
        return tuple(datetime.strptime(s, "%d/%m/%Y").date() for s in m.groups())
    except ValueError:
        return None


def chua_ngay(text, ngay):
    khoang = tach_khoang_ngay(text)
    return bool(khoang) and khoang[0] <= ngay <= khoang[1]


def cac_ngay_trong(text):
    """Mọi ngày dd/mm/yyyy hợp lệ có trong chuỗi."""
    ket_qua = []
    for s in re.findall(r"\b\d{1,2}/\d{1,2}/\d{4}\b", text or ""):
        try:
            ket_qua.append(datetime.strptime(s, "%d/%m/%Y").date())
        except ValueError:
            pass
    return ket_qua


def hom_nay():
    return datetime.now(VN_TZ).date()


def ngan_gon(loi):
    """Dòng đầu của thông báo lỗi (bỏ phần 'Call log' dài dòng của Playwright)."""
    dong = str(loi).strip().splitlines()
    return dong[0] if dong else type(loi).__name__


def link_lan_chay():
    """Link tới lần chạy GitHub Actions hiện tại (nếu có)."""
    server = os.environ.get("GITHUB_SERVER_URL")
    repo = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    if server and repo and run_id:
        return f"{server}/{repo}/actions/runs/{run_id}"
    return ""


# =========================================================
# TIỆN ÍCH PLAYWRIGHT
# =========================================================

# Tìm phần tử ĐANG HIỂN THỊ đầu tiên khớp CSS (querySelector không hiểu :visible).
_JS_TIM = "sel => [...document.querySelectorAll(sel)].find(e => e.getClientRects().length > 0)"

# Đọc innerText của bảng đang hiển thị. Trả null khi chưa có bảng hoặc tbody đang
# trống: Angular thường XÓA bảng rồi mới đổ dữ liệu mới, lúc trống không được tính
# là "đã cập nhật".
JS_DOC_TEXT = f"""sel => {{
    const el = ({_JS_TIM})(sel);
    if (!el) return null;
    const tb = el.querySelector('tbody');
    if (tb && !tb.querySelector('tr')) return null;
    return el.innerText;
}}"""

JS_BANG_CO_MON = f"""sel => {{
    const t = ({_JS_TIM})(sel);
    if (!t) return null;
    return [...t.querySelectorAll('tbody tr')].some(tr =>
        [...tr.querySelectorAll('td')].slice(1).some(td => td.innerText.trim() !== '')
    );
}}"""


def doc_text(page, css):
    try:
        return page.evaluate(JS_DOC_TEXT, css)
    except Exception:
        return None


def cho_text_on_dinh(page, css, timeout=5_000, khoang=200):
    """Chờ innerText của phần tử giống nhau ở 2 lần đo liên tiếp (bảng render xong)."""
    het_gio = time.monotonic() + timeout / 1000
    truoc = doc_text(page, css)
    while time.monotonic() < het_gio:
        page.wait_for_timeout(khoang)
        sau = doc_text(page, css)
        if sau is not None and sau == truoc:
            return True
        truoc = sau
    return False


class TheoDoiMang:
    """Đếm request API (xhr/fetch) để biết Angular đã tải xong dữ liệu chưa."""

    def __init__(self, page):
        self.tong = 0
        self.dang_cho = 0
        page.on("request", self._bat_dau)
        page.on("requestfinished", self._xong)
        page.on("requestfailed", self._xong)

    @staticmethod
    def _la_api(req):
        return req.resource_type in ("xhr", "fetch")

    def _bat_dau(self, req):
        if self._la_api(req):
            self.tong += 1
            self.dang_cho += 1

    def _xong(self, req):
        if self._la_api(req):
            self.dang_cho = max(0, self.dang_cho - 1)


_mang = None   # TheoDoiMang của page hiện tại


def cho_bang_cap_nhat(page, css, noi_dung_cu, so_request_truoc):
    """
    Sau khi đổi dropdown, dừng chờ ngay khi:
      - bảng đã có nội dung mới, hoặc
      - request API đã xong (dữ liệu mới có thể giống hệt dữ liệu cũ), hoặc
      - 1.5s mà không có request nào (lựa chọn không cần tải dữ liệu).
    Không phải ngồi chờ hết timeout khi bảng không đổi như trước.
    Sau đó chờ bảng render ổn định.
    """
    bat_dau = time.monotonic()
    het_gio = bat_dau + TIMEOUT_DOI_NOI_DUNG / 1000
    while time.monotonic() < het_gio:
        text = doc_text(page, css)
        if text is not None:
            if text != noi_dung_cu:
                break
            if _mang is not None:
                co_request = _mang.tong > so_request_truoc
                if co_request and _mang.dang_cho == 0:
                    break
                if not co_request and time.monotonic() - bat_dau > 1.5:
                    break
        page.wait_for_timeout(100)
    cho_text_on_dinh(page, css)


def so_request():
    return _mang.tong if _mang is not None else 0


def cho_danh_sach_on_dinh(page, selector, timeout):
    """Chờ số phần tử > 0 và không đổi qua 2 lần đo (ng-repeat render dần)."""
    het_gio = time.monotonic() + timeout / 1000
    truoc, lan_on_dinh = -1, 0
    while time.monotonic() < het_gio:
        so = page.locator(selector).count()
        if so > 0 and so == truoc:
            lan_on_dinh += 1
            if lan_on_dinh >= 2:
                return True
        else:
            lan_on_dinh = 0
        truoc = so
        page.wait_for_timeout(150)
    return truoc > 0


# Angular đổi trang bằng #hash: giao diện trang cũ còn nằm lại một lúc. Đánh dấu
# các phần tử cũ trước khi chuyển, rồi chờ chúng biến mất để không đọc nhầm bảng/
# dropdown của trang trước.
JS_DANH_DAU_CU = """() => document
    .querySelectorAll('.page-content table, .page-content .ui-select-match, .portlet-body')
    .forEach(e => e.setAttribute('data-bot-cu', ''))"""
JS_HET_TRANG_CU = "() => !document.querySelector('[data-bot-cu]')"


def mo_trang(page, url, lan_thu=3):
    for i in range(1, lan_thu + 1):
        try:
            try:
                page.evaluate(JS_DANH_DAU_CU)
            except Exception:
                pass
            page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_NAV)
            try:
                page.wait_for_function(JS_HET_TRANG_CU, timeout=10_000)
            except PWTimeout:
                pass   # web giữ nguyên khung cũ: vẫn tiếp tục như bình thường
            return
        except Exception as e:
            log(f"⚠️ Mở trang lỗi ({i}/{lan_thu}): {e}")
            if i < lan_thu:
                page.wait_for_timeout(3_000)
    raise RuntimeError(f"Không mở được {url}")


def dong_dropdown(page):
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass


def mo_dropdown(page, dropdown):
    """Click mở dropdown, trả về locator các option."""
    dropdown.click(timeout=TIMEOUT_CLICK)
    if not cho_danh_sach_on_dinh(page, SEL_OPTION, TIMEOUT_ELEMENT):
        dong_dropdown(page)
        raise RuntimeError("Dropdown không load được danh sách lựa chọn.")
    return page.locator(SEL_OPTION)


def chon_dropdown(page, index, css_bang, text=None, vi_tri=None):
    """
    Chọn một option ở dropdown thứ `index` (theo text hoặc vị trí) rồi chờ bảng cập nhật.
    Nếu option đó đang được chọn sẵn thì bỏ qua, không phải chờ bảng đổi.
    Trả về True nếu bảng đang hiển thị đúng lựa chọn.
    """
    dropdowns = page.locator(SEL_DROPDOWN)
    if dropdowns.count() <= index:
        return False

    dropdown = dropdowns.nth(index)
    try:
        dang_chon = dropdown.inner_text().strip()
        cu = doc_text(page, css_bang)
        options = mo_dropdown(page, dropdown)
        opt = options.filter(has_text=text).first if text is not None else options.nth(vi_tri)

        if opt.count() == 0:
            dong_dropdown(page)
            return False

        if opt.inner_text().strip() == dang_chon:
            dong_dropdown(page)
            return True

        truoc = so_request()
        opt.click(timeout=TIMEOUT_CLICK)
    except Exception as e:
        log(f"⚠️ Lỗi chọn dropdown #{index}: {e}")
        dong_dropdown(page)
        return False

    cho_bang_cap_nhat(page, css_bang, cu, truoc)
    return True


def chup(locator, path):
    locator.screenshot(path=path, animations="disabled")
    return path


def chup_debug(page, ten):
    path = f"debug_{ten}.png"
    try:
        page.screenshot(path=path, full_page=True)
        return path
    except Exception:
        return None


# =========================================================
# ĐĂNG NHẬP
# =========================================================

# Câu báo lỗi thường gặp ở form đăng nhập. Chỉ tính câu XUẤT HIỆN SAU khi bấm
# Đăng nhập, để chữ có sẵn trên trang không gây báo nhầm.
JS_KET_QUA_DANG_NHAP = r"""before => {
    if (!location.hash.includes('/login')) return 'ok';
    const t = document.body.innerText.toLowerCase();
    const m = t.match(/không đúng|không chính xác|sai mật khẩu|sai tài khoản|incorrect|invalid/);
    return (m && !before.includes(m[0])) ? 'sai' : false;
}"""


def dang_nhap(page, msv, password):
    log("🚀 Đăng nhập...")
    mo_trang(page, URL_LOGIN)
    page.wait_for_selector("#username", timeout=TIMEOUT_ELEMENT)
    page.fill("#username", msv)
    page.fill("#password", password)

    truoc = page.evaluate("() => document.body.innerText.toLowerCase()")
    page.click('button:has-text("Đăng nhập")', timeout=TIMEOUT_CLICK)

    try:
        ket_qua = page.wait_for_function(
            JS_KET_QUA_DANG_NHAP, arg=truoc, timeout=TIMEOUT_NAV
        ).json_value()
    except PWTimeout:
        raise RuntimeError("Đăng nhập quá thời gian chờ (web trường chậm?).")

    if ket_qua == "sai":
        raise LoiDangNhap("Sai MSV hoặc mật khẩu.")
    log("✅ Đăng nhập thành công")


# =========================================================
# LỊCH HỌC
# =========================================================

def cao_lich_hoc(page):
    """
    Chỉ xử lý ĐÚNG tuần chứa hôm nay (giờ VN).
    Trả về {"anh": path} nếu tuần này có lịch, {"tin": "..."} nếu được nghỉ.
    """
    log("📅 Lịch học...")
    mo_trang(page, URL_LICH_HOC)

    tab_bang = page.locator('a:has-text("Bảng")').first
    tab_bang.wait_for(state="visible", timeout=TIMEOUT_ELEMENT)
    tab_bang.click(timeout=TIMEOUT_CLICK)

    ngay = hom_nay()
    dau_tuan = ngay - timedelta(days=ngay.weekday())
    cuoi_tuan = dau_tuan + timedelta(days=6)
    khoang = f"({dau_tuan:%d/%m} - {cuoi_tuan:%d/%m})"
    nghi = {"tin": f"🎉 Tuần này {khoang} không có lịch học nào. Nghỉ!"}

    dropdown_tuan = (
        page.locator("label").filter(has_text="Tuần")
        .locator("..").locator(".ui-select-match")
    )
    dropdown_tuan.wait_for(state="visible", timeout=TIMEOUT_ELEMENT)

    if chua_ngay(dropdown_tuan.inner_text(), ngay):
        # Web đang mở sẵn đúng tuần này.
        page.locator(f"{CSS_BANG_LICH_HOC}:visible").first.wait_for(timeout=TIMEOUT_ELEMENT)
        cho_text_on_dinh(page, CSS_BANG_LICH_HOC)
    else:
        cu = doc_text(page, CSS_BANG_LICH_HOC)
        options = mo_dropdown(page, dropdown_tuan)
        # Lấy text mọi option trong 1 lần gọi thay vì đọc từng dòng.
        vi_tri = next(
            (i for i, t in enumerate(options.all_inner_texts()) if chua_ngay(t, ngay)),
            None,
        )
        if vi_tri is None:
            # Danh sách tuần không có tuần này -> chưa có lịch. KHÔNG chụp tuần khác.
            dong_dropdown(page)
            log("ℹ️ Không có tuần chứa hôm nay trong danh sách.")
            return nghi

        truoc = so_request()
        options.nth(vi_tri).click(timeout=TIMEOUT_CLICK)
        cho_bang_cap_nhat(page, CSS_BANG_LICH_HOC, cu, truoc)

        # Xác minh lại trước khi chụp để không bao giờ gửi nhầm tuần.
        sau = dropdown_tuan.inner_text()
        if not chua_ngay(sau, ngay):
            raise RuntimeError(f"Web không chuyển sang tuần hiện tại (đang: {sau.strip()}).")

    co_mon = page.evaluate(JS_BANG_CO_MON, CSS_BANG_LICH_HOC)
    if co_mon is None:
        raise RuntimeError("Không thấy bảng lịch học.")
    if not co_mon:
        return nghi

    path = chup(page.locator(f"{CSS_BANG_LICH_HOC}:visible").first, "anh_lich_hoc.png")
    log("✅ Đã chụp lịch học tuần này")
    return {"anh": path}


# =========================================================
# LỊCH THI
# =========================================================

def dong_thi_sap_toi(page, ngay):
    """Các dòng trong bảng thi có ngày >= hôm nay."""
    try:
        rows = page.locator(f"{CSS_BANG_THI} tbody tr").all_inner_texts()
    except Exception:
        return []
    return [r.strip() for r in rows if any(d >= ngay for d in cac_ngay_trong(r))]


def cao_lich_thi(page):
    """Trả về {"anh": path, "hash": ...} nếu có lịch thi sắp tới, ngược lại None."""
    log("📝 Lịch thi...")
    mo_trang(page, URL_LICH_THI)
    page.wait_for_selector(".page-content", timeout=TIMEOUT_ELEMENT)
    cho_text_on_dinh(page, CSS_BANG_THI)

    ngay = hom_nay()
    dong = dong_thi_sap_toi(page, ngay)

    if not dong:
        try:
            page.locator(SEL_DROPDOWN).first.wait_for(timeout=TIMEOUT_ELEMENT)
        except PWTimeout:
            raise RuntimeError("Trang lịch thi không load được ô chọn.")

        if page.locator(SEL_DROPDOWN).count() < 2:
            raise RuntimeError("Trang lịch thi thiếu ô chọn Năm học/Học kỳ.")

        for i in range(SO_NAM_HOC_THI):
            if dong or not chon_dropdown(page, 0, CSS_BANG_THI, vi_tri=i):
                break
            for loai in LOAI_HOC_KY:
                if not chon_dropdown(page, 1, CSS_BANG_THI, text=loai):
                    continue
                if page.locator(SEL_DROPDOWN).count() >= 3:
                    chon_dropdown(page, 2, CSS_BANG_THI, vi_tri=0)   # đợt thi mới nhất
                dong = dong_thi_sap_toi(page, ngay)
                if dong:
                    break

    if not dong:
        log("✅ Không có lịch thi sắp tới")
        return None

    vung = page.locator(".portlet-body:visible").last
    if vung.count() == 0:
        vung = page.locator(".page-content").first
    path = chup(vung, "anh_lich_thi.png")

    dau_van_tay = hashlib.sha256("\n".join(sorted(dong)).encode()).hexdigest()
    log(f"✅ Có {len(dong)} môn thi sắp tới, đã chụp")
    return {"anh": path, "hash": dau_van_tay}


# =========================================================
# HỌC PHÍ
# =========================================================

def kiem_tra_hoc_phi(page):
    """Trả về {"anh": path, "tin": ...} nếu còn nợ, ngược lại None."""
    log("💰 Học phí...")
    mo_trang(page, URL_HOC_PHI)
    page.wait_for_selector(".page-content", timeout=TIMEOUT_ELEMENT)

    o_tien = page.locator("strong.font-red").first
    try:
        o_tien.wait_for(state="visible", timeout=TIMEOUT_HOC_PHI)
    except PWTimeout:
        log("✅ Không thấy khoản nợ")
        return None

    chuoi = o_tien.inner_text().strip()
    so = re.sub(r"\D", "", chuoi)
    if not so:
        raise RuntimeError(f"Không đọc được số tiền nợ: {chuoi!r}")
    if int(so) <= 0:
        log("✅ Không còn nợ")
        return None

    vung = page.locator(".portlet-body:visible").first
    if vung.count() == 0:
        vung = page.locator(".page-content").first
    path = chup(vung, "anh_hoc_phi.png")

    # Không in số tiền ra log: log GitHub Actions của repo public ai cũng xem được.
    log("🚨 Còn khoản học phí chưa đóng, đã chụp")
    return {"anh": path, "tin": f"🚨 CẢNH BÁO HỌC PHÍ: {chuoi} VNĐ"}


# =========================================================
# CÀO DỮ LIỆU
# =========================================================

CAC_MUC = (
    ("lich_hoc", "Lịch học", cao_lich_hoc),
    ("lich_thi", "Lịch thi", cao_lich_thi),
    ("hoc_phi", "Học phí", kiem_tra_hoc_phi),
)


def chay_muc(page, khoa, ten, ham):
    """
    Chạy một mục có retry. Mỗi lần thử lại bắt đầu từ trang trắng để Angular load mới
    hoàn toàn (goto cùng URL chỉ khác #hash thì trình duyệt KHÔNG tải lại trang).
    """
    for i in range(1, MAX_RETRY_MUC + 1):
        try:
            if i > 1:
                page.goto("about:blank")
            return ham(page)
        except Exception as e:
            log(f"⚠️ {ten} lỗi ({i}/{MAX_RETRY_MUC}): {e}")
            loi = e
    raise RuntimeError(f"{ten}: {ngan_gon(loi)}") from loi


def scrape_mot_lan(msv, password):
    global _mang
    ket_qua = {"loi": []}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        try:
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                device_scale_factor=DEVICE_SCALE,
                locale="vi-VN",
                timezone_id="Asia/Ho_Chi_Minh",
            )
            page = context.new_page()
            _mang = TheoDoiMang(page)
            page.set_default_timeout(TIMEOUT_ELEMENT)
            page.set_default_navigation_timeout(TIMEOUT_NAV)

            try:
                dang_nhap(page, msv, password)
            except Exception:
                chup_debug(page, "dang_nhap")
                raise

            # Mỗi mục độc lập: một mục hỏng không làm mất kết quả của mục khác,
            # cũng không phải đăng nhập lại từ đầu.
            for khoa, ten, ham in CAC_MUC:
                try:
                    ket_qua[khoa] = chay_muc(page, khoa, ten, ham)
                except Exception as e:
                    ket_qua["loi"].append((str(e), chup_debug(page, khoa)))
                    dong_dropdown(page)
        finally:
            browser.close()

    return ket_qua


def scrape_data(msv, password):
    """Retry cả phiên chỉ khi lỗi ở mức trình duyệt/đăng nhập. Sai mật khẩu thì dừng ngay."""
    loi = None
    for i in range(1, MAX_RETRY_SCRAPE + 1):
        log(f"\n===== PHIÊN {i}/{MAX_RETRY_SCRAPE} =====")
        try:
            return scrape_mot_lan(msv, password)
        except LoiDangNhap:
            raise
        except Exception as e:
            loi = e
            log(f"❌ Phiên {i} lỗi: {e}")
            if i < MAX_RETRY_SCRAPE:
                time.sleep(10)
    raise RuntimeError(f"Thử {MAX_RETRY_SCRAPE} phiên đều lỗi: {ngan_gon(loi)}")


# =========================================================
# TRẠNG THÁI GIỮA CÁC LẦN CHẠY
# =========================================================

def doc_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def ghi_state(state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# =========================================================
# TELEGRAM
# =========================================================

class Telegram:
    def __init__(self, token, chat_id):
        self.base = f"https://api.telegram.org/bot{token}"
        self.chat_id = chat_id
        self.session = requests.Session()

    def _goi(self, method, data, files=None):
        data = {"chat_id": self.chat_id, **data}
        for i in range(1, MAX_RETRY_TELEGRAM + 1):
            cho = 3 * i
            try:
                r = self.session.post(f"{self.base}/{method}", data=data, files=files, timeout=30)
                if r.ok:
                    return True
                if r.status_code == 429:
                    try:
                        cho = int(r.json()["parameters"]["retry_after"]) + 1
                    except Exception:
                        pass
                elif r.status_code < 500:
                    # Lỗi do request (chat_id sai, ảnh quá lớn...): thử lại cũng vô ích.
                    log(f"❌ Telegram {method} lỗi {r.status_code}: {r.text}")
                    return False
                log(f"⚠️ Telegram {r.status_code}, thử lại sau {cho}s")
            except requests.RequestException as e:
                log(f"⚠️ Lỗi mạng Telegram ({i}/{MAX_RETRY_TELEGRAM}): {e}")
            if i < MAX_RETRY_TELEGRAM:
                time.sleep(cho)
        log(f"❌ Gửi Telegram {method} thất bại")
        return False

    def tin(self, text):
        return self._goi("sendMessage", {"text": text[:4096]})

    def anh(self, path, caption=""):
        try:
            noi_dung = Path(path).read_bytes()
        except OSError as e:
            log(f"❌ Không đọc được ảnh {path}: {e}")
            return False
        return self._goi(
            "sendPhoto",
            {"caption": caption[:1024]},
            files={"photo": (Path(path).name, noi_dung, "image/png")},
        )


# =========================================================
# MAIN
# =========================================================

def main():
    token = os.environ.get("TELE_BOT_TOKEN")
    chat_id = os.environ.get("TELE_CHAT_ID")
    if not token or not chat_id:
        log("❌ Thiếu TELE_BOT_TOKEN hoặc TELE_CHAT_ID")
        return 1

    tg = Telegram(token, chat_id)
    link = link_lan_chay()
    duoi = f"\nXem log: {link}" if link else ""

    msv = os.environ.get("MSV")
    password = os.environ.get("PASS_TRUONG")
    if not msv or not password:
        tg.tin("❌ Bot lịch học: thiếu secret MSV hoặc PASS_TRUONG." + duoi)
        return 1

    try:
        kq = scrape_data(msv, password)
    except Exception as e:
        if isinstance(e, LoiDangNhap):
            tg.tin(f"❌ Bot lịch học: {e} Hãy cập nhật secret PASS_TRUONG.")
        else:
            tg.tin(f"❌ Bot lịch học không lấy được dữ liệu tuần này.\n{ngan_gon(e)}{duoi}")
        if Path("debug_dang_nhap.png").exists():
            tg.anh("debug_dang_nhap.png", "Ảnh màn hình lúc lỗi")
        return 1

    # ---- Lịch học
    lh = kq.get("lich_hoc")
    if lh and lh.get("anh"):
        tg.anh(lh["anh"], "📌 Lịch học tuần này")
    elif lh and lh.get("tin"):
        tg.tin(lh["tin"])

    # ---- Lịch thi (báo rõ là mới hay chỉ nhắc lại)
    state = doc_state()
    lt = kq.get("lich_thi")
    if lt:
        moi = lt["hash"] != state.get("lich_thi_hash")
        caption = "🚨 CÓ LỊCH THI MỚI" if moi else "📝 Nhắc lịch thi sắp tới (không đổi)"
        if tg.anh(lt["anh"], caption):
            state["lich_thi_hash"] = lt["hash"]
    elif "lich_thi" in kq:
        # Cào thành công và không còn lịch thi -> lần sau có lịch sẽ báo là mới.
        state.pop("lich_thi_hash", None)
    ghi_state(state)

    # ---- Học phí
    hp = kq.get("hoc_phi")
    if hp:
        tg.anh(hp["anh"], hp["tin"])

    # ---- Lỗi từng mục: báo kèm ảnh màn hình qua Telegram (riêng tư),
    #      không upload lên GitHub vì repo public ai cũng tải được.
    if kq["loi"]:
        tg.tin("⚠️ Một số mục bị lỗi:\n" + "\n".join(f"• {m}" for m, _ in kq["loi"]) + duoi)
        for m, anh in kq["loi"]:
            if anh:
                tg.anh(anh, f"Ảnh lúc lỗi: {m[:200]}")
        return 1

    log("\n🏁 Xong!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
