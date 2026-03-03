import streamlit as st
import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
import qrcode
from PIL import Image
from io import BytesIO
import re
import uuid
from datetime import datetime, time as dtime
from streamlit_qrcode_scanner import qrcode_scanner


# 1. 환경 설정 및 DB 연결
load_dotenv()

@st.cache_resource
def get_engine():
    return create_engine(
        st.secrets["DATABASE_URL"], 
        pool_pre_ping=True,
        connect_args={"connect_timeout": 10}
    ) 


def run_query(query, params=None, fetch=False):
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(text(query), params or {})
        if not fetch:
            conn.commit()
        if fetch:
            return result.fetchall()
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
            options.append(dtime(h, m))
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
            phone_pattern = re.compile(r'^010\d{4}\d{4}$')
            if not phone_pattern.match(u_phone):
                st.error("❌ 전화번호 형식이 올바르지 않습니다. (010xxxxyyyy)")
            elif u_nick and u_phone:
                user = run_query(
                    "SELECT id, nickname FROM users WHERE nickname = :name AND phone = :phone",
                    {"name": u_nick, "phone": u_phone},
                    fetch=True
                )
                if user:
                    st.session_state.user = {"id": user[0][0], "nickname": user[0][1]}
                    st.rerun()
                else:
                    phone_exists = run_query(
                        "SELECT id FROM users WHERE phone = :phone",
                        {"phone": u_phone},
                        fetch=True
                    )
                    if phone_exists:
                        st.error("⚠️ 이미 등록된 번호에요! 기존 닉네임으로 접속해주세요.")
                    else:
                        new_id = str(uuid.uuid4())[:8]
                        run_query(
                            "INSERT INTO users (id, nickname, phone) VALUES (:id, :nick, :phone)",
                            {"id": new_id, "nick": u_nick, "phone": u_phone}
                        )
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
        if meetings:
            for m in meetings:
                with st.container(border=True):
                    col1, col2 = st.columns([3, 1])
                    col1.subheader(m[1])
                    col1.write(f"📝 {m[6]}")
                    col1.caption(f"⏰ {m[7].strftime('%H:%M')} ~ {m[8].strftime('%H:%M')}")
                    my_status = run_query(
                        "SELECT status FROM attendance WHERE meeting_id=:mid AND user_id=:uid",
                        {"mid": m[0], "uid": st.session_state.user['id']},
                        fetch=True
                    )
                    if my_status:
                        if my_status[0][0] == 'pending':
                            if col2.button("내 QR보기", key=f"qr_{m[0]}"):
                                st.image(generate_qr(f"USER:{m[0]}:{st.session_state.user['id']}"), width=150)
                        else:
                            col2.success("출석완료")
                    elif m[3] == '모집중':
                        if col2.button("참여", key=f"join_{m[0]}"):
                            run_query(
                                "INSERT INTO attendance (meeting_id, user_id, is_host, status) VALUES (:mid, :uid, False, 'pending')",
                                {"mid": m[0], "uid": st.session_state.user['id']}
                            )
                            st.rerun()

    # [TAB 2. 방장 관리]
    with tab_manage:
        my_hosting = run_query(
            "SELECT id, title, target_count, status FROM meetings WHERE user_id=:uid AND status!='종료'",
            {"uid": st.session_state.user['id']},
            fetch=True
        )
        
        if my_hosting:
            for h in my_hosting:
                st.subheader(f"📍 {h[1]}")
                
                mems = run_query(
                    "SELECT u.nickname, a.status, u.id FROM attendance a JOIN users u ON a.user_id=u.id WHERE a.meeting_id=:mid",
                    {"mid": h[0]},
                    fetch=True
                )
                conf_count = len([m for m in mems if m[1] == 'confirmed']) if mems else 0
                st.write(f"🔥 현재 출석 인원: **{conf_count} / {h[2]}**")

                if st.button("📸 QR 출석체크 시작", key=f"btn_scan_{h[0]}"):
                    st.session_state[f"scan_active_{h[0]}"] = True
                
                if st.session_state.get(f"scan_active_{h[0]}", False):
                    sc_val = qrcode_scanner(key=f"scanner_{h[0]}")
                    if sc_val:
                        if sc_val.startswith("USER:"):
                            _, mid, uid = sc_val.split(":")
                            if mid == h[0]:
                                run_query(
                                    "UPDATE attendance SET status='confirmed' WHERE meeting_id=:mid AND user_id=:uid",
                                    {"mid": mid, "uid": uid}
                                )
                                st.success("출석 인정!")
                                st.session_state[f"scan_active_{h[0]}"] = False
                                st.rerun()
                            else:
                                st.error("❌ 우리 파티원이 아니에요.")

                if conf_count >= (h[2] / 2):
                    st.divider()
                    st.success("✅ 조건 달성! 방문하신 식당을 선택해 사장님 확인을 받으세요.")
                    
                    all_shops = run_query(
                        "SELECT id, name, pass, description FROM shops ORDER BY id ASC",
                        fetch=True
                    )
                    
                    if all_shops:
                        for s in all_shops:
                            with st.expander(f"🍴 {s[1]}"):
                                st.write(f"🎁 **혜택:** {s[3]}")
                                
                                with st.form(f"auth_form_{h[0]}_{s[0]}"):
                                    pin = st.text_input("사장님 확인 (비밀번호)", type="password", key=f"pin_{h[0]}_{s[0]}")
                                    if st.form_submit_button(f"{s[1]} 인증 완료"):
                                        if pin == s[2]:
                                            run_query(
                                                "INSERT INTO shop_logs (shop_id, meeting_id, coupon_count) VALUES (:sid, :mid, :cnt)",
                                                {"sid": s[0], "mid": h[0], "cnt": conf_count}
                                            )
                                            run_query(
                                                "UPDATE meetings SET status='종료' WHERE id=:mid",
                                                {"mid": h[0]}
                                            )
                                            st.balloons()
                                            st.success(f"🎉 {s[1]} 인증 성공! 파티가 종료되었습니다.")
                                            st.rerun()
                                        else:
                                            st.error("❌ 비밀번호가 틀렸습니다.")
                st.divider()
        else:
            st.info("현재 관리 중인 활성화된 모임이 없습니다.")

    # [TAB 3. 방 만들기]
    with tab_create:
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        daily_count_result = run_query(
            "SELECT COUNT(*) FROM meetings WHERE created_at >= :ts",
            {"ts": today_start},
            fetch=True
        )
        daily_count = daily_count_result[0][0] if daily_count_result else 0
        
        if daily_count >= 5:
            st.warning("🚫 오늘 준비된 모임 이벤트(5개)가 모두 마감되었습니다.")
        else:
            st.info(f"📢 오늘 남은 생성 가능 모임: {5 - daily_count}개")
            
            time_opts = get_time_options()
            
            now = datetime.now()
            current_time = now.time()
            def_start_idx = 0
            for i, t in enumerate(time_opts):
                if t >= current_time:
                    def_start_idx = i
                    break
            
            def_end_idx = min(def_start_idx + 4, len(time_opts) - 1)

            with st.form("create_form"):
                t = st.text_input("모임 제목")
                desc = st.text_area("모임 설명")
                tc = st.number_input("목표 인원 (최대 8명)", 2, 8, 4)
                
                col1, col2 = st.columns(2)
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
                            VALUES (:mid, :uid, :title, :desc, :tc, '모집중', :s_dt, :e_dt)
                        """, {"mid": mid, "uid": st.session_state.user['id'], "title": t, "desc": desc, "tc": tc, "s_dt": s_dt, "e_dt": e_dt})
                        
                        run_query("""
                            INSERT INTO attendance (meeting_id, user_id, is_host, status) 
                            VALUES (:mid, :uid, True, 'confirmed')
                        """, {"mid": mid, "uid": st.session_state.user['id']})
                        
                        st.success("✅ 모임이 생성되었습니다!")
                        st.rerun()