import streamlit as st
import pandas as pd
import json
from supabase import create_client, Client
from google import genai
from google.genai import types
from PyPDF2 import PdfReader
import io

# 1. 환경 설정 및 시크릿 불러오기
try:
    SUPABASE_URL = st.secrets["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
except KeyError as e:
    st.error(f"Secret 설정이 누락되었습니다: {e}")
    st.stop()

# 클라이언트 초기화
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
client = genai.Client(api_key=GEMINI_API_KEY)

st.set_page_config(page_title="AI 과외 선생님", layout="wide")
st.title("🎓 대학교 수업 요약 & 시험 문제 제작기")

# 세션 상태 초기화
if 'current_exam' not in st.session_state:
    st.session_state.current_exam = None
if 'user_answers' not in st.session_state:
    st.session_state.user_answers = {}
if 'last_file_content' not in st.session_state:
    st.session_state.last_file_content = ""

# PDF 텍스트 추출 함수
def extract_text_from_pdf(file):
    pdf_reader = PdfReader(file)
    text = ""
    for page in pdf_reader.pages:
        extracted = page.extract_text()
        if extracted:
            text += extracted
    return text

# 탭 구성
tab1, tab2, tab3 = st.tabs(["📖 이해하기 (요약/QA)", "📝 시험문제 만들기 & 풀기", "📊 복습 라이브러리"])

# --- Tab 1: 이해하기 ---
with tab1:
    st.header("파일 분석 및 스마트 질의응답")
    
    with st.expander("📁 수업 자료 업로드 및 정보 입력", expanded=True):
        col1, col2 = st.columns(2)
        with col1:
            subject = st.text_input("과목명", placeholder="예: 데이터베이스")
            professor = st.text_input("교수명", placeholder="예: 이영희 교수님")
        with col2:
            topic = st.text_input("주제/키워드", placeholder="예: SQL 인덱스")
            uploaded_file = st.file_uploader("수업 PDF 파일", type=['pdf'])

    if st.button("AI 분석 및 요약 시작"):
        if uploaded_file and subject:
            with st.spinner("파일을 분석 중입니다..."):
                file_text = extract_text_from_pdf(uploaded_file)
                st.session_state.last_file_content = file_text # 세션에 저장
                
                # Gemini 요약 호출 (Google Search 활용)
                summary_prompt = f"다음 수업 자료({subject} - {topic})를 읽고 요약해줘. 자료 내용: {file_text[:5000]}"
                
                try:
                    # google-genai 최신 SDK 도구 설정 방식 수정
                    response = client.models.generate_content(
                        model='gemini-2.0-flash',
                        contents=summary_prompt,
                        config=types.GenerateContentConfig(
                            tools=[types.Tool(google_search_retrieval=types.GoogleSearchRetrieval())], # 명칭 수정
                            temperature=0.0
                        )
                    )
                    summary_result = response.text
                    
                    # DB 저장
                    data = {
                        "subject_name": subject,
                        "professor_name": professor,
                        "topic": topic,
                        "file_content": file_text[:10000],
                        "summary": summary_result
                    }
                    supabase.table("sm_history").insert(data).execute()
                    st.success("분석 완료! 파일 내용이 Tab 2 라이브러리에 저장되었습니다.")
                    st.info(f"**AI 요약:** {summary_result}")
                except Exception as e:
                    st.error(f"AI 호출 중 오류가 발생했습니다: {e}")
        else:
            st.warning("과목명과 파일을 모두 입력해주세요.")

    st.divider()
    st.subheader("💬 수업 내용 챗봇")
    if "messages" not in st.session_state:
        st.session_state.messages = []

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    if chat_input := st.chat_input("이 수업 자료에 대해 궁금한 점을 물어보세요!"):
        st.session_state.messages.append({"role": "user", "content": chat_input})
        with st.chat_message("user"):
            st.markdown(chat_input)

        with st.chat_message("assistant"):
            # 파일 내용을 컨텍스트로 결합하여 답변
            qa_prompt = f"수업자료 내용: {st.session_state.last_file_content[:4000]}\n\n질문: {chat_input}\n\n위 수업자료 내용을 바탕으로 답변하고, 자료에 없는 내용은 구글 검색을 활용해 보완해서 답변해줘."
            try:
                response = client.models.generate_content(
                    model='gemini-2.0-flash',
                    contents=qa_prompt,
                    config=types.GenerateContentConfig(
                        tools=[types.Tool(google_search_retrieval=types.GoogleSearchRetrieval())],
                        temperature=0.0
                    )
                )
                st.markdown(response.text)
                st.session_state.messages.append({"role": "assistant", "content": response.text})
            except Exception as e:
                st.error(f"오류가 발생했습니다: {e}")

# --- Tab 2: 시험문제 만들기 ---
with tab2:
    st.header("📝 맞춤형 시험 문제 생성")
    
    # DB 데이터 불러오기
    res = supabase.table("sm_history").select("*").order("created_at", desc=True).execute()
    items = res.data

    if items:
        search_query = st.text_input("🔍 과목명 또는 키워드로 검색", "")
        filtered_items = [i for i in items if search_query.lower() in i['subject_name'].lower() or search_query.lower() in (i['topic'] or "").lower()]
        
        if filtered_items:
            options = [f"[{i['subject_name']}] {i['topic']} ({i['created_at'][:10]})" for i in filtered_items]
            selected_option = st.selectbox("학습 자료 선택", options)
            selected_idx = options.index(selected_option)
            selected_data = filtered_items[selected_idx]

            col1, col2, col3 = st.columns(3)
            q_type = col1.selectbox("문제 유형", ["객관식 5문항", "주관식/단답형 5문항", "OX 퀴즈"])
            diff = col2.select_slider("난이도", ["쉬움", "보통", "어려움"])
            
            if st.button("시험 문제 생성 (JSON 모드)"):
                with st.spinner("문제를 생성하고 있습니다..."):
                    exam_prompt = f"""
                    다음 내용을 바탕으로 시험 문제를 만들어줘.
                    내용: {selected_data['file_content'][:4000]}
                    유형: {q_type}
                    난이도: {diff}
                    
                    반드시 아래 JSON 형식으로만 답변해:
                    [
                      {{"id": 1, "question": "문제", "options": ["보기1", "보기2", "보기3", "보기4"], "answer": "보기번호 또는 정답텍스트", "explanation": "해설"}}
                    ]
                    * 객관식이 아니면 options는 빈 리스트[]로 작성.
                    """
                    try:
                        # JSON 모드에서는 tools를 사용하지 않음 (충돌 방지)
                        exam_res = client.models.generate_content(
                            model='gemini-2.0-flash',
                            contents=exam_prompt,
                            config=types.GenerateContentConfig(
                                response_mime_type="application/json",
                                temperature=0.7
                            )
                        )
                        st.session_state.current_exam = json.loads(exam_res.text)
                        st.session_state.user_answers = {}
                        # 생성된 문제를 DB에 업데이트
                        supabase.table("sm_history").update({"generated_exam": st.session_state.current_exam}).eq("id", selected_data['id']).execute()
                    except Exception as e:
                        st.error(f"문제 생성 실패: {e}")

            # 시험 풀기 섹션
            if st.session_state.current_exam:
                st.divider()
                st.subheader("✍️ 직접 풀어보기")
                for i, q in enumerate(st.session_state.current_exam):
                    st.write(f"**{i+1}. {q['question']}**")
                    if q['options']:
                        st.session_state.user_answers[i] = st.radio(f"선택지_{i}", q['options'], key=f"ans_{i}", index=None)
                    else:
                        st.session_state.user_answers[i] = st.text_input(f"답변 입력_{i}", key=f"ans_{i}")

                if st.button("정답 확인 및 제출"):
                    score = 0
                    wrong_list = []
                    for i, q in enumerate(st.session_state.current_exam):
                        user_ans = st.session_state.user_answers.get(i)
                        if str(user_ans) == str(q['answer']):
                            st.success(f"Q{i+1}: 정답!")
                            score += 1
                        else:
                            st.error(f"Q{i+1}: 오답! (정답: {q['answer']})")
                            st.write(f"💡 해설: {q['explanation']}")
                            wrong_list.append(q['question'])
                    
                    st.metric("최종 점수", f"{score} / {len(st.session_state.current_exam)}")
                    if wrong_list:
                        new_wrongs = (selected_data.get('wrong_concepts') or "") + " | " + " / ".join(wrong_list)
                        supabase.table("sm_history").update({"wrong_concepts": new_wrongs}).eq("id", selected_data['id']).execute()

            # CSV 다운로드 버튼
            df_export = pd.DataFrame(filtered_items)
            csv = df_export.to_csv(index=False).encode('utf-8-sig')
            st.download_button("📥 학습 기록 다운로드 (CSV)", data=csv, file_name="my_study_log.csv", mime="text/csv")
    else:
        st.info("Tab 1에서 수업 파일을 먼저 업로드해 주세요.")

# --- Tab 3: 통계 및 복습 ---
with tab3:
    st.header("📊 학습 데이터 대시보드")
    res = supabase.table("sm_history").select("*").execute()
    all_data = res.data
    
    if all_data:
        df = pd.DataFrame(all_data)
        df['date'] = pd.to_datetime(df['created_at']).dt.date
        
        col_l, col_r = st.columns(2)
        with col_l:
            st.subheader("과목별 누적 학습 건수")
            subj_counts = df['subject_name'].value_counts()
            st.bar_chart(subj_counts)
        with col_r:
            st.subheader("일자별 학습 추이")
            date_counts = df.groupby('date').size()
            st.line_chart(date_counts)
            
        st.divider()
        st.subheader("🔍 집중 복습 대상 (오답 노트)")
        wrong_notes = df[df['wrong_concepts'].notna()]
        if not wrong_notes.empty:
            for _, row in wrong_notes.iterrows():
                with st.expander(f"❌ {row['subject_name']} - {row['topic']} 에서 틀린 문제"):
                    st.write(row['wrong_concepts'])
        else:
            st.write("아직 기록된 오답이 없습니다. 훌륭합니다! 🎉")
    else:
        st.write("데이터가 존재하지 않습니다.")
