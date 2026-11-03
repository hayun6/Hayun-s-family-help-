"""가족 가전 교체 알리미 — Streamlit Community Cloud 배포용

[배포 방법]
1. GitHub 저장소에 app.py, requirements.txt 두 파일을 올립니다.
2. Supabase 프로젝트를 만든 뒤 SQL Editor에서 아래 SQL을 실행합니다.
   (한 프로젝트에서 여러 가족을 쓸 경우 가족마다 FAMILY_ID와 암호를 다르게 설정합니다.)

create table if not exists public.appliance_items (
  id uuid primary key,
  family_id text not null,
  kind text not null,
  name text not null,
  price bigint not null check (price >= 0),
  purchased_on date not null,
  replacement_on date not null,
  created_at timestamptz not null default now(),
  check (replacement_on > purchased_on)
);
create index if not exists appliance_family_idx
  on public.appliance_items (family_id, replacement_on);
alter table public.appliance_items enable row level security;
revoke all on public.appliance_items from anon, authenticated;
grant select, insert, delete on public.appliance_items to service_role;

3. Streamlit Community Cloud에서 GitHub 저장소를 연결하고 Main file을 app.py로 지정합니다.
4. 앱 Settings > Secrets에 다음 TOML을 입력합니다. 실제 값으로 교체하세요.

SUPABASE_URL = "https://YOUR_PROJECT.supabase.co"
SUPABASE_SERVICE_ROLE_KEY = "YOUR_SERVER_ONLY_SERVICE_ROLE_KEY"
FAMILY_ID = "your-random-family-id"
FAMILY_PASSWORD = "a-long-random-family-password"
APP_URL = "https://YOUR_APP.streamlit.app"

   SERVICE_ROLE_KEY는 서버 전용입니다. 코드/GitHub/QR/화면에 공개하지 마세요.
   Supabase 설정의 service_role 키(서버용)를 사용하세요. publishable/anon 키는 안 됩니다.
   가족은 같은 앱 URL과 공용 암호를 사용합니다. 암호를 QR에 넣지 않습니다.
   Community Cloud 앱 접근 설정에 따라 플랫폼 로그인이 추가로 필요할 수 있습니다.
5. 앱의 '설정·QR' 탭에서 QR을 표시하거나 PNG로 내려받습니다.

[동작 및 한계]
- 구매일 + 사용기간(개월)로 교체 예정일을 계산합니다. 예정일 직접 수정도 가능합니다.
- 한 달 전은 30일이 아니라 달력 기준 한 달 전입니다(말일 자동 보정).
- 한국 날짜 기준으로 앱을 열 때 및 열린 앱의 주기적 새로고침에서 확인합니다.
- 알림은 Streamlit 앱 안의 상단 토스트입니다. OS 푸시/카카오톡 메시지가 아닙니다.
  앱 종료·백그라운드·휴대폰 잠금 상태의 알림 및 정확한 시각의 전달은 보장하지 않습니다.
- 등록/삭제는 가족 공용 데이터에 반영됩니다. 삭제는 확인 체크 후 실행합니다.
- 좁은 휴대폰/확대 글꼴/많은 제품에서는 스크롤이 생길 수 있습니다.
- 가족 공용 암호 방식은 소규모 가족용입니다. 공개 다중 사용자 서비스에는 별도 인증이 필요합니다.
"""

from __future__ import annotations

import hmac
import io
import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo
from urllib.parse import urlparse

import pandas as pd
import qrcode
import requests
import streamlit as st
from dateutil.relativedelta import relativedelta

st.set_page_config(page_title="우리집 가전", page_icon="🏠", layout="centered", initial_sidebar_state="collapsed")
st.markdown("""<style>
.block-container {max-width:800px;padding-top:1.1rem;padding-bottom:1rem;}
h1 {font-size:1.65rem !important;} h3 {font-size:1.1rem !important;}
[data-testid="stMetricValue"] {font-size:1.5rem;}
[data-testid="stVerticalBlock"] {gap:.65rem;}
.stButton>button,.stFormSubmitButton>button {border-radius:12px;}
</style>""", unsafe_allow_html=True)

TODAY = datetime.now(ZoneInfo("Asia/Seoul")).date()
KINDS = ["냉장고", "세탁기", "건조기", "에어컨", "TV", "청소기", "주방가전", "생활가전", "기타"]


