"""
Bot Telegram báo Lịch học / Lịch thi / Học phí từ cổng sinh viên Thủy Lợi (sinhvien1.tlu.edu.vn).

Gọi thẳng API của web trường (đúng các request mà trang web tự gọi), không cần trình duyệt:
mỗi lần chạy chỉ vài giây.

Chạy bởi GitHub Actions mỗi sáng (xem .github/workflows/run_bot.yml):
    python bot_telegram.py         Tự chọn: thứ Hai gửi thêm tổng quan cả tuần.
    python bot_telegram.py tuan    Ép gửi bản thứ Hai (tổng quan tuần + nhắc học phí).
    python bot_telegram.py ngay    Ép gửi bản ngày thường.

Mỗi sáng bot gửi:
    - Lịch học hôm nay (hoặc báo nghỉ), kèm môn thi hôm nay nếu có.
    - 🚨 Lịch thi MỚI / THAY ĐỔI ngay khi trường đăng.
    - ⏰ Nhắc trước 1 ngày khi ngày mai có thi.
    - 💰 Học phí: báo khi số tiền còn phải đóng thay đổi; thứ Hai nhắc lại nếu còn nợ.
    - Thứ Hai: thêm tổng quan lịch học cả tuần + các môn thi sắp tới.

Biến môi trường cần có:
    TELE_BOT_TOKEN, TELE_CHAT_ID, MSV, PASS_TRUONG
Tùy chọn:
    BOT_STATE_DIR   thư mục lưu trạng thái giữa các lần chạy (mặc định .bot_state)
    TLU_BASE_URL    đổi địa chỉ web (dùng khi test)
"""

import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests


# =========================================================
# CẤU HÌNH
# =========================================================

BASE_URL = os.environ.get("TLU_BASE_URL", "https://sinhvien1.tlu.edu.vn").rstrip("/")
API = f"{BASE_URL}/education"

# Lấy từ tab Network (F12) của trang web trường.
API_DANG_NHAP = f"{API}/oauth/token"
API_HOC_KY = f"{API}/api/semester/semester_info"
API_LICH_HOC = f"{API}/api/StudentCourseSubject/studentLoginUser/{{hk}}"
API_DS_HOC_KY = f"{API}/api/semester/1/100"
API_DOT = f"{API}/api/registerperiod/find/{{hk}}"
API_LICH_THI = f"{API}/api/semestersubjectexamroom/getListRoomByStudentByLoginUser/{{hk}}/{{dot}}/{{lan}}"
API_HOC_PHI = f"{API}/api/student/viewstudentpayablebyLoginUser"

# client_id / client_secret là mã CHUNG của web trường (ai đăng nhập cũng gửi y hệt),
# không phải thông tin bí mật của bạn.
OAUTH_CLIENT = {"client_id": "education_client", "client_secret": "password"}

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
TIMEOUT_API = 20            # giây
MAX_RETRY_API = 3
MAX_RETRY_TELEGRAM = 3
LAN_THI = (1, 2, 3)         # lần 1 (thi chính), lần 2-3 (thi lại)
# Học kỳ còn liên quan tới lịch thi: đã bắt đầu (hoặc bắt đầu trong 30 ngày tới)
# và kết thúc chưa quá 60 ngày (trường hay xếp thi sau khi học kỳ kết thúc).
THI_TRUOC_HK_NGAY = 30
THI_SAU_HK_NGAY = 60
SO_LUONG_SONG_SONG = 6

STATE_DIR = Path(os.environ.get("BOT_STATE_DIR", ".bot_state"))
STATE_FILE = STATE_DIR / "state.json"

TEN_THU = ("Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật")


class LoiDangNhap(Exception):
    """Sai tài khoản/mật khẩu: không thử lại để tránh bị khóa tài khoản."""


# =========================================================
# TIỆN ÍCH
# =========================================================

def log(msg=""):
    print(msg, flush=True)


def hom_nay():
    return datetime.now(VN_TZ).date()


def ngan_gon(loi):
    dong = str(loi).strip().splitlines()
    return dong[0] if dong else type(loi).__name__


def ms_sang_ngay(ms):
    return datetime.fromtimestamp(ms / 1000, VN_TZ).date()


def tien(so):
    """9180000.0 -> '9.180.000'"""
    return f"{int(round(so or 0)):,}".replace(",", ".")


