import streamlit as st
import pandas as pd
import json
from supabase import create_client, Client
from google import genai
from google.genai import types
import io
from PyPDF2 import PdfReader

# 1. 환경 설정 및 시크릿 불러오기
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
client = genai.Client(api_key=GEMINI_API_KEY)

# 앱 페이지 설정
st.set_page_config(page_title="AI 과외 선생님", layout="wide")
st.title("🎓 대학교 수업 요약 & 시험 문제 제작기")

# 세션 상태 초기화 (시험 풀기용)
if 'current_exam' not in st.session_state:
    st.session_state.current_exam = None
if 'user_answers' not in st.session_state:
    st.session_state.user_answers = {}

# --- 공통 함수: PDF에서 텍스트 추출 ---
def extract_text_from_pdf(file):
    pdf_reader = PdfReader(file)
    text = ""
    for page in pdf_reader.pages:
        text += page.extract_text()
    return text

# --- 메인 화면 탭 구성 ---
tab1, tab2, tab3 = st.tabs(["📖 이해하기 (요약/QA)", "📝 시험문제 만들기 & 풀기", "📊 복습 & 라이브러리"])

# --- Tab 1: 이해하기 (파일 업로드 및 질의응답) ---
with tab1:
    st.header("파일 업로드 및 핵심 요약")
    
    with st.expander("📁 수업 자료 업로드", expanded=True):
        col1, col2 = st.columns(2)
        with col1:
            subject = st.text_input("과목명", placeholder="예: 운영체제")
            professor = st.text_input("교수명", placeholder="예: 김철수 교수님")
        with col2:
            topic = st.text_input("주제/키워드", placeholder="예: 프로세스와 스레드")
            uploaded_file = st.file_uploader("PDF 수업 자료 업로드", type=['pdf'])

    if st.button("AI 분석 시작"):
        if uploaded_file and subject:
            with st.spinner("AI가 파일을 읽고 요약 중입니다..."):
                # 텍스트 추출
                file_text = extract_text_from_pdf(uploaded_file)
                
                # Gemini 요약 (Google Search 도구 활용)
                prompt = f"다음은 {subject} 수업 자료야. 내용을 기반으로 한줄 요약을 해주고, 중요한 개념 3가지를 정리해줘. 자료내용: {file_text[:5000]}"
                
                # 2.0 Flash 모델 사용, 구글 검색 도구 포함
                response = client.models.generate_content(
                    model='gemini-2.0-flash',
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        tools=[types.Tool(google_search=types.GoogleSearchRetrieval())],
                        temperature=0.0
                    )
                )
                
                summary_text = response.text
                
                # DB 저장
                data = {
                    "subject_name": subject,
                    "professor_name": professor,
                    "topic": topic,
                    "file_content": file_text[:10000], # 용량 제한상 앞부분 저장
                    "summary": summary_text
                }
                supabase.table("sm_history").insert(data).execute()
                st.success("분석 완료 및 라이브러리에 저장되었습니다!")
                st.info(f"**AI 요약:** {summary_text}")
        else:
            st.warning("과목명과 파일을 모두 입력해주세요.")

    st.divider()
    st.subheader("💬 수업 내용 Q&A (환각 방지)")
    if "messages" not in st.session_state:
        st.session_state.messages = []

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    if prompt := st.chat_input("수업 내용 중 궁금한 점을 물어보세요!"):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            # 파일 내용을 컨텍스트로 전달
            context = "이전에 업로드된 파일 내용을 기반으로 답변해줘."
            response = client.models.generate_content(
                model='gemini-2.0-flash',
                contents=[context, prompt],
                config=types.GenerateContentConfig(temperature=0.0)
            )
            st.markdown(response.text)
            st.session_state.messages.append({"role": "assistant", "content": response.text})

