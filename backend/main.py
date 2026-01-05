import os
import json
import requests
import time
import re
from typing import List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

# --- API 配置 ---
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY") 
SORA_API_KEY = os.environ.get("SORA_API_KEY") 
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-chat" 
SORA_BASE_URL = "https://grsai.dakka.com.cn" 

app = FastAPI(title="Math Notebook AI Backend")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class VideoRequest(BaseModel):
    topic: str
    style: str 
    character: Optional[str] = None 
    duration_minutes: int

# --- 工具函數 ---
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

def call_deepseek_api(prompt: str):
    if not DEEPSEEK_API_KEY: return "API Key 未配置。"
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": DEEPSEEK_MODEL, "messages": [
        {"role": "system", "content": "你是一位專業的卡通編劇與數學老師。你擅長編寫生動、有角色動作細節且對白豐富的劇本。你必須「全程使用繁體中文」。絕對禁止輸出任何英文對白或旁白。"},
        {"role": "user", "content": prompt}
    ]}
    try:
        response = requests.post(f"{DEEPSEEK_BASE_URL}/chat/completions", headers=headers, json=payload, timeout=60)
        return response.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"服務暫時不可用: {str(e)}"

def get_style_prompt(style_name: str):
    styles = {
        "Disney 3D Animation Style": "Disney Pixar 3D style animation, cinematic lighting, vibrant colors, expressive character faces, highly detailed.",
        "Anime Style": "Modern Japanese anime style, clean line art, beautiful hand-painted backgrounds.",
        "Cinematic Movie": "Realistic cinematic movie style, 4k photorealistic, high contrast."
    }
    return styles.get(style_name, styles["Disney 3D Animation Style"])

def get_character_description_for_sora(char_name: str):
    """
    使用視覺特徵描述避開版權攔截。
    """
    characters = {
        "熊大熊二": "two friendly anthropomorphic brown bears, one tall and strong, one chubby and cute, stylized 3D animation, no branded logos",
        "喜羊羊": "a cute stylized white sheep with a small bell around its neck, large expressive eyes, 3D animated character",
        "小博士": "a small adorable owl wearing large round glasses and a black graduation cap, 3D stylized",
        "default": "a friendly and cute 3D cartoon animal teacher"
    }
    if not char_name: return characters["default"]
    if "熊" in char_name: return characters["熊大熊二"]
    if "羊" in char_name: return characters["喜羊羊"]
    if "博士" in char_name: return characters["小博士"]
    return characters["default"]

async def submit_and_poll_video(sora_prompt: str, headers: dict, max_retries=2):
    """封裝提交與輪詢邏輯，加入自動重試機制"""
    current_attempt = 0
    while current_attempt <= max_retries:
        try:
            submit_res = requests.post(
                f"{SORA_BASE_URL}/v1/video/sora-video", 
                headers=headers, 
                json={"model": "sora-2", "prompt": sora_prompt}, 
                timeout=60
            )
            task_id = parse_sse_response(submit_res.text).get("id")
            
            if not task_id:
                print(f"提交失敗，嘗試第 {current_attempt + 1} 次重試...")
                current_attempt += 1
                continue

            for i in range(25):
                time.sleep(12)
                res = requests.post(f"{SORA_BASE_URL}/v1/draw/result", headers=headers, json={"id": task_id}, timeout=30)
                data = parse_sse_response(res.text)
                res_obj = data.get("data") if isinstance(data.get("data"), dict) else data
                results = res_obj.get("results")
                
                if results and len(results) > 0:
                    return results[0].get("url")
                
                status = str(res_obj.get("status", "")).lower()
                if status in ["failed", "error"]:
                    print(f"檢測到違規或生成失敗 (ID: {task_id})，自動觸發重試...")
                    break 
                print(f"任務 {task_id} 狀態: {status} (第 {i+1} 次輪詢)")
            
            current_attempt += 1
        except Exception as e:
            print(f"連線異常: {e}")
            current_attempt += 1
            
    return None

@app.post("/generate-video")
async def generate_video(request: VideoRequest):
    if "學生提問" in request.topic or request.style == "chat":
        prompt = f"請用繁體中文回答以下問題：{request.topic}"
        answer = call_deepseek_api(prompt)
        return {"original_script": answer, "full_course": []}

    actual_char_key = request.character if request.character and request.character.strip() else "熊大熊二"
    char_visual_for_sora = get_character_description_for_sora(actual_char_key)
    
    # 修改：要求視覺描述集中於數學數字與公式，而非漢字
    split_prompt = f"""
    請針對數學主題「{request.topic}」編寫一個分為三段的「動畫教學劇本」。
    
    【主角】：{actual_char_key}
    【語言限制】：全程繁體中文，禁止出現任何英文對白。
    
    JSON 格式：
    {{
      "scenes": [
        {{
          "title": "...",
          "visual_prompt": "[char] teaching math, focus on big floating math numbers and equations, no chinese characters on chalkboard, only numbers",
          "narration": "..."
        }}
      ]
    }}
    """
    
    raw_script = call_deepseek_api(split_prompt)
    try:
        clean_json = re.sub(r'```json|```', '', raw_script).strip()
        script_data = json.loads(clean_json)
    except:
        script_data = {"scenes": [{"title": "教學片段", "visual_prompt": "cartoon character teaching numbers", "narration": raw_script}]}

    final_results = []
    headers = {"Authorization": f"Bearer {SORA_API_KEY}", "Content-Type": "application/json"}
    style_prompt = get_style_prompt(request.style)

    for scene in script_data.get("scenes", []):
        safe_visual = scene['visual_prompt'].replace("[char]", char_visual_for_sora)
        # 修正：移除對漢字的要求，強化對數字的要求
        sora_prompt = f"{style_prompt} {safe_visual}. High quality, 4k, no text, focus on mathematical numbers. 中文影片請用中文生成。"
        
        print(f"正在提交場景任務: {scene['title']}")
        video_url = await submit_and_poll_video(sora_prompt, headers)
        
        final_results.append({
            "title": scene["title"],
            "narration": scene["narration"],
            "video_url": video_url
        })

    return {"full_course": final_results, "original_script": raw_script}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)