def link_lan_chay():
    """Link tới lần chạy GitHub Actions hiện tại (nếu có)."""
    server = os.environ.get("GITHUB_SERVER_URL")
    repo = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    if server and repo and run_id:
        return f"{server}/{repo}/actions/runs/{run_id}"
    return ""


# =========================================================
# API WEB TRƯỜNG
# =========================================================

class TluApi:
    def __init__(self, msv, password):
        self.msv = msv
        self.password = password
        self.s = requests.Session()
        self.s.headers["Accept"] = "application/json"

    def _goi(self, method, url, **kw):
        """Gọi API, tự thử lại khi lỗi mạng hoặc máy chủ trường lỗi (5xx)."""
        loi = None
        for i in range(1, MAX_RETRY_API + 1):
            try:
                r = self.s.request(method, url, timeout=TIMEOUT_API, **kw)
                if r.status_code < 500:
                    return r
                loi = f"HTTP {r.status_code}"
            except requests.RequestException as e:
                loi = e
            log(f"⚠️ API lỗi ({i}/{MAX_RETRY_API}): {ngan_gon(loi)}")
            if i < MAX_RETRY_API:
                time.sleep(3 * i)
        raise RuntimeError(f"Không gọi được API web trường: {ngan_gon(loi)}")

    def dang_nhap(self):
        r = self._goi("POST", API_DANG_NHAP, data={
            **OAUTH_CLIENT,
            "grant_type": "password",
            "username": self.msv,
            "password": self.password,
        })
        if r.status_code in (400, 401):     # OAuth trả invalid_grant khi sai mật khẩu
            raise LoiDangNhap("Sai MSV hoặc mật khẩu.")
        r.raise_for_status()
        token = r.json().get("access_token")
        if not token:
            raise RuntimeError("API đăng nhập không trả về token.")
        self.s.headers["Authorization"] = f"Bearer {token}"

    def get(self, url):
        r = self._goi("GET", url)
        r.raise_for_status()
        return r.json()


# =========================================================
# LỊCH HỌC
# =========================================================

def nhom_thuc_hanh(ten_lop):
    """'Lập trình mạng-5-26 (66ANM2 (THM 1))' -> 'THM 1'."""
    m = re.search(r"\b(TH[A-ZĐ]*\s*\d+)\b", ten_lop or "")
    return m.group(1) if m else ""


def tach_buoi_hoc(cac_mon):
    """
    JSON môn học -> danh sách khung giờ học, mỗi phần tử:
        thu (0 = thứ Hai .. 6 = Chủ nhật), tu/den (ngày ISO), bat_dau/ket_thuc ("12:55"),
        tiet ("7-9"), mon, phong, gv, nhom ("THM 1" nếu là buổi thực hành)
    weekIndex của trường: 2 = Thứ Hai ... 7 = Thứ Bảy, 8 = Chủ nhật.
    """
    buoi = []
    for mon in cac_mon or []:
        lop = mon.get("courseSubject") or {}
        ten = mon.get("subjectName") or "?"
        gv_lop = (lop.get("teacher") or {}).get("displayName")
        nhom = nhom_thuc_hanh(lop.get("displayName") or mon.get("subjectCode"))
        for tkb in lop.get("timetables") or []:
            try:
                thu = int(tkb["weekIndex"]) - 2
                tu, den = ms_sang_ngay(tkb["startDate"]), ms_sang_ngay(tkb["endDate"])
            except (KeyError, TypeError, ValueError):
                continue
            if not 0 <= thu <= 6:
                continue
            dau = tkb.get("startHour") or {}
            cuoi = tkb.get("endHour") or {}
            phong = (tkb.get("roomName") or (tkb.get("room") or {}).get("name") or "").strip(" `'")
            gv = (tkb.get("teacher") or {}).get("displayName") or tkb.get("teacherName") or gv_lop
            tiet = "-".join(str(x) for x in (dau.get("indexNumber"), cuoi.get("indexNumber")) if x)
            buoi.append({
                "thu": thu, "tu": tu.isoformat(), "den": den.isoformat(),
                "bat_dau": dau.get("startString") or "", "ket_thuc": cuoi.get("endString") or "",
                "tiet": tiet, "mon": ten, "phong": phong, "gv": gv or "", "nhom": nhom,
            })
    return buoi


