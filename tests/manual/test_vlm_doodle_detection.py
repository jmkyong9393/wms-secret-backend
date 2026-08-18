import os
import base64
from pathlib import Path
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

load_dotenv()

img_path = Path(r'E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-secret-backend\app\experiment_data\job-f309b042\raw_3.jpg')

def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

if img_path.exists():
    b64_img = encode_image(img_path)
    
    llm_vlm = ChatOpenAI(model="gpt-4o", temperature=0.1)
    
    prompt = """당신은 WMS 도서 물류센터의 객관적 AI 비전 관찰관입니다.
이 도서 이미지에서 관찰되는 물리적 상태(마모, 찢김, 낙서/손글씨 필기, 도장, 오염 등)를 정밀하게 분석해 주세요.
특히 '낙서(Doodle/Writing)', '친필 서명', '도서관 도장' 등이 관찰되는지 상세히 서술해 주세요."""

    message = HumanMessage(
        content=[
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}
            }
        ]
    )
    
    print("[Testing GPT-4o Vision VLM Detection on raw_3.jpg...]")
    response = llm_vlm.invoke([message])
    print("\n--- GPT-4o VLM Analysis Result ---")
    print(response.content)