def config(key: str) -> str:
    try:
        return str(st.secrets.get(key, "")).strip()
    except Exception:
        return ""


URL = config("SUPABASE_URL").rstrip("/")
KEY = config("SUPABASE_SERVICE_ROLE_KEY")
FAMILY = config("FAMILY_ID")
PASSWORD = config("FAMILY_PASSWORD")


def api(method: str, *, body=None, item_id=None):
    params = {"family_id": f"eq.{FAMILY}"}
    if method == "GET":
        params.update({"select": "id,kind,name,price,purchased_on,replacement_on", "order": "replacement_on.asc"})
    if item_id:
        params["id"] = f"eq.{item_id}"
    try:
        response = requests.request(
            method, f"{URL}/rest/v1/appliance_items", params=params,
            headers={"apikey": KEY, "Authorization": f"Bearer {KEY}",
                     "Content-Type": "application/json", "Prefer": "return=representation"},
            json=body, timeout=15,
        )
        response.raise_for_status()
        return response.json() if response.content else []
    except requests.RequestException:
        # 키, 응답 전문, 내부 URL은 사용자 화면에 노출하지 않습니다.
        st.error("저장소에 연결하지 못했습니다. 잠시 후 다시 시도하거나 Supabase 연결·테이블 설정을 확인하세요.")
        return None


def reminder_day(replacement: date) -> date:
    return replacement - relativedelta(months=1)


def state_for(replacement: date, today: date) -> str:
    if replacement < today:
        return "🔴 교체일 지남"
    if replacement == today:
        return "🔴 오늘 교체"
    if today >= reminder_day(replacement):
        return "🟠 교체 준비"
    return "🟢 여유"


st.title("🏠 우리집 가전")
st.caption("구매는 간편하게, 교체는 잊지 않게 · 가족 공유")

if not all([URL, KEY, FAMILY, PASSWORD]):
    st.info("처음 한 번만 Supabase와 가족 암호를 연결해 주세요. 배포 방법은 app.py 맨 위에 있습니다.")
    with st.expander("필요한 Secrets 이름"):
        st.code("SUPABASE_URL\nSUPABASE_SERVICE_ROLE_KEY\nFAMILY_ID\nFAMILY_PASSWORD\nAPP_URL (QR용)")
    st.stop()

if not st.session_state.get("authenticated", False):
    st.caption("가족에게 받은 공용 암호를 입력하세요.")
    with st.form("login"):
        entered = st.text_input("가족 암호", type="password", max_chars=256)
        login = st.form_submit_button("우리집 들어가기", use_container_width=True)
    if login:
        if hmac.compare_digest(entered.encode(), PASSWORD.encode()):
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("암호를 확인해 주세요.")
    st.stop()

add_tab, list_tab, qr_tab = st.tabs(["➕ 간편 등록", "📋 우리집 목록", "🔗 설정·QR"])

with add_tab:
    st.caption("사용기간은 제품 설명서와 실제 상태를 참고해 정하세요. 자동 권장 수명은 적용하지 않습니다.")
    # 폼 밖 날짜/개월 위젯: 변경 시 예정일을 즉시 다시 계산합니다.
    c1, c2 = st.columns(2)
    bought = c1.date_input("구매일", value=TODAY, min_value=date(1970, 1, 1), max_value=TODAY, format="YYYY/MM/DD")
    months = c2.number_input("예상 사용기간 (개월)", min_value=1, max_value=600, value=60, step=12)
    calculated = bought + relativedelta(months=int(months))
    direct = st.checkbox("교체 예정일 직접 지정", value=False)
    if direct:
        replacement = st.date_input("교체 예정일", value=calculated,
                                    min_value=date(1970, 1, 1), max_value=date(2200, 12, 31),
                                    format="YYYY/MM/DD")
    else:
        replacement = calculated
    st.caption(f"교체 {replacement:%Y.%m.%d} · 알림 시작 {reminder_day(replacement):%Y.%m.%d}")
    with st.form("add_item", clear_on_submit=True):
        a, b = st.columns([1, 2])
        kind = a.selectbox("유형", KINDS)
        name = b.text_input("가전제품 이름", placeholder="예: 거실 공기청정기", max_chars=60)
        price = st.number_input("구매 가격 (원)", min_value=0, max_value=100_000_000, value=0, step=10000)
        submit = st.form_submit_button("등록하기", type="primary", use_container_width=True)
    if submit:
        if not name.strip():
            st.warning("제품 이름을 입력해 주세요.")
        elif replacement <= bought:
            st.warning("교체 예정일은 구매일보다 뒤여야 합니다.")
        else:
            result = api("POST", body={"id": str(uuid.uuid4()), "family_id": FAMILY,
                         "kind": kind, "name": name.strip(), "price": int(price),
                         "purchased_on": bought.isoformat(), "replacement_on": replacement.isoformat()})
            if result is not None:
                st.toast("제품을 등록했습니다. 우리집 목록에서 확인하세요.", icon="✅")