def buoi_trong_ngay(buoi, ngay):
    iso = ngay.isoformat()
    ket_qua, da_co = [], set()
    for b in sorted(buoi, key=lambda b: (b["bat_dau"], b["mon"])):
        if b["thu"] == ngay.weekday() and b["tu"] <= iso <= b["den"]:
            khoa = (b["bat_dau"], b["mon"], b["phong"])
            if khoa not in da_co:
                da_co.add(khoa)
                ket_qua.append(b)
    return ket_qua


def _ten_mon(b):
    return f"{b['mon']} (thực hành {b['nhom']})" if b.get("nhom") else b["mon"]


# =========================================================
# LỊCH THI
# =========================================================

def _ngay_thi(phong_thi):
    """Ưu tiên chuỗi ngày mà web hiển thị (dd/mm/yyyy); không có thì dùng timestamp."""
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", phong_thi.get("examDateString") or "")
    if m:
        d, thang, nam = map(int, m.groups())
        try:
            return datetime(nam, thang, d).date()
        except ValueError:
            pass
    try:
        return ms_sang_ngay(phong_thi["examDate"]) if phong_thi.get("examDate") else None
    except (TypeError, ValueError, OSError):
        return None


def tach_lich_thi(ds, nguon):
    """
    JSON lịch thi -> danh sách môn thi gọn.
    KHÔNG bỏ môn nào: không đọc được ngày thì để ngay = "" và vẫn báo ("chưa rõ ngày").
    `nguon` = "hk|đợt|lần" để nhận ra cùng một môn khi phòng/giờ thay đổi.
    """
    ket_qua = []
    for e in ds or []:
        p = e.get("examRoom") or {}
        ngay = _ngay_thi(p)
        gio = p.get("examHour") or {}
        mon_hoc = (p.get("semesterSubjectExam") or {}).get("subject") or {}
        mon = mon_hoc.get("subjectName") or e.get("subjectName") or "Môn thi (chưa rõ tên)"
        ket_qua.append({
            "id": f"{nguon}|{mon_hoc.get('subjectCode') or mon}",
            "nguon": nguon,
            "lan": int(nguon.rsplit("|", 1)[-1]),
            "ngay": ngay.isoformat() if ngay else "",
            "ngay_web": (p.get("examDateString") or "").strip(),
            "bat_dau": gio.get("startString") or "",
            "ket_thuc": gio.get("endString") or "",
            "ca": gio.get("name") or "",
            "mon": mon,
            "sbd": str(e.get("examCode") or ""),
            "phong": ((p.get("room") or {}).get("name") or "").strip(),
            "dot": e.get("examPeriodCode") or "",
        })
    return ket_qua


def _d(t):
    return datetime.fromisoformat(t["ngay"]).date() if t["ngay"] else None


def con_lien_quan(t, ngay):
    """Môn chưa thi (hoặc chưa rõ ngày thì vẫn giữ để không bỏ sót)."""
    return not t["ngay"] or t["ngay"] >= ngay.isoformat()


def _gio(t):
    return f"{t['bat_dau']}–{t['ket_thuc']}" if t["bat_dau"] else (t["ca"] or "chưa rõ giờ")


def _ngay_hien_thi(t):
    if t["ngay"]:
        return f"{TEN_THU[_d(t).weekday()]} {_d(t):%d/%m/%Y}"
    return f"chưa rõ ngày ({t['ngay_web']}) - xem trên web" if t["ngay_web"] else "chưa rõ ngày - xem trên web"


def _ten_thi(t):
    return f"{t['mon']} (thi lại lần {t['lan']})" if t.get("lan", 1) > 1 else t["mon"]


def dong_thi(t, kem_ngay=True, thay_doi=()):
    phan = [f"📝 {_ten_thi(t)}"]
    phan += [f"⚠️ {x}" for x in thay_doi]          # vd "⚠️ Phòng: 301-A2 → 205-A2"
    phan.append((f"🗓️ {_ngay_hien_thi(t)} · " if kem_ngay else "🕐 ") + _gio(t))
    chi_tiet = " · ".join(x for x in (f"📍 {t['phong']}" if t["phong"] else "",
                                     f"SBD {t['sbd']}" if t["sbd"] else "") if x)
    if chi_tiet:
        phan.append(chi_tiet)
    return "\n".join(phan)


