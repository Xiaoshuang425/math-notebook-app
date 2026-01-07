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
    version="3.9.0"
)

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

# --- 輔助工具 ---

def get_character_desc(name: str):
    mapping = {
        "熊大熊二": "two friendly brown bears, 3D cartoon style, cute faces",
        "喜羊羊": "a cute white sheep with a bell, 3D animated style",
        "小博士": "a wise little owl with glasses, 3D stylized"
    }
    return mapping.get(name, "a cute 3D educational character")

async def poll_video_url(task_id: str, headers: dict):
    """靈敏輪詢：支援長達 10 分鐘等待"""
    print(f"開始輪詢 Sora 任務 ID: {task_id}")
    for i in range(120): 
        await asyncio.sleep(8) # 稍微拉長輪詢間隔，減少對 API 的負擔
        try:
            res = requests.post(
                f"{SORA_BASE_URL}/v1/draw/result", 
                headers=headers, 
                json={"id": task_id}, 
                timeout=30 
            )
            if res.status_code != 200:
                continue
                
            data = res.json()
            res_obj = data.get("data") if isinstance(data.get("data"), dict) else data
            results = res_obj.get("results")
            
            if results and len(results) > 0:
                url = results[0].get('url')
                if url:
                    print(f"影片生成成功: {url}")
                    return url
            
            status = str(res_obj.get("status", "")).lower()
            if status in ["waiting", "processing", "pending", "none", "running"]:
                if i % 3 == 0: print(f"任務 {task_id} 處理中... ({i+1}/120)")
                continue
            
            if status in ["failed", "error"]:
                print(f"Sora 回報失敗狀態: {status}")
                return None
                
        except Exception:
            continue
            
    return None

async def background_generate_course(request: VideoRequest, internal_task_id: str):
    """背景執行緒：處理繁重的生成任務"""
    try:
        print(f"--- 啟動背景製作: {request.topic} ---")
        
        # 1. 生成劇本 (DeepSeek)
        task_results[internal_task_id] = {"status": "processing", "message": "正在規劃教學劇本..."}
        
        headers_ds = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
        ds_payload = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": "You are a math tutor scriptwriter. Output JSON with 'scenes' (title, visual_prompt, narration)."},
                {"role": "user", "content": f"Create a 2-scene math lesson about {request.topic}."}
            ],
            "response_format": {"type": "json_object"}
        }
        
        ds_res = requests.post(f"{DEEPSEEK_BASE_URL}/chat/completions", headers=headers_ds, json=ds_payload, timeout=60)
        script_data = ds_res.json()["choices"][0]["message"]["content"]
        scenes = json.loads(script_data).get("scenes", [])
        print(f"劇本完成，準備提交影片...")

        # 2. 提交影片請求
        final_course = []
        headers_sora = {"Authorization": f"Bearer {SORA_API_KEY}", "Content-Type": "application/json"}
        char_desc = get_character_desc(request.character)

        for idx, scene in enumerate(scenes):
            prompt = f"{request.style} animation, {char_desc}, {scene['visual_prompt']}, high quality."
            print(f"正在提交場景 {idx+1} (超時設定已增加至 300s)...")
            task_results[internal_task_id] = {
                "status": "processing", 
                "progress": f"{idx}/{len(scenes)}",
                "message": f"正在生成第 {idx+1} 個場景動畫..."
            }
            
            video_url = None
            try:
                # 提交影片請求
                submit_res = requests.post(
                    f"{SORA_BASE_URL}/v1/video/sora-video",
                    headers=headers_sora,
                    json={"model": "sora-2", "prompt": prompt},
                    timeout=300 # 設定為 5 分鐘
                )
                
                if submit_res.status_code == 200:
                    # 檢查回應是否為空
                    if not submit_res.text.strip():
                        raise ValueError("伺服器回傳了空內容")
                        
                    sora_data = submit_res.json()
                    sora_job_id = sora_data.get("id") or (sora_data.get("data") and sora_data.get("data").get("id"))
                    
                    if sora_job_id:
                        video_url = await poll_video_url(sora_job_id, headers_sora)
                    else:
                        print(f"提交成功但未獲取 ID: {sora_data}")
                else:
                    print(f"場景 {idx+1} 伺服器回傳錯誤代碼: {submit_res.status_code}, 內容: {submit_res.text[:100]}")
                    
            except requests.exceptions.Timeout:
                print(f"場景 {idx+1} 提交超時（300秒已到）")
            except Exception as e:
                print(f"場景 {idx+1} 發生錯誤: {e}")
            
            final_course.append({
                "title": scene.get("title", f"場景 {idx+1}"),
                "narration": scene.get("narration", "正在學習..."),
                "video_url": video_url or "https://media.giphy.com/media/3o7TKMGpxx36E20Nl6/giphy.gif"
            })
            
            task_results[internal_task_id]["progress"] = f"{idx+1}/{len(scenes)}"

        task_results[internal_task_id] = {"status": "completed", "data": final_course}
        print(f"--- 任務完成 ---")
        
    except Exception as e:
        print(f"背景任務失敗: {e}")
        task_results[internal_task_id] = {"status": "error", "message": f"製作失敗: {str(e)}"}

# --- 路由 ---

@app.get("/health")
async def health():
    return {"status": "online"}

@app.post("/generate-video")
async def generate_video(request: VideoRequest, background_tasks: BackgroundTasks):
    internal_id = f"task_{int(time.time())}"
    # 初始化狀態，讓前端能立刻得到 ID
    task_results[internal_id] = {"status": "processing", "message": "任務已加入佇列"}
    
    # 將所有耗時操作完全移入背景任務
    background_tasks.add_task(background_generate_course, request, internal_id)
    
    # 立即回傳 ID，避免瀏覽器端超時或 RESET
    return {"status": "queued", "task_id": internal_id}

@app.get("/task-status/{task_id}")
async def get_task_status(task_id: str):
    return task_results.get(task_id, {"status": "not_found"})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)