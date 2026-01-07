import os
import json
import requests
import time
import re
import random
from typing import List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from fastapi.responses import JSONResponse

# --- 初始化與配置 ---
load_dotenv()
load_dotenv(os.path.join(os.path.dirname(__file__), '../.env'))

app = FastAPI(
    title="KidAni Math AI Backend",
    description="具備智慧意圖偵測與導師模式的數學教學系統",
    version="2.8.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
SORA_API_KEY = os.environ.get("SORA_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-chat" 
SORA_BASE_URL = "https://grsai.dakka.com.cn"

class VideoRequest(BaseModel):
    topic: str
    style: str 
    character: Optional[str] = None 
    duration_minutes: int = 1

# --- 輔助工具函式 ---

def log_status(stage: str, message: str):
    timestamp = time.strftime("%H:%M:%S")
    print(f"[{timestamp}] 🚀 {stage.ljust(12)} | {message}")

def parse_sse_response(text: str):
    if not text or not text.strip(): return {}
    try:
        return json.loads(text)
    except:
        pass
    lines = [line.strip() for line in text.strip().split('\n') if line.strip()]
    for line in reversed(lines):
        if line.startswith('data: '):
            try: 
                content = line[6:].strip()
                return json.loads(content)
            except: 
                continue
    return {}

def get_character_description_for_sora(char_name: str):
    envs = ["sunny playground", "bright colorful room", "soft dream-like forest"]
    env = random.choice(envs)
    characters = {
        "熊大熊二": f"two friendly chubby anthropomorphic forest creatures, soft textures, cute stylized, in {env}, 3D animation",
        "喜羊羊": f"a cute stylized white fluffy creature with a friendly face, in {env}, 3D animated",
        "小博士": f"a small adorable wise owl with glasses, in {env}, Pixar style",
        "default": f"a cute stylized 3D character in {env}"
    }
    if not char_name: return characters["default"]
    if "熊" in char_name: return characters["熊大熊二"]
    if "羊" in char_name: return characters["喜羊羊"]
    if "博士" in char_name: return characters["小博士"]
    return characters.get(char_name, characters["default"])

async def submit_and_poll_video(sora_prompt: str, headers: dict, max_retries=1):
    current_attempt = 0
    while current_attempt <= max_retries:
        try:
            log_status("影片提交", f"嘗試第 {current_attempt + 1} 次...")
            submit_res = requests.post(
                f"{SORA_BASE_URL}/v1/video/sora-video", 
                headers=headers, 
                json={"model": "sora-2", "prompt": sora_prompt}, 
                timeout=120 
            )
            task_data = parse_sse_response(submit_res.text)
            task_id = task_data.get("id")
            if not task_id:
                current_attempt += 1
                time.sleep(3)
                continue
            for i in range(20): 
                time.sleep(15)
                res = requests.post(f"{SORA_BASE_URL}/v1/draw/result", headers=headers, json={"id": task_id}, timeout=60)
                data = parse_sse_response(res.text)
                res_obj = data.get("data") if isinstance(data.get("data"), dict) else data
                results = res_obj.get("results")
                if results and len(results) > 0:
                    return results[0].get("url")
                status = str(res_obj.get("status", "")).lower()
                if status in ["failed", "error"]: break 
                log_status("輪詢中", f"ID: {task_id} ({i+1}/20)")
            current_attempt += 1
        except:
            current_attempt += 1
    return None

# --- 核心路由 ---

@app.get("/health")
async def health_check():
    return {"status": "alive", "timestamp": time.time()}

@app.post("/generate-video")
async def handle_request(request: VideoRequest):
    # 1. 強化意圖偵測 (智慧攔截)
    # 定義專有名詞清單
    math_terms = ["分數", "加法", "減法", "乘法", "除法", "面積", "周長", "幾何", "代數", "因數", "倍數"]
    
    # 判斷條件：包含疑問詞 OR 字數太少(可能是術語) OR 命中專有名詞清單
    is_qa_mode = (
        any(word in request.topic for word in ["為什麼", "怎麼做", "什麼是", "如何", "解釋", "意思", "教我", "？", "?"]) or
        len(request.topic) <= 4 or 
        any(term == request.topic for term in math_terms)
    )
    
    # 模式一：導師解題模式
    if is_qa_mode:
        log_status("導師模式", f"智能攔截/解答: {request.topic}")
        tutor_system_prompt = """你是一位世界級的兒童數學導師。
        你的任務：當老師輸入一個專有名詞或題目時，用最有趣的比喻（如：分糖果、披薩）解釋它。
        規範：禁止死板定義，語氣要親切。全程繁體中文。"""
        
        try:
            headers_ds = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
            ds_res = requests.post(
                f"{DEEPSEEK_BASE_URL}/chat/completions",
                headers=headers_ds,
                json={
                    "model": DEEPSEEK_MODEL,
                    "messages": [
                        {"role": "system", "content": tutor_system_prompt},
                        {"role": "user", "content": f"請解釋這個數學概念：{request.topic}"}
                    ]
                },
                timeout=60
            )
            answer = ds_res.json()["choices"][0]["message"]["content"]
            return {"type": "qa", "answer": answer, "full_course": []}
        except Exception as e:
            raise HTTPException(status_code=500, detail="導師離線中")

    # 模式二：動畫生成模式
    log_status("動畫模式", f"製作劇本: {request.topic}")
    actual_char_key = request.character if request.character and request.character.strip() else "可愛助手"
    char_desc = get_character_description_for_sora(actual_char_key)
    
    # 這裡的提示詞改為要求 AI 「將概念故事化」
    script_prompt = f"將主題「{request.topic}」寫成一個冒險故事。主角是 {actual_char_key}。輸出 JSON 包含 scenes。"

    try:
        headers_ds = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
        ds_res = requests.post(
            f"{DEEPSEEK_BASE_URL}/chat/completions",
            headers=headers_ds,
            json={
                "model": DEEPSEEK_MODEL,
                "messages": [
                    {"role": "system", "content": "你是一位動畫編劇，擅長將冷冰冰的數學變成好玩的故事場景。"},
                    {"role": "user", "content": script_prompt}
                ],
                "response_format": {"type": "json_object"}
            },
            timeout=60
        )
        script_json = json.loads(ds_res.json()["choices"][0]["message"]["content"])
    except:
        script_json = {"scenes": [{"title": "教學", "visual_prompt": "cartoon style math", "narration": "數學時間到囉！"}]}

    final_results = []
    headers_sora = {"Authorization": f"Bearer {SORA_API_KEY}", "Content-Type": "application/json"}
    
    for scene in script_json.get("scenes", []):
        log_status("處理場景", scene['title'])
        v_p = scene['visual_prompt'].lower().replace("bear", "creature").replace("pizza", "disk")
        safe_visual = v_p.replace("[char]", char_desc)
        full_sora_prompt = f"3D Disney style animation, {safe_visual}, vibrant colors, happy, no text."
        video_url = await submit_and_poll_video(full_sora_prompt, headers_sora)
        final_results.append({
            "title": scene["title"],
            "narration": scene["narration"],
            "video_url": video_url or "https://media.giphy.com/media/3o7TKMGpxx36E20Nl6/giphy.gif"
        })

    return {"type": "video", "full_course": final_results}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)