_TRUONG_SO_SANH = (("ngay", "Ngày", _ngay_hien_thi), ("bat_dau", "Giờ", _gio),
                   ("phong", "Phòng", lambda t: t["phong"] or "?"), ("sbd", "SBD", lambda t: t["sbd"] or "?"))


def so_sanh_thi(cu, moi):
    """Các dòng mô tả thay đổi, vd 'Phòng: 301-A2 → 205-A2'."""
    return [f"{ten}: {hien(cu)} → {hien(moi)}" for k, ten, hien in _TRUONG_SO_SANH if cu.get(k) != moi.get(k)]


# =========================================================
# HỌC PHÍ
# =========================================================

def tach_hoc_phi(js):
    con_no = js.get("differenceAmount")
    if con_no is None:
        con_no = js.get("totalReceiveAbleNotComplete") or 0
    return {
        "phai_dong": js.get("totalReceiveAble") or 0,
        "da_dong": js.get("totalReceived") or 0,
        "con_no": con_no,
        "chi_tiet": [
            (x.get("note") or "Khoản chưa đóng", x.get("amountAfterBalance") or x.get("amount") or 0)
            for x in js.get("receiveAbleNotCompleteDtos") or []
        ],
    }


def tin_hoc_phi(hp, tieu_de):
    dong = [tieu_de,
            f"Phải đóng: {tien(hp['phai_dong'])}đ · Đã đóng: {tien(hp['da_dong'])}đ",
            f"👉 Còn phải đóng: {tien(hp['con_no'])}đ"]
    dong += [f"  • {ten}: {tien(so)}đ" for ten, so in hp["chi_tiet"]]
    return "\n".join(dong)


# =========================================================
# TIN NHẮN LỊCH
# =========================================================

def tin_hom_nay(buoi, thi, ngay):
    tieu_de = f"{TEN_THU[ngay.weekday()]} {ngay:%d/%m/%Y}"
    hoc = buoi_trong_ngay(buoi, ngay)
    thi_nay = sorted((t for t in thi if t["ngay"] == ngay.isoformat()), key=lambda t: t["bat_dau"])
    phan = []

    if thi_nay:
        phan.append(f"🚨 HÔM NAY THI {len(thi_nay)} MÔN:")
        phan += [dong_thi(t, kem_ngay=False) for t in thi_nay]

    if hoc:
        phan.append(f"☀️ {tieu_de} - hôm nay có {len(hoc)} buổi học:")
        for b in hoc:
            dong = [f"🕐 {b['bat_dau']}–{b['ket_thuc']}" + (f" (tiết {b['tiet']})" if b["tiet"] else ""),
                    f"📘 {_ten_mon(b)}"]
            chi_tiet = " · ".join(x for x in (f"📍 {b['phong']}" if b["phong"] else "",
                                             f"👤 {b['gv']}" if b["gv"] else "") if x)
            if chi_tiet:
                dong.append(chi_tiet)
            phan.append("\n".join(dong))
    elif thi_nay:
        phan.append(f"📚 {tieu_de}: không có lịch học.")
    else:
        phan.append(f"😴 {tieu_de}\nHôm nay không có lịch học. Nghỉ!")
    return "\n\n".join(phan)


def tin_tuan(buoi, thi, ngay):
    """Tổng quan cả tuần (gửi sáng thứ Hai)."""
    dau = ngay - timedelta(days=ngay.weekday())
    khoang = f"({dau:%d/%m} - {dau + timedelta(days=6):%d/%m})"
    dong, tong = [f"📅 Lịch học tuần này {khoang}"], 0
    for k in range(7):
        trong_ngay = buoi_trong_ngay(buoi, dau + timedelta(days=k))
        tong += len(trong_ngay)
        if not trong_ngay:
            dong.append(f"\n{TEN_THU[k]}: nghỉ")
            continue
        dong.append(f"\n{TEN_THU[k]}:")
        for b in trong_ngay:
            phong = f" · {b['phong']}" if b["phong"] else ""
            dong.append(f"  • {b['bat_dau']} {_ten_mon(b)}{phong}")
    if tong == 0:
        dong = [f"🎉 Tuần này {khoang} không có lịch học nào. Nghỉ!"]

    sap_toi = [t for t in thi if con_lien_quan(t, ngay)]
    if sap_toi:
        dong.append(f"\n\n📝 Lịch thi sắp tới ({len(sap_toi)} môn):")
        for t in sap_toi:
            ngay_t = f"{_d(t):%d/%m}" if t["ngay"] else "chưa rõ ngày"
            gio = f" {t['bat_dau']}" if t["bat_dau"] else ""
            dong.append(f"  • {ngay_t}{gio} {_ten_thi(t)}" + (f" · {t['phong']}" if t["phong"] else ""))
    return "\n".join(dong)


