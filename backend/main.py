import os
import json
import time
import asyncio
import httpx
import re
from typing import List, Optional
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

# --- 初始化與配置 ---
load_dotenv()

app = FastAPI(
    title="KidAni Math AI Studio",
    version="4.5.3",
    description="具備穩定 SSE 解析與純中文音軌權重的 AI 數學動畫工作室"
)

# 解決跨域問題 - Render 部署後，前端 Vercel 訪問必須靠這個
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()
SORA_API_KEY = os.environ.get("SORA_API_KEY", "").strip()
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
SORA_BASE_URL = "https://grsai.dakka.com.cn"

# 任務儲存字典
task_results = {}

class VideoRequest(BaseModel):
    topic: str
    character: Optional[str] = "可愛助手"
    style: Optional[str] = "3D"

# --- 核心工具函數 ---

def clean_prompt_for_safety(prompt: str) -> str:
    """過濾敏感詞，避免第三方 API 攔截"""
    forbidden = ["politics", "bloody", "violence", "sexy"]
    for word in forbidden:
        prompt = re.sub(word, "", prompt, flags=re.IGNORECASE)
    return prompt

def get_character_desc(name: str):
    """角色映射邏輯"""
    mapping = {
        "熊大熊二": "two friendly brown bears, 3D Disney Pixar style, high quality textures",
        "喜羊羊": "a cute white sheep with a golden bell, 3D animated style, fluffy wool",
        "小博士": "a wise little owl wearing large glasses and a graduation cap, 3D stylized"
    }
    return mapping.get(name, "a cute 3D educational cartoon character")

def extract_id_from_sse(raw_text: str) -> Optional[str]:
    """專門處理第三方 API 奇怪的 SSE 格式"""
    lines = raw_text.split('\n')
    for line in lines:
        line = line.strip()
        if not line: continue
        
        content = line
        if line.startswith("data:"):
            content = line.replace("data:", "", 1).strip()
        
        try:
            data = json.loads(content)
            job_id = data.get("id") or (data.get("data") and data.get("data").get("id"))
            if job_id: return str(job_id)
        except:
            continue
    return None

async def poll_video_url(task_id: str, headers: dict):
    """靈敏輪詢：具備容錯解析與狀態追蹤"""
    print(f">>> 進入輪詢階段 [ID: {task_id}]")
    async with httpx.AsyncClient(timeout=30.0) as client:
        for i in range(120): # 最多等 20 分鐘
            await asyncio.sleep(10)
            try:
                res = await client.post(
                    f"{SORA_BASE_URL}/v1/draw/result", 
                    headers=headers, 
                    json={"id": task_id}
                )
                
                if res.status_code == 200:
                    raw_text = res.text.strip()
                    lines = raw_text.split('\n')
                    for line in lines:
                        content = line.strip()
                        if content.startswith("data:"):
                            content = content.replace("data:", "", 1).strip()
                        try:
                            data = json.loads(content)
                            res_obj = data.get("data") if isinstance(data.get("data"), dict) else data
                            results = res_obj.get("results")
                            
                            if results and len(results) > 0:
                                url = results[0].get('url')
                                if url: 
                                    print(f"✅ 動畫生成完畢: {url}")
                                    return url
                            
                            status = str(res_obj.get("status", "")).lower()
                            if status in ["waiting", "processing", "pending", "running", "none"]:
                                if i % 3 == 0: print(f"⏳ 任務 {task_id} 狀態: {status}...")
                                break
                            if status in ["failed", "error"]:
                                return None
                        except:
                            continue
            except Exception as e:
                print(f"⚠️ 輪詢異常 (Task {task_id}): {e}")
                continue
    return None

