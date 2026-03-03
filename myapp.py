import streamlit as st
import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
import qrcode
from PIL import Image
from io import BytesIO  # 이 줄이 있어야 사진의 노란 밑줄이 사라집니다!
import time
import re
import uuid
import datetime
from streamlit_qrcode_scanner import qrcode_scanner

# 1. 환경 설정 및 DB 연결
load_dotenv()

@st.cache_resource
def get_engine():
    return create_engine(st.secrets["DATABASE_URL"])

def run_query(query, params=None, fetch=False):
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(text(query), params or {})
        conn.commit() # SELECT가 아닌 경우 반영을 위해 필요
        if fetch:
            return result.fetchone()
        return None

def generate_qr(data):
    qr = qrcode.QRCode(version=1, box_size=10, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

# 시간 옵션 생성 함수
def get_time_options():
    options = []
    for h in range(24):
        for m in [0, 15, 30, 45]:
            options.append(time(h, m))
    return options

# UI 설정
st.set_page_config(page_title="partyfind", layout="centered")

if "user" not in st.session_state:
    st.session_state.user = None

# --- [1. 로그인 및 회원가입] ---
if st.session_state.user is None:
    st.title("👋 partyfind")
    with st.form("login_form"):
        u_nick = st.text_input("닉네임")
        u_phone = st.text_input("전화번호 (예: 01012345678)")
        if st.form_submit_button("입장하기"):
            # 전화번호 형식 검사 (Regex)
            phone_pattern = re.compile(r'^010\d{4}\d{4}$')
            if not phone_pattern.match(u_phone):
                st.error("❌ 전화번호 형식이 올바르지 않습니다. (010xxxxyyyy)")
            elif u_nick and u_phone:
                # 닉네임+번호 일치 조회
                user = run_query(
                    "SELECT id, nickname FROM users WHERE nickname = :name AND phone = :phone", 
                    {"name": u_nick, "phone": u_phone}, 
                    fetch=True
                )
                if user:
                    st.session_state.user = {"id": user[0][0], "nickname": user[0][1]}
                    st.rerun()
                else:
                    # 번호 중복 체크 (닉네임이 다른데 번호만 같은 경우 방지)
                    phone_exists = run_query("SELECT id FROM users WHERE phone=%s", (u_phone,), fetch=True)
                    if phone_exists:
                        st.error("⚠️ 이미 등록된 번호에요! 기존 닉네임으로 접속해주세요.")
                    else:
                        new_id = str(uuid.uuid4())[:8]
                        run_query("INSERT INTO users (id, nickname, phone) VALUES (%s, %s, %s)", (new_id, u_nick, u_phone))
                        st.session_state.user = {"id": new_id, "nickname": u_nick}
                        st.rerun()

# --- [2. 메인 서비스] ---
else:
    with st.sidebar:
        st.write(f"👤 **{st.session_state.user['nickname']}** 님")
        if st.button("로그아웃"):
            st.session_state.user = None
            st.rerun()

    tab_list, tab_manage, tab_create = st.tabs(["🏠 모임 리스트", "👑 내 모임 관리", "➕ 방 만들기"])

    # [TAB 1. 리스트]
    with tab_list:
        meetings = run_query("""
            SELECT m.id, m.title, m.target_count, m.status, u.nickname,
                   (SELECT COUNT(*) FROM attendance WHERE meeting_id=m.id AND status='confirmed') as c_count,
                   m.description, m.start_at, m.end_at
            FROM meetings m JOIN users u ON m.user_id=u.id
            WHERE m.status != '종료' ORDER BY m.created_at DESC
        """, fetch=True)
        for m in meetings:
            with st.container(border=True):
                col1, col2 = st.columns([3, 1])
                col1.subheader(m[1])
                col1.write(f"📝 {m[6]}")
                col1.caption(f"⏰ {m[7].strftime('%H:%M')} ~ {m[8].strftime('%H:%M')}")
                my_status = run_query("SELECT status FROM attendance WHERE meeting_id=%s AND user_id=%s", (m[0], st.session_state.user['id']), fetch=True)
                if my_status:
                    if my_status[0][0] == 'pending':
                        if col2.button("내 QR보기", key=f"qr_{m[0]}"):
                            st.image(generate_qr(f"USER:{m[0]}:{st.session_state.user['id']}"), width=150)
                    else: col2.success("출석완료")
                elif m[3] == '모집중':
                    if col2.button("참여", key=f"join_{m[0]}"):
                        run_query("INSERT INTO attendance (meeting_id, user_id, is_host, status) VALUES (%s, %s, False, 'pending')", (m[0], st.session_state.user['id']))
                        st.rerun()

    # [TAB 2. 방장 관리]
    with tab_manage:
        # 내가 방장인 모임 중 아직 종료되지 않은 것 조회
        my_hosting = run_query("SELECT id, title, target_count, status FROM meetings WHERE user_id=%s AND status!='종료'", (st.session_state.user['id'],), fetch=True)
        
        if my_hosting:
            for h in my_hosting:
                st.subheader(f"📍 {h[1]}")
                
                # 현재 출석 인원 계산
                mems = run_query("SELECT u.nickname, a.status, u.id FROM attendance a JOIN users u ON a.user_id=u.id WHERE a.meeting_id=%s", (h[0],), fetch=True)
                conf_count = len([m for m in mems if m[1]=='confirmed'])
                st.write(f"🔥 현재 출석 인원: **{conf_count} / {h[2]}**")

                # 1. QR 출석체크 기능
                if st.button("📸 QR 출석체크 시작", key=f"btn_scan_{h[0]}"):
                    st.session_state[f"scan_active_{h[0]}"] = True
                
                if st.session_state.get(f"scan_active_{h[0]}", False):
                    sc_val = qrcode_scanner(key=f"scanner_{h[0]}")
                    if sc_val:
                        if sc_val.startswith("USER:"):
                            _, mid, uid = sc_val.split(":")
                            if mid == h[0]:
                                run_query("UPDATE attendance SET status='confirmed' WHERE meeting_id=%s AND user_id=%s", (mid, uid))
                                st.success("출석 인정!")
                                st.session_state[f"scan_active_{h[0]}"] = False
                                st.rerun()
                            else:
                                st.error("❌ 우리 파티원이 아니에요.")

                # 2. 식당 선택 및 사장님 인증 (정원의 1/2 이상 출석 시 노출)
                if conf_count >= (h[2] / 2):
                    st.divider()
                    st.success("✅ 조건 달성! 방문하신 식당을 선택해 사장님 확인을 받으세요.")
                    
                    # DB에서 모든 식당 목록 로드 (S1~S5 등)
                    all_shops = run_query("SELECT id, name, pass, description FROM shops ORDER BY id ASC", fetch=True)
                    
                    for s in all_shops:
                        # 식당별 아코디언 생성
                        with st.expander(f"🍴 {s[1]}"):
                            st.write(f"🎁 **혜택:** {s[3]}") # 사장님이 작성한 description
                            
                            # 각 식당별 독립된 인증 폼
                            with st.form(f"auth_form_{h[0]}_{s[0]}"):
                                pin = st.text_input("사장님 확인 (비밀번호)", type="password", key=f"pin_{h[0]}_{s[0]}")
                                if st.form_submit_button(f"{s[1]} 인증 완료"):
                                    if pin == s[2]: # 해당 식당의 pass와 일치하는지 확인
                                        # shop_logs에 선택한 식당 ID와 함께 기록
                                        run_query("INSERT INTO shop_logs (shop_id, meeting_id, coupon_count) VALUES (%s, %s, %s)", (s[0], h[0], conf_count))
                                        # 해당 모임 종료 처리
                                        run_query("UPDATE meetings SET status='종료' WHERE id=%s", (h[0],))
                                        st.balloons()
                                        st.success(f"🎉 {s[1]} 인증 성공! 파티가 종료되었습니다.")
                                        st.rerun()
                                    else:
                                        st.error("❌ 비밀번호가 틀렸습니다.")
                st.divider()
        else:
            st.info("현재 관리 중인 활성화된 모임이 없습니다.")

    # [TAB 3. 방 만들기 (이벤트성 제한 추가)]
    # [TAB 3. 방 만들기 (이벤트성 제한 및 시간 디폴트 추가)]
    with tab_create:
        # 오늘 생성된 방 개수 확인
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        daily_count = run_query("SELECT COUNT(*) FROM meetings WHERE created_at >= %s", (today_start,), fetch=True)[0][0]
        
        if daily_count >= 5:
            st.warning("🚫 오늘 준비된 모임 이벤트(5개)가 모두 마감되었습니다.")
        else:
            st.info(f"📢 오늘 남은 생성 가능 모임: {5 - daily_count}개")
            
            # --- 시간 디폴트 로직 추가 ---
            time_opts = get_time_options()
            
            # 현재 시간 기준으로 가장 가까운 15분 단위 인덱스 찾기
            now = datetime.now()
            current_time = now.time()
            def_start_idx = 0
            for i, t in enumerate(time_opts):
                if t >= current_time:
                    def_start_idx = i
                    break
            
            # 종료 시간은 시작 시간보다 1시간(4칸) 뒤로 기본 설정
            def_end_idx = min(def_start_idx + 4, len(time_opts) - 1)
            # --------------------------

            with st.form("create_form"):
                t = st.text_input("모임 제목")
                desc = st.text_area("모임 설명")
                tc = st.number_input("목표 인원 (최대 8명)", 2, 8, 4)
                
                col1, col2 = st.columns(2)
                # 계산된 인덱스를 index 파라미터에 적용
                s_t = col1.selectbox("시작 시간", time_opts, index=def_start_idx, format_func=lambda x: x.strftime('%H:%M'))
                e_t = col2.selectbox("종료 시간", time_opts, index=def_end_idx, format_func=lambda x: x.strftime('%H:%M'))
                
                if st.form_submit_button("방 생성"):
                    now_dt = datetime.now()
                    s_dt = datetime.combine(now_dt.date(), s_t)
                    e_dt = datetime.combine(now_dt.date(), e_t)
                    
                    if e_dt <= now_dt: 
                        st.error("❌ 종료 시간은 현재 시간보다 뒤여야 합니다.")
                    elif not t or not desc: 
                        st.error("❌ 제목과 설명을 모두 입력해주세요.")
                    else:
                        mid = str(uuid.uuid4())[:8]
                        run_query("""
                            INSERT INTO meetings (id, user_id, title, description, target_count, status, start_at, end_at) 
                            VALUES (%s, %s, %s, %s, %s, '모집중', %s, %s)
                        """, (mid, st.session_state.user['id'], t, desc, tc, s_dt, e_dt))
                        
                        run_query("""
                            INSERT INTO attendance (meeting_id, user_id, is_host, status) 
                            VALUES (%s, %s, True, 'confirmed')
                        """, (mid, st.session_state.user['id']))
                        
                        st.success("✅ 모임이 생성되었습니다!")
                        st.rerun()