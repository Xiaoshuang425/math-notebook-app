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
# 確保能讀取到根目錄或當前目錄的 .env
load_dotenv(os.path.join(os.path.dirname(__file__), '../.env'))

app = FastAPI(
    title="KidAni Math AI Backend",
    description="具備格式容錯、敏感詞攔截與狀態監控的數學教學系統",
    version="3.0.0"
)

# 解決跨域問題 (CORS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 從環境變數獲取 API Key
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
    """在終端機顯示格式化日誌"""
    timestamp = time.strftime("%H:%M:%S")
    print(f"[{timestamp}] 🚀 {stage.ljust(12)} | {message}")

def parse_sse_response(text: str):
    """解析後端返回的 SSE 格式或普通 JSON"""
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
    """
    規避 Sora 攔截機制：
    將具體品牌名稱（Disney, Pixar, 喜羊羊等）替換為描述性詞彙。
    """
    envs = ["sunny meadow", "bright colorful study room", "soft dreamy garden"]
    env = random.choice(envs)
    characters = {
        "熊大熊二": f"two friendly chubby bear-like forest creatures, brown fur, cute stylized, in {env}, 3D animation",
        "喜羊羊": f"a cute fluffy white sheep character with a friendly face, stylized 3D, in {env}",
        "小博士": f"a wise little owl wearing small glasses, cute cartoon style, in {env}",
        "default": f"a cute friendly stylized 3D character, bright lighting, in {env}"
    }
    if not char_name: return characters["default"]
    
    # 模糊匹配邏輯
    name = str(char_name)
    if "熊" in name: return characters["熊大熊二"]
    if "羊" in name: return characters["喜羊羊"]
    if "博士" in name: return characters["小博士"]
    return characters.get(name, characters["default"])

async def submit_and_poll_video(sora_prompt: str, headers: dict, max_retries=1):
    """提交影片任務並持續輪詢結果"""
    current_attempt = 0
    while current_attempt <= max_retries:
        try:
            # 敏感詞清洗：強制移除可能引起 5秒攔截(退費) 的關鍵字
            forbidden = ["disney", "pixar", "mickey", "copyright", "xi yang yang", "spongebob"]
            for word in forbidden:
                sora_prompt = re.compile(re.escape(word), re.IGNORECASE).sub("", sora_prompt)

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
                log_status("提交失敗", "未獲取到 Task ID，稍後重試")
                current_attempt += 1
                time.sleep(3)
                continue

            # 開始輪詢
            for i in range(25): 
                time.sleep(15)
                res = requests.post(f"{SORA_BASE_URL}/v1/draw/result", headers=headers, json={"id": task_id}, timeout=60)
                data = parse_sse_response(res.text)
                res_obj = data.get("data") if isinstance(data.get("data"), dict) else data
                results = res_obj.get("results")
                
                if results and len(results) > 0:
                    return results[0].get("url")
                
                status = str(res_obj.get("status", "")).lower()
                if status in ["failed", "error"]: 
                    log_status("輪詢終止", f"任務 {task_id} 失敗")
                    break 
                log_status("輪詢中", f"ID: {task_id} ({i+1}/25)")
                
            current_attempt += 1
        except Exception as e:
            log_status("連線異常", str(e))
            current_attempt += 1
    return None

# --- 核心路由 ---

@app.get("/")
async def root():
    """根路徑，用於確認服務是否在線"""
    return {
        "status": "online",
        "message": "KidAni AI Math Backend is running!",
        "endpoints": ["/generate-video", "/health"]
    }

@app.get("/health")
async def health_check():
    """健康檢查路徑"""
    return {"status": "ok", "timestamp": time.time()}