# =========================================================
# LẤY DỮ LIỆU
# =========================================================

def lay_lich_hoc(api, hk):
    cac_mon = api.get(API_LICH_HOC.format(hk=hk["id"]))
    buoi = tach_buoi_hoc(cac_mon)
    if cac_mon and not buoi:
        raise RuntimeError("API trả dữ liệu lịch học nhưng không đọc được buổi nào.")
    log(f"✅ Lịch học: {len(cac_mon)} lớp, {len(buoi)} khung giờ")
    return buoi


def hoc_ky_can_xem_thi(api, hk_hien_tai, ngay):
    """Học kỳ hiện tại + học kỳ vừa kết thúc / sắp bắt đầu (lúc giao học kỳ vẫn không sót)."""
    ket_qua = {hk_hien_tai["id"]: hk_hien_tai.get("semesterName") or hk_hien_tai["id"]}
    try:
        for h in (api.get(API_DS_HOC_KY) or {}).get("content") or []:
            try:
                bat_dau, ket_thuc = ms_sang_ngay(h["startDate"]), ms_sang_ngay(h["endDate"])
            except (KeyError, TypeError, ValueError):
                continue
            if bat_dau - timedelta(days=THI_TRUOC_HK_NGAY) <= ngay <= ket_thuc + timedelta(days=THI_SAU_HK_NGAY):
                ket_qua[h["id"]] = h.get("semesterName") or h["id"]
    except Exception as e:
        log(f"⚠️ Không lấy được danh sách học kỳ, chỉ xem học kỳ hiện tại: {ngan_gon(e)}")
    return ket_qua


def lay_lich_thi(api, hk, ngay, state):
    """
    Hỏi mọi (học kỳ liên quan × đợt × lần thi), song song cho nhanh.
    Nguồn nào lỗi thì dùng lại kết quả lần trước của chính nguồn đó (state["thi_nguon"]),
    để một request lỗi không làm "biến mất" môn thi.
    Trả về (danh sách môn thi, tập nguồn tải thành công lần này).
    """
    cache = state.setdefault("thi_nguon", {})
    nguon = []
    for hk_id, ten_hk in hoc_ky_can_xem_thi(api, hk, ngay).items():
        try:
            cac_dot = [d["id"] for d in api.get(API_DOT.format(hk=hk_id)) or []]
            state.setdefault("thi_dot", {})[str(hk_id)] = cac_dot
        except Exception as e:
            cac_dot = state.get("thi_dot", {}).get(str(hk_id), [])
            log(f"⚠️ Không lấy được đợt thi {ten_hk}, dùng danh sách đã lưu: {ngan_gon(e)}")
        nguon += [(hk_id, dot, lan) for dot in cac_dot for lan in LAN_THI]

    def tai(n):
        hk_id, dot, lan = n
        return tach_lich_thi(api.get(API_LICH_THI.format(hk=hk_id, dot=dot, lan=lan)), f"{hk_id}|{dot}|{lan}")

    thi, ok, loi_khong_cache = [], set(), []
    with ThreadPoolExecutor(SO_LUONG_SONG_SONG) as pool:
        for n, kq in zip(nguon, pool.map(lambda n: _an_toan(tai, n), nguon)):
            khoa = "|".join(map(str, n))
            if isinstance(kq, Exception):
                if khoa in cache:
                    thi += cache[khoa]                       # dùng kết quả lần trước
                else:
                    loi_khong_cache.append(kq)
                continue
            ok.add(khoa)
            if kq:
                cache[khoa] = kq
                thi += kq
            else:
                cache.pop(khoa, None)

    if nguon and not ok:
        raise RuntimeError(f"Không tải được lịch thi: {ngan_gon(loi_khong_cache[0]) if loi_khong_cache else 'lỗi'}")
    if loi_khong_cache:
        log(f"⚠️ {len(loi_khong_cache)} nguồn lịch thi lỗi và chưa có bản lưu (sẽ thử lại lần sau)")

    # bỏ trùng theo id, sắp theo ngày (môn chưa rõ ngày xếp đầu cho dễ thấy)
    gop = {t["id"]: t for t in thi}
    ket_qua = sorted(gop.values(), key=lambda t: (t["ngay"], t["bat_dau"], t["mon"]))
    log(f"✅ Lịch thi: {len(ket_qua)} môn, {len(ok)}/{len(nguon)} nguồn tải được")
    return ket_qua, ok, len(loi_khong_cache)