async def background_generate_course(request: VideoRequest, internal_task_id: str):
    """背景執行緒：全功能流水線 (強化：純中文語音鎖定)"""
    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            print(f"🚀 開始製作純中文課程: {request.topic}")
            task_results[internal_task_id] = {"status": "processing", "message": "正在規劃純中文教學劇本..."}
            
            # 1. 使用 DeepSeek 生成劇本
            headers_ds = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
            ds_payload = {
                "model": "deepseek-chat",
                "messages": [
                    {
                        "role": "system", 
                        "content": (
                            "你是一位專業的兒童數學老師。請生成 JSON 格式的劇本。劇本包含 'scenes' 列表，"
                            "每個場景有 'title' (標題), 'visual_prompt' (英文視覺描述，不含敏感詞), "
                            "'narration' (旁白)。【重要限制】：旁白必須完全使用繁體中文，絕對禁止出現任何英文字母。"
                        )
                    },
                    {"role": "user", "content": f"請為 6 歲孩子製作一堂關於『{request.topic}』的課。請用中文生成 2 個核心場景，不要有任何英語。"}
                ],
                "response_format": {"type": "json_object"}
            }
            
            ds_res = await client.post(f"{DEEPSEEK_BASE_URL}/chat/completions", headers=headers_ds, json=ds_payload)
            script_data = json.loads(ds_res.json()["choices"][0]["message"]["content"])
            scenes = script_data.get("scenes", [])

            # 2. 依次提交 Sora 任務
            final_course = []
            headers_sora = {"Authorization": f"Bearer {SORA_API_KEY}", "Content-Type": "application/json"}
            char_desc = get_character_desc(request.character)

            for idx, scene in enumerate(scenes):
                # 這裡加入 "video with Chinese audio only" 指令
                raw_prompt = (
                    f"{request.style} animation, {char_desc}, {scene['visual_prompt']}, "
                    f"Chinese environment, video with Chinese audio only, no English speech, "
                    f"educational video, high quality."
                )
                safe_prompt = clean_prompt_for_safety(raw_prompt)
                
                task_results[internal_task_id].update({
                    "progress": f"{idx}/{len(scenes)}",
                    "message": f"正在製作純中文場景 {idx+1}: {scene['title']}..."
                })
                
                sora_job_id = None
                video_url = None

                for attempt in range(3):
                    try:
                        submit_res = await client.post(
                            f"{SORA_BASE_URL}/v1/video/sora-video",
                            headers=headers_sora,
                            json={"model": "sora-2", "prompt": safe_prompt},
                            timeout=180.0
                        )
                        raw_text = submit_res.text.strip()
                        if not raw_text or "<html>" in raw_text.lower(): continue

                        sora_job_id = extract_id_from_sse(raw_text)
                        if sora_job_id: break
                    except Exception as e:
                        print(f"⚠️ 提交失敗: {e}")
                    await asyncio.sleep(5)

                if sora_job_id:
                    video_url = await poll_video_url(sora_job_id, headers_sora)
                
                final_course.append({
                    "title": scene.get("title", f"場景 {idx+1}"),
                    "narration": scene.get("narration", "正在準備內容..."),
                    "video_url": video_url or "https://media.giphy.com/media/3o7TKMGpxx36E20Nl6/giphy.gif"
                })

            task_results[internal_task_id] = {
                "status": "completed", 
                "data": final_course,
                "message": "純中文動畫課程製作完成！"
            }
            
        except Exception as e:
            task_results[internal_task_id] = {"status": "error", "message": f"製作過程錯誤: {str(e)}"}

# --- API 路由 ---

@app.route("/health", methods=["GET", "HEAD"])
async def health():
    return {"status": "online", "time": time.time()}

@app.post("/generate-video")
async def generate_video(request: VideoRequest, background_tasks: BackgroundTasks):
    internal_id = f"task_{int(time.time())}"
    task_results[internal_id] = {"status": "processing", "message": "任務已啟動"}
    background_tasks.add_task(background_generate_course, request, internal_id)
    return {"status": "queued", "task_id": internal_id}

@app.get("/task-status/{task_id}")
async def get_task_status(task_id: str):
    return task_results.get(task_id, {"status": "not_found"})

if __name__ == "__main__":
    import uvicorn
    # Render 會自動分配端口，所以我們用 os.environ.get("PORT")
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port, timeout_keep_alive=60)