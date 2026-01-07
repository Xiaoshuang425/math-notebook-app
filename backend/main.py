import os
import json
import time
import asyncio
import httpx
import re
from typing import List, Optional
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

# --- 初始化與配置 ---
# 從 .env 檔案讀取環境變數（如 API Key），保護訊息避免不法分子不法分母不法分數綫來偷api
load_dotenv()

# 初始化 FastAPI 
app = FastAPI(
    title="KidAni Math AI Studio",
    version="4.5.3",
    description="具備穩定 SSE 解析與純中文音軌權重的 AI 數學動畫工作室"
)

# 解決跨域問題 (CORS) 我真的很崩潰每次都被這個陰
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],      # 允許所有訪問
    allow_credentials=True,   # 允許攜帶憑證
    allow_methods=["*"],      # 允許所有 HTTP 方法
    allow_headers=["*"],      # 允許所有請求標頭
)

# 從系統環境變數中提取 API Key，沒有返回null
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()
SORA_API_KEY = os.environ.get("SORA_API_KEY", "").strip()

# 設定三方服務的 API URL
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
SORA_BASE_URL = "https://grsai.dakka.com.cn"

# 任務儲存字典：用來暫存後端正在處理的動畫任務狀態與結果
task_results = {}

# 定義請求模型：規範前端發送過來的 JSON 格式。防止地鋪西克輸出莫名其妙的markdown
class VideoRequest(BaseModel):
    topic: str                      # 課程主題（必填）
    character: Optional[str] = "可愛助手"  # 角色名稱（選填，預設為可愛助手）
    style: Optional[str] = "3D"      # 動畫風格（選填，預設為 3D）

# --- 核心工具函數 ---

def clean_prompt_for_safety(prompt: str) -> str:
    """過濾敏感詞：檢查並移除 Prompt 中的不當字詞，防止被 AI 內容審查攔截"""
    forbidden = ["politics", "bloody", "violence", "sexy"]
    for word in forbidden:
        # 使用正規表達式進行不區分大小寫的替換
        prompt = re.sub(word, "", prompt, flags=re.IGNORECASE)
    return prompt

def get_character_desc(name: str):
    """角色映射邏輯：根據角色名稱返回詳細的英文視覺描述給 Sora 生成影片使用"""
    mapping = {
        "熊大熊二": "two friendly brown bears, 3D Disney Pixar style, high quality textures",
        "喜羊羊": "a cute white sheep with a golden bell, 3D animated style, fluffy wool",
        "小博士": "a wise little owl wearing large glasses and a graduation cap, 3D stylized"
    }
    return mapping.get(name, "a cute 3D educational cartoon character")

def extract_id_from_sse(raw_text: str) -> Optional[str]:
    """專門處理 SSE (Server-Sent Events) 格式：從串流回應中解析出任務 ID"""
    lines = raw_text.split('\n') # 按行拆分回應文本
    for line in lines:
        line = line.strip()
        if not line: continue
        
        content = line
        if line.startswith("data:"):
            # 移除 SSE 的資料前綴 "data:"
            content = line.replace("data:", "", 1).strip()
        
        try:
            # 試圖將字串解析為 JSON 並提取任務 ID
            data = json.loads(content)
            # 兼容不同的 JSON 結構（直接在根目錄或在 data 欄位下）
            job_id = data.get("id") or (data.get("data") and data.get("data").get("id"))
            if job_id: return str(job_id)
        except:
            continue
    return None

async def poll_video_url(task_id: str, headers: dict):
    """名場面防禦型編程"""
    print(f">>> 進入輪詢階段 [ID: {task_id}]")
    async with httpx.AsyncClient(timeout=30.0) as client:
        # 最多輪詢 120 次，每次間隔 10 秒。因爲sora真的很慢。
        for i in range(120): 
            await asyncio.sleep(10) # 每次查詢前等待 10 秒
            try:
                # 調用第三方服務的結果查詢介面
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
                            
                            # 若 results 存在且有 URL，代表影片生成成功
                            if results and len(results) > 0:
                                url = results[0].get('url')
                                if url: 
                                    print(f"✅ 動畫生成完畢: {url}")
                                    return url
                            
                            # 檢查任務目前的狀態（排隊中、處理中等）
                            status = str(res_obj.get("status", "")).lower()
                            if status in ["waiting", "processing", "pending", "running", "none"]:
                                # 每輪詢 3 次才打印一次日誌，減少控制台噪音。
                                if i % 3 == 0: print(f"⏳ 任務 {task_id} 狀態: {status}...")
                                break
                            # 若狀態顯示失敗則提早結束輪詢
                            if status in ["failed", "error"]:
                                return None
                        except:
                            continue
            except Exception as e:
                print(f"⚠️ 輪詢異常 (Task {task_id}): {e}")
                continue
    return None