# --- Tab 2: 시험문제 만들기 & 풀기 ---
with tab2:
    st.header("🧠 예상 시험 문제")
    
    # DB에서 목록 불러오기
    res = supabase.table("sm_history").select("*").order("created_at", desc=True).execute()
    db_data = res.data

    if db_data:
        options = [f"{d['subject_name']} - {d['topic']} ({d['created_at'][:10]})" for d in db_data]
        selected_option = st.selectbox("문제를 생성할 수업 자료를 선택하세요", options)
        selected_idx = options.index(selected_option)
        selected_item = db_data[selected_idx]

        col1, col2 = st.columns(2)
        q_type = col1.selectbox("문제 유형", ["객관식 5문항", "단답형 5문항"])
        difficulty = col2.select_slider("난이도", options=["쉬움", "보통", "어려움"])

        if st.button("시험 문제 생성"):
            with st.spinner("수업 자료를 바탕으로 문제를 출제 중입니다..."):
                # JSON 응답을 위해 프롬프트 구성 (Search 도구 미사용 시 JSON 가능)
                exam_prompt = f"""
                수업내용: {selected_item['file_content'][:4000]}
                위 내용을 바탕으로 {q_type}, 난이도 {difficulty}의 시험 문제를 만들어줘.
                반드시 아래 JSON 형식으로만 응답해줘:
                [
                  {{"id": 1, "question": "문제 내용", "options": ["1번", "2번", "3번", "4번"], "answer": "정답", "explanation": "해설"}},
                  ...
                ]
                """
                exam_res = client.models.generate_content(
                    model='gemini-2.0-flash',
                    contents=exam_prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.7
                    )
                )
                
                generated_questions = json.loads(exam_res.text)
                st.session_state.current_exam = generated_questions
                st.session_state.user_answers = {}
                
                # 생성된 문제를 DB에 업데이트 (나중에 다시 볼 수 있게)
                supabase.table("sm_history").update({"generated_exam": generated_questions}).eq("id", selected_item['id']).execute()

        # 시험 풀기 UI
        if st.session_state.current_exam:
            st.divider()
            st.subheader("✍️ 테스트 진행")
            score = 0
            for i, q in enumerate(st.session_state.current_exam):
                st.write(f"**Q{i+1}. {q['question']}**")
                if "options" in q:
                    st.session_state.user_answers[i] = st.radio(f"선택지 {i}", q['options'], key=f"q{i}")
                else:
                    st.session_state.user_answers[i] = st.text_input(f"답변 입력 {i}", key=f"q{i}")

            if st.button("제출 및 채점"):
                wrong_concepts = []
                for i, q in enumerate(st.session_state.current_exam):
                    is_correct = st.session_state.user_answers[i] == q['answer']
                    if is_correct:
                        st.success(f"Q{i+1}: 정답입니다!")
                        score += 1
                    else:
                        st.error(f"Q{i+1}: 오답입니다. (정답: {q['answer']})")
                        st.info(f"💡 해설: {q['explanation']}")
                        wrong_concepts.append(q['question'])
                
                st.balloons()
                st.metric("최종 점수", f"{score} / {len(st.session_state.current_exam)}")
                
                # 틀린 개념 저장
                if wrong_concepts:
                    existing_wrong = selected_item.get('wrong_concepts') or ""
                    new_wrong = existing_wrong + " | " + " / ".join(wrong_concepts)
                    supabase.table("sm_history").update({"wrong_concepts": new_wrong}).eq("id", selected_item['id']).execute()

        # CSV 다운로드
        df = pd.DataFrame(db_data)
        csv = df.to_csv(index=False).encode('utf-8-sig')
        st.download_button("학습 기록 CSV 다운로드", data=csv, file_name="study_history.csv", mime="text/csv")
    else:
        st.info("먼저 [이해하기] 탭에서 파일을 업로드해주세요.")

# --- Tab 3: 복습 & 라이브러리 (통계) ---
with tab3:
    st.header("📊 학습 통계 및 복습 퀴즈")
    
    res = supabase.table("sm_history").select("*").execute()
    data = res.data
    
    if data:
        df = pd.DataFrame(data)
        df['created_at'] = pd.to_datetime(df['created_at']).dt.date
        
        col1, col2 = st.columns(2)
        with col1:
            st.subheader("과목별 학습 횟수")
            subject_counts = df['subject_name'].value_counts()
            st.bar_chart(subject_counts)
            
        with col2:
            st.subheader("날짜별 열공 기록")
            date_counts = df.groupby('created_at').size()
            st.line_chart(date_counts)
            
        st.divider()
        st.subheader("⚠️ 오답 노트 기반 복습")
        wrong_data = df[df['wrong_concepts'].notna()]
        if not wrong_data.empty:
            selected_wrong = st.selectbox("복습할 과목 선택", wrong_data['subject_name'].unique())
            concepts = wrong_data[wrong_data['subject_name'] == selected_wrong]['wrong_concepts'].iloc[0]
            st.warning(f"최근 틀린 개념: {concepts}")
            
            if st.button("복습 퀴즈 생성"):
                st.info("Gemini가 틀린 개념을 바탕으로 새로운 문제를 구성 중입니다... (기능 확장 예정)")
        else:
            st.write("아직 틀린 문제가 없습니다. 완벽해요! 👍")
    else:
        st.write("데이터가 없습니다.")