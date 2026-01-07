import os
import json
import requests
import time
import asyncio
from typing import List, Optional
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

# --- 初始化與配置 ---
load_dotenv()

app = FastAPI(
    title="KidAni Math AI Backend",
    version="3.5.0"
)

# 強化 CORS 設定
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API 配置
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()
SORA_API_KEY = os.environ.get("SORA_API_KEY", "").strip()
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
SORA_BASE_URL = "https://grsai.dakka.com.cn"

# 全域任務狀態存儲 (生產環境建議使用 Redis)
task_results = {}

class VideoRequest(BaseModel):
    topic: str
    character: Optional[str] = "可愛助手"
    style: Optional[str] = "3D"

# --- 輔助工具 ---

def get_character_desc(name: str):
    mapping = {
        "熊大熊二": "two friendly brown bears, 3D cartoon style, cute faces",
        "喜羊羊": "a cute white sheep with a bell, 3D animated style",
        "小博士": "a wise little owl with glasses, 3D stylized"
    }
    return mapping.get(name, "a cute 3D educational character")

async def poll_video_url(task_id: str, headers: dict):
    """向 Sora API 輪詢影片結果"""
    for i in range(40):  # 最多等待約 6-7 分鐘
        await asyncio.sleep(10)
        try:
            res = requests.post(
                f"{SORA_BASE_URL}/v1/draw/result", 
                headers=headers, 
                json={"id": task_id}, 
                timeout=15
            )
            data = res.json()
            # 兼容不同 API 回傳格式
            res_obj = data.get("data") if isinstance(data.get("data"), dict) else data
            results = res_obj.get("results")
            
            if results and len(results) > 0:
                return results[0].get("url")
            
            status = str(res_obj.get("status", "")).lower()
            if status in ["failed", "error"]:
                return None
        except:
            continue
    return None

async def background_generate_course(request: VideoRequest, internal_task_id: str):
    """核心背景任務：生成劇本並調用 Sora"""
    try:
        # 1. 使用 DeepSeek 生成劇本
        headers_ds = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
        ds_payload = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": "You are a math tutor scriptwriter. Output JSON with 'scenes' (title, visual_prompt, narration)."},
                {"role": "user", "content": f"Create a 2-scene math lesson about {request.topic}."}
            ],
            "response_format": {"type": "json_object"}
        }
        
        ds_res = requests.post(f"{DEEPSEEK_BASE_URL}/chat/completions", headers=headers_ds, json=ds_payload, timeout=30)
        script_data = ds_res.json()["choices"][0]["message"]["content"]
        scenes = json.loads(script_data).get("scenes", [])

        # 2. 遍歷場景生成影片
        final_course = []
        headers_sora = {"Authorization": f"Bearer {SORA_API_KEY}", "Content-Type": "application/json"}
        char_desc = get_character_desc(request.character)

        for scene in scenes:
            prompt = f"{request.style} animation, {char_desc}, {scene['visual_prompt']}, vibrant colors."
            
            # 提交影片任務
            submit_res = requests.post(
                f"{SORA_BASE_URL}/v1/video/sora-video",
                headers=headers_sora,
                json={"model": "sora-2", "prompt": prompt},
                timeout=30
            )
            sora_task_id = submit_res.json().get("id")
            
            video_url = None
            if sora_task_id:
                video_url = await poll_video_url(sora_task_id, headers_sora)
            
            final_course.append({
                "title": scene.get("title", "教學場景"),
                "narration": scene.get("narration", "正在學習中..."),
                "video_url": video_url or "https://media.giphy.com/media/3o7TKMGpxx36E20Nl6/giphy.gif"
            })
        
        task_results[internal_task_id] = {"status": "completed", "data": final_course}
        
    except Exception as e:
        task_results[internal_task_id] = {"status": "error", "message": str(e)}

# --- 路由 ---

@app.get("/health")
async def health():
    return {"status": "online", "api_configured": bool(DEEPSEEK_API_KEY and SORA_API_KEY)}

@app.post("/generate-video")
async def generate_video(request: VideoRequest, background_tasks: BackgroundTasks):
    if not DEEPSEEK_API_KEY or not SORA_API_KEY:
        raise HTTPException(status_code=500, detail="API Key 尚未配置")

    # 建立唯一的任務 ID
    task_id = f"task_{int(time.time())}_{request.topic[:5]}"
    task_results[task_id] = {"status": "processing"}
    
    # 將任務丟到背景執行，避免 Request 超時
    background_tasks.add_task(background_generate_course, request, task_id)
    
    return {"status": "queued", "task_id": task_id}

@app.get("/task-status/{task_id}")
async def get_task_status(task_id: str):
    status = task_results.get(task_id)
    if not status:
        raise HTTPException(status_code=404, detail="找不到該任務")
    return status

if __name__ == "__main__":
    import uvicorn
    # 本地測試時使用，Render 部署時會由其啟動指令控管
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))