with list_tab:
    @st.fragment(run_every="60s")
    def inventory():
        today = datetime.now(ZoneInfo("Asia/Seoul")).date()
        rows = api("GET")
        if rows is None:
            return
        due = [r for r in rows if reminder_day(date.fromisoformat(r["replacement_on"])) <= today]
        a, b = st.columns(2)
        a.metric("등록 가전", f"{len(rows)}개")
        b.metric("교체 확인", f"{len(due)}개")
        # 같은 브라우저 세션에서는 하루에 제품별 한 번만 알립니다.
        seen = st.session_state.setdefault("seen_reminders", set())
        unseen = [r for r in due if (today.isoformat(), r["id"]) not in seen]
        if unseen:
            first = unseen[0]
            suffix = f" 외 {len(unseen)-1}개" if len(unseen) > 1 else ""
            st.toast(f"{first['name']}{suffix}: 교체 예정일을 확인하세요.", icon="🔔")
            seen.update((today.isoformat(), r["id"]) for r in unseen)
        if not rows:
            st.info("아직 등록된 가전이 없어요. 간편 등록에서 첫 제품을 추가하세요.")
            return
        only_due = st.toggle("교체 확인이 필요한 제품만", value=False)
        filtered = due if only_due else rows
        if not filtered:
            st.success("지금은 교체 확인이 필요한 제품이 없습니다.")
            return
        table = [{"상태": state_for(date.fromisoformat(r["replacement_on"]), today),
                  "제품": r["name"], "유형": r["kind"], "가격(원)": r["price"],
                  "구매일": r["purchased_on"], "교체일": r["replacement_on"]} for r in filtered]
        st.dataframe(pd.DataFrame(table), hide_index=True, use_container_width=True, height=220)
        with st.expander("제품 상세 · 삭제"):
            indexed = {r["id"]: r for r in filtered}
            chosen = st.selectbox("제품 선택", list(indexed),
                                  format_func=lambda k: f"{indexed[k]['name']} · {indexed[k]['purchased_on']} · {k[:6]}")
            r = indexed[chosen]
            replace_date = date.fromisoformat(r["replacement_on"])
            st.caption(f"알림 시작일 {reminder_day(replace_date):%Y.%m.%d} · 교체일 {replace_date:%Y.%m.%d}")
            confirmed = st.checkbox("가족 목록에서 이 제품을 삭제합니다", key=f"confirm_{chosen}")
            if st.button("선택 제품 삭제", disabled=not confirmed, key=f"delete_{chosen}"):
                if api("DELETE", item_id=chosen) is not None:
                    st.rerun()
        if st.button("목록 새로고침"):
            st.rerun()
    inventory()

with qr_tab:
    st.caption("QR에는 앱 주소만 들어갑니다. 가족 암호는 별도로 전달하세요.")
    app_url = config("APP_URL")
    parsed = urlparse(app_url)
    if app_url and parsed.scheme == "https" and parsed.netloc and not parsed.username and not parsed.password:
        image = qrcode.make(app_url)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        st.image(buffer.getvalue(), width=170)
        st.download_button("가족 공유 QR 다운로드", buffer.getvalue(), "family-appliances-qr.png", "image/png")
        st.link_button("앱 주소 열기", app_url)
    else:
        st.info("배포 후 Secrets에 APP_URL을 https 주소로 입력하면 QR이 생성됩니다.")
    st.caption("🔔 알림은 앱 내부 팝업입니다. 앱을 닫거나 화면이 잠기면 알림이 오지 않습니다.")
    st.caption("최초 설정: app.py 상단의 SQL 실행 → Secrets 등록 → 앱 실행")
    if st.button("로그아웃"):
        st.session_state.clear()
        st.rerun()