def _an_toan(ham, *args):
    try:
        return ham(*args)
    except Exception as e:
        log(f"⚠️ Lịch thi {args[0]}: {ngan_gon(e)}")
        return e


def lay_hoc_phi(api):
    hp = tach_hoc_phi(api.get(API_HOC_PHI))
    log("✅ Học phí: " + ("còn nợ" if hp["con_no"] > 0 else "đã đóng đủ"))   # không in số tiền ra log public
    return hp


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
                    # Lỗi do request (chat_id sai...): thử lại cũng vô ích.
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


# =========================================================
# MAIN
# =========================================================

def main(argv):
    ngay = hom_nay()
    yeu_cau = argv[0] if argv else "auto"
    thu_hai = yeu_cau == "tuan" or (yeu_cau != "ngay" and ngay.weekday() == 0)
    log(f"🗓️ {TEN_THU[ngay.weekday()]} {ngay:%d/%m/%Y}" + (" (bản thứ Hai)" if thu_hai else ""))

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

    state = doc_state()
    loi = []          # (mục, lỗi) để báo ở cuối

    # ---- Đăng nhập + học kỳ hiện tại
    api = hk = None
    try:
        api = TluApi(msv, password)
        api.dang_nhap()
        hk = api.get(API_HOC_KY)
        log(f"✅ Đăng nhập OK, học kỳ {hk.get('semesterName')}")
    except LoiDangNhap as e:
        tg.tin(f"❌ Bot lịch học: {e} Hãy cập nhật secret PASS_TRUONG.")
        return 1          # dừng hẳn, không thử lại để tránh khóa tài khoản
    except Exception as e:
        loi.append(("Đăng nhập", e))
        api = None

    def lay(muc, ham, khoa_luu):
        """Lấy 1 mục qua API, lỗi thì dùng bản đã lưu. Trả (dữ liệu hoặc None, là_dữ_liệu_mới)."""
        if api is not None:
            try:
                du_lieu = ham()
                state[khoa_luu] = {"luc": datetime.now(VN_TZ).isoformat(timespec="minutes"),
                                   "du_lieu": du_lieu}
                return du_lieu, True
            except Exception as e:
                loi.append((muc, e))
        da_luu = state.get(khoa_luu)
        return (da_luu["du_lieu"] if da_luu else None), False

    # Mỗi mục độc lập: mục này lỗi không ảnh hưởng mục khác.
    buoi, buoi_moi = lay("Lịch học", lambda: lay_lich_hoc(api, hk), "lich_hoc")
    thi, thi_ok, thi_moi = None, set(), False
    if api is not None:
        try:
            thi, thi_ok, so_loi = lay_lich_thi(api, hk, ngay, state)
            state["lich_thi"] = {"luc": datetime.now(VN_TZ).isoformat(timespec="minutes"), "du_lieu": thi}
            thi_moi = True
            if so_loi and thu_hai:   # lỗi lẻ tẻ: chỉ báo vào thứ Hai cho đỡ phiền, ngày khác chỉ ghi log
                loi.append(("Lịch thi", RuntimeError(f"{so_loi} đợt thi không tải được (bot vẫn thử lại hằng ngày)")))
        except Exception as e:
            loi.append(("Lịch thi", e))
    if not thi_moi and state.get("lich_thi"):
        thi = state["lich_thi"]["du_lieu"]
    hp, hp_moi = lay("Học phí", lambda: lay_hoc_phi(api), "hoc_phi")

    ghi_chu = ""
    if buoi is not None and not buoi_moi:
        luc = datetime.fromisoformat(state["lich_hoc"]["luc"])
        ghi_chu = f"\n\n⚠️ Web trường đang lỗi, lịch lấy từ bản lưu lúc {luc:%H:%M %d/%m}."

    # ---- 1. Tổng quan tuần (thứ Hai)
    if thu_hai and buoi is not None:
        tg.tin(tin_tuan(buoi, thi or [], ngay) + ghi_chu)

    # ---- 2. Lịch hôm nay (kèm môn thi hôm nay)
    if buoi is not None:
        tg.tin(tin_hom_nay(buoi, thi or [], ngay) + ghi_chu)

    # ---- 3. Lịch thi mới / thay đổi / bị gỡ (so với lần đã báo trước)
    if thi_moi:
        da_bao = state.get("thi_da_bao")
        da_bao = da_bao if isinstance(da_bao, dict) else {}      # bỏ định dạng cũ
        hien_tai = {t["id"]: t for t in thi if con_lien_quan(t, ngay)}
        moi = [t for i, t in hien_tai.items() if i not in da_bao]
        doi = [(da_bao[i], t) for i, t in hien_tai.items() if i in da_bao and so_sanh_thi(da_bao[i], t)]
        # Chỉ coi là "bị gỡ" khi nguồn của môn đó tải THÀNH CÔNG lần này (tránh báo nhầm lúc web lỗi).
        go = [c for i, c in da_bao.items()
              if i not in hien_tai and c.get("nguon") in thi_ok and con_lien_quan(c, ngay)]

        phan = []
        if moi:
            phan.append(f"🚨 CÓ LỊCH THI MỚI ({len(moi)} môn):\n\n" + "\n\n".join(dong_thi(t) for t in moi))
        if doi:
            phan.append("🔄 LỊCH THI THAY ĐỔI:\n\n" + "\n\n".join(dong_thi(t, thay_doi=so_sanh_thi(c, t)) for c, t in doi))
        if go:
            phan.append("🗑️ LỊCH THI BỊ GỠ KHỎI WEB (kiểm tra lại với trường):\n\n"
                        + "\n\n".join(dong_thi(c) for c in go))

        if not phan or tg.tin("\n\n━━━━━━━━━━\n\n".join(phan)):
            # Nhớ trạng thái mới; giữ lại môn có nguồn lỗi lần này để không báo "gỡ" nhầm.
            giu = {i: c for i, c in da_bao.items()
                   if i not in hien_tai and c.get("nguon") not in thi_ok and con_lien_quan(c, ngay)}
            state["thi_da_bao"] = {**giu, **hien_tai}

    # ---- 4. Nhắc thi ngày mai
    mai = (ngay + timedelta(days=1)).isoformat()
    thi_mai = [t for t in (thi or []) if t["ngay"] == mai]
    if thi_mai:
        tg.tin(f"⏰ NGÀY MAI THI {len(thi_mai)} MÔN:\n\n" + "\n\n".join(dong_thi(t) for t in thi_mai))

    # ---- 5. Học phí: báo khi số còn phải đóng thay đổi; thứ Hai nhắc lại nếu còn nợ
    if hp_moi:
        truoc = state.get("hoc_phi_con_no")
        if truoc is None or round(truoc) != round(hp["con_no"]):
            if hp["con_no"] > 0:
                tg.tin(tin_hoc_phi(hp, "💰 HỌC PHÍ THAY ĐỔI" if truoc is not None else "💰 HỌC PHÍ"))
            elif truoc:
                tg.tin(f"✅ Học phí đã đóng đủ! (đã đóng {tien(hp['da_dong'])}đ)")
            state["hoc_phi_con_no"] = hp["con_no"]
        elif thu_hai and hp["con_no"] > 0:
            tg.tin(tin_hoc_phi(hp, "💰 NHẮC HỌC PHÍ"))

    ghi_state(state)

    # ---- 6. Báo lỗi (nếu có)
    if loi:
        if buoi is None:
            tg.tin("❌ Bot không lấy được lịch học và chưa có dữ liệu lưu.")
        tg.tin("⚠️ Một số mục bị lỗi:\n" + "\n".join(f"• {m}: {ngan_gon(e)}" for m, e in loi) + duoi)
        return 1

    log("🏁 Xong!")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