async def background_generate_course(request: VideoRequest, internal_task_id: str):
    """背景執行緒：處理完整的生成流程（劇本規劃 -> 影片生成 -> 結果組合）"""
    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            print(f"開始製作純中文課程: {request.topic}")
            # 更新任務狀態為「正在處理」
            task_results[internal_task_id] = {"status": "processing", "message": "正在規劃純中文教學劇本..."}
            
            # --- 第一階段：使用 DeepSeek 生成課程劇本 ---
            headers_ds = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
            ds_payload = {
                "model": "deepseek-chat",
                "messages": [
                    {
                        "role": "system", 
                        "content": (
                            "你是一位專業的兒童數學老師。請生成 JSON 格式的劇本。劇本包含 'scenes' 列表，"
                            "每個場景有 'title' (標題), 'visual_prompt' (英文視覺描述), "
                            "'narration' (旁白)。【重要限制】：旁白必須完全使用繁體中文。"
                        )
                    },
                    {"role": "user", "content": f"請為 6 歲孩子製作一堂關於『{request.topic}』的課。"}
                ],
                "response_format": {"type": "json_object"} # 強制要求 AI 回傳 JSON 格式
            }
            
            # 發送請求到 DeepSeek
            ds_res = await client.post(f"{DEEPSEEK_BASE_URL}/chat/completions", headers=headers_ds, json=ds_payload)
            script_data = json.loads(ds_res.json()["choices"][0]["message"]["content"])
            scenes = script_data.get("scenes", []) # 提取劇本中的場景列表

            # --- 第二階段：依次提交場景到 Sora 生成影片 ---
            final_course = []
            headers_sora = {"Authorization": f"Bearer {SORA_API_KEY}", "Content-Type": "application/json"}
            char_desc = get_character_desc(request.character) # 獲取角色視覺描述

            for idx, scene in enumerate(scenes):
                # 組合最終要送給 AI 影片模型的提示詞 (Prompt)
                raw_prompt = (
                    f"{request.style} animation, {char_desc}, {scene['visual_prompt']}, "
                    f"Chinese environment, video with Chinese audio only, no English speech, "
                    f"educational video, high quality."
                )
                # 安全過濾。openai有這個知識產權意識。希望不只是爲防止賠錢XD
                safe_prompt = clean_prompt_for_safety(raw_prompt)
                
                # 回報目前處理進度給前端
                task_results[internal_task_id].update({
                    "progress": f"{idx}/{len(scenes)}",
                    "message": f"正在製作純中文場景 {idx+1}: {scene['title']}..."
                })
                
                sora_job_id = None
                video_url = None

                # 提交任務重試機制（最多嘗試 3 次，應對網路波動）
                for attempt in range(3):
                    try:
                        submit_res = await client.post(
                            f"{SORA_BASE_URL}/v1/video/sora-video",
                            headers=headers_sora,
                            json={"model": "sora-2", "prompt": safe_prompt},
                            timeout=180.0
                        )
                        raw_text = submit_res.text.strip()
                        # 跳過無效回應或 HTML 報錯頁面
                        if not raw_text or "<html>" in raw_text.lower(): continue

                        # 從 SSE 串流中解析任務 ID
                        sora_job_id = extract_id_from_sse(raw_text)
                        if sora_job_id: break
                    except Exception as e:
                        print(f"⚠️ 提交失敗: {e}")
                    await asyncio.sleep(5)

                # 若提交成功，啟動靈敏輪詢獲取影片網址
                if sora_job_id:
                    video_url = await poll_video_url(sora_job_id, headers_sora)
                
                # 將該場景結果（標題、旁白、影片連結）存入最終列表
                final_course.append({
                    "title": scene.get("title", f"場景 {idx+1}"),
                    "narration": scene.get("narration", "正在準備內容..."),
                    "video_url": video_url or "https://media.giphy.com/media/3o7TKMGpxx36E20Nl6/giphy.gif" # 若失敗則給預設動圖
                })

            # 所有場景製作完畢，更新任務狀態為「完成」
            task_results[internal_task_id] = {
                "status": "completed", 
                "data": final_course,
                "message": "純中文動畫課程製作完成！"
            }
            
        except Exception as e:
            # 捕捉全局錯誤並存入狀態字典，方便前端查詢報錯原因
            task_results[internal_task_id] = {"status": "error", "message": f"製作過程錯誤: {str(e)}"}

# --- API 路由 (Endpoints) ---

@app.api_route("/health", methods=["GET", "HEAD"])
async def health(request: Request = None):
    # 修正重點：添加 request 參數，解決 TypeError
    return {"status": "ok", "timestamp": time.time()}

@app.post("/generate-video")
async def generate_video(request: VideoRequest, background_tasks: BackgroundTasks):
    """啟動生成任務：主入口。會立即回傳任務 ID，隨後在背景非同步啟動生成邏輯"""
    internal_id = f"task_{int(time.time())}" # 生成唯一的內部任務 ID
    task_results[internal_id] = {"status": "processing", "message": "任務已啟動"}
    # 將耗時操作放入 BackgroundTasks，確保 API 不會因為等待影片生成而造成 HTTP 超時
    background_tasks.add_task(background_generate_course, request, internal_id)
    return {"status": "queued", "task_id": internal_id}

@app.get("/task-status/{task_id}")
async def get_task_status(task_id: str):
    """查詢任務狀態：前端會透過 polling 方式每隔幾秒來獲取最新進度"""
    return task_results.get(task_id, {"status": "not_found"})

if __name__ == "__main__":
    import uvicorn
    # Render 等平台會透過 PORT 環境變數指定埠號，若無則預設 8000
    port = int(os.environ.get("PORT", 8000))
    # 啟動伺服器，host 設為 0.0.0.0 以接受外部網路請求
    uvicorn.run(app, host="0.0.0.0", port=port, timeout_keep_alive=60)