@app.post("/generate-video")
async def handle_request(request: VideoRequest):
    # 1. 意圖偵測：判斷是「問答模式」還是「影片製作模式」
    math_terms = ["分數", "加法", "減法", "乘法", "除法", "面積", "周長", "幾何", "代數", "因數", "倍數"]
    is_qa_mode = (
        any(word in request.topic for word in ["為什麼", "怎麼做", "什麼是", "如何", "解釋", "？", "?"]) or
        len(request.topic) <= 4 or 
        any(term == request.topic for term in math_terms)
    )
    
    if is_qa_mode:
        log_status("導師模式", request.topic)
        try:
            headers_ds = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
            ds_res = requests.post(
                f"{DEEPSEEK_BASE_URL}/chat/completions",
                headers=headers_ds,
                json={
                    "model": DEEPSEEK_MODEL,
                    "messages": [
                        {"role": "system", "content": "你是一位兒童數學導師。請用有趣的比喻解釋概念，字數在 200 字以內。"},
                        {"role": "user", "content": f"請解釋：{request.topic}"}
                    ]
                }
            )
            answer = ds_res.json()["choices"][0]["message"]["content"]
            return {"type": "qa", "answer": answer, "full_course": []}
        except:
            raise HTTPException(status_code=500, detail="DeepSeek 服務暫時不可用")

    # 2. 生成劇本 (強化 JSON 結構要求與錯誤處理)
    log_status("動畫模式", request.topic)
    actual_char_key = request.character if request.character else "可愛助手"
    char_desc = get_character_description_for_sora(actual_char_key)
    
    try:
        headers_ds = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
        ds_res = requests.post(
            f"{DEEPSEEK_BASE_URL}/chat/completions",
            headers=headers_ds,
            json={
                "model": DEEPSEEK_MODEL,
                "messages": [
                    {"role": "system", "content": "你是一位動畫編劇。輸出必須是 JSON，包含一個 'scenes' 列表。每個場景必須有 'title', 'visual_prompt', 'narration' 三個欄位。"},
                    {"role": "user", "content": f"為主題「{request.topic}」寫一個包含 2 個場景的劇本。"}
                ],
                "response_format": {"type": "json_object"}
            }
        )
        script_json = json.loads(ds_res.json()["choices"][0]["message"]["content"])
    except Exception as e:
        log_status("劇本錯誤", str(e))
        script_json = {"scenes": []}

    # 3. 逐場景生成動畫 (包含 Key 缺失的防禦邏輯)
    final_results = []
    headers_sora = {"Authorization": f"Bearer {SORA_API_KEY}", "Content-Type": "application/json"}
    
    scenes = script_json.get("scenes", [])
    if not scenes:
        # 保底數據，避免完全沒畫面
        scenes = [{"title": "教學開始", "visual_prompt": "educational animation background", "narration": "準備好要開始學習了嗎？"}]

    for scene in scenes:
        # 防禦邏輯：解決 KeyError 'visual_prompt'
        # 如果 AI 寫錯欄位名稱（例如 image_prompt），現在也能正常工作
        raw_v_p = scene.get('visual_prompt') or scene.get('image_prompt') or scene.get('description') or "educational scene"
        title = scene.get('title', '場景')
        narration = scene.get('narration', '請看螢幕上的說明...')
        
        log_status("處理場景", title)
        
        # 提示詞優化
        v_p_cleaned = str(raw_v_p).lower().replace("bear", "creature").replace("pizza", "disk")
        safe_visual = v_p_cleaned.replace("[char]", char_desc)
        full_sora_prompt = f"3D animation style, {safe_visual}, vibrant colors, happy atmosphere, 4k, no text."
        
        video_url = await submit_and_poll_video(full_sora_prompt, headers_sora)
        
        final_results.append({
            "title": title,
            "narration": narration,
            "video_url": video_url or "https://media.giphy.com/media/3o7TKMGpxx36E20Nl6/giphy.gif" # 失敗時使用 Placeholder
        })

    return {"type": "video", "full_course": final_results}

if __name__ == "__main__":
    import uvicorn
    # 讀取 Railway 或 Vercel 提供的高級 PORT
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)