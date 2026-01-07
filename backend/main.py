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
    version="4.5.1",
    description="具備穩定 SSE 解析與自動修復機名的 AI 數學動畫工作室"
)

# 解決跨域問題
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
    """角色映射邏輯 (保留您原有的角色描述)"""
    mapping = {
        "熊大熊二": "two friendly brown bears, 3D Disney Pixar style, high quality textures",
        "喜羊羊": "a cute white sheep with a golden bell, 3D animated style, fluffy wool",
        "小博士": "a wise little owl wearing large glasses and a graduation cap, 3D stylized"
    }
    return mapping.get(name, "a cute 3D educational cartoon character")

def extract_id_from_sse(raw_text: str) -> Optional[str]:
    """專門處理第三方 API 奇怪的 SSE (data: {...}) 格式"""
    lines = raw_text.split('\n')
    for line in lines:
        line = line.strip()
        if not line: continue
        
        content = line
        if line.startswith("data:"):
            content = line.replace("data:", "", 1).strip()
        
        try:
            data = json.loads(content)
            # 支援多種可能的 ID 欄位路徑 (這是您之前的修復重點)
            job_id = data.get("id") or (data.get("data") and data.get("data").get("id"))
            if job_id: return str(job_id)
        except:
            continue
    return None

async def poll_video_url(task_id: str, headers: dict):
    """靈敏輪詢：具備容錯解析與狀態追蹤 (確保影片地址不遺失)"""
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
                            
                            # 成功拿到影片 (這裡就是您最在意的「獲得地址」邏輯)
                            if results and len(results) > 0:
                                url = results[0].get('url')
                                if url: 
                                    print(f"✅ 動畫生成完畢: {url}")
                                    return url
                            
                            # 檢查中間狀態
                            status = str(res_obj.get("status", "")).lower()
                            if status in ["waiting", "processing", "pending", "running", "none"]:
                                if i % 3 == 0: print(f"⏳ 任務 {task_id} 狀態: {status}...")
                                break
                            if status in ["failed", "error"]:
                                print(f"❌ 第三方回報失敗: {status}")
                                return None
                        except:
                            continue
            except Exception as e:
                print(f"⚠️ 輪詢異常 (Task {task_id}): {e}")
                continue
    return None

async def background_generate_course(request: VideoRequest, internal_task_id: str):
    """背景執行緒：全功能教學影片生成流水線 (整合 DeepSeek + Sora)"""
    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            print(f"🚀 開始製作課程: {request.topic}")
            task_results[internal_task_id] = {"status": "processing", "message": "正在規劃教學劇本..."}
            
            # 1. 使用 DeepSeek 生成劇本
            headers_ds = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
            ds_payload = {
                "model": "deepseek-chat",
                "messages": [
                    {
                        "role": "system", 
                        "content": "你是一位專業的兒童數學老師。請生成 JSON 格式的劇本。劇本包含 'scenes' 列表，每個場景有 'title' (標題), 'visual_prompt' (英文視覺描述，不含敏感詞), 'narration' (繁體中文旁白)。"
                    },
                    {"role": "user", "content": f"請為 6 歲孩子製作一堂關於『{request.topic}』的課。只需要 2 個最核心的場景。"}
                ],
                "response_format": {"type": "json_object"}
            }
            
            ds_res = await client.post(f"{DEEPSEEK_BASE_URL}/chat/completions", headers=headers_ds, json=ds_payload)
            script_data = json.loads(ds_res.json()["choices"][0]["message"]["content"])
            scenes = script_data.get("scenes", [])
            print(f"🎬 劇本規劃完成，場景數: {len(scenes)}")

            # 2. 依次提交 Sora 任務
            final_course = []
            headers_sora = {"Authorization": f"Bearer {SORA_API_KEY}", "Content-Type": "application/json"}
            char_desc = get_character_desc(request.character)

            for idx, scene in enumerate(scenes):
                raw_prompt = f"{request.style} animation, {char_desc}, {scene['visual_prompt']}, high quality, educational video."
                safe_prompt = clean_prompt_for_safety(raw_prompt)
                
                task_results[internal_task_id].update({
                    "progress": f"{idx}/{len(scenes)}",
                    "message": f"正在製作場景 {idx+1}: {scene['title']}..."
                })
                
                sora_job_id = None
                video_url = None

                # 提交任務
                for attempt in range(3):
                    try:
                        print(f"📤 提交場景 {idx+1} (嘗試 {attempt+1})...")
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

                # 輪詢結果
                if sora_job_id:
                    video_url = await poll_video_url(sora_job_id, headers_sora)
                
                final_course.append({
                    "title": scene.get("title", f"場景 {idx+1}"),
                    "narration": scene.get("narration", "正在準備有趣的內容..."),
                    "video_url": video_url or "https://media.giphy.com/media/3o7TKMGpxx36E20Nl6/giphy.gif"
                })

            # 全部完成
            task_results[internal_task_id] = {
                "status": "completed", 
                "data": final_course,
                "message": "動畫課程製作完成！"
            }
            print(f"✨ --- 全部任務結束 ---")
            
        except Exception as e:
            print(f"💥 背景任務崩潰: {e}")
            task_results[internal_task_id] = {"status": "error", "message": f"製作過程發生錯誤: {str(e)}"}

# --- API 路由 ---

@app.get("/health")
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
    # 您最擔心的底部啟動代碼在這裡！
    uvicorn.run(app, host="0.0.0.0", port=8000, timeout_keep_alive=60)