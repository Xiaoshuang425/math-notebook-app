🎬 KidAni Math AI Studio

智能數學動畫製片廠 - 讓數學筆記動起來！

KidAni 是一款專為兒童設計的 AI 學習工具，結合了最先進的多模態視覺辨識、劇本生成與影片合成技術。使用者只需拍攝一張手寫數學筆記或題目，系統便會自動將其轉化為生動、有趣的 2D 動畫教學課程。

🌟 核心亮點

📸 智能視覺辨識 (Qwen-VL)：利用阿里雲通義千問視覺模型，自動識別手寫題目、公式與圖形，並提取核心教學要點。

✍️ 劇本深度創作 (DeepSeek)：由 DeepSeek-V3 引擎擔任劇本編劇，將枯燥的數學題目轉化為富有邏輯且親切的「導師旁白」與「視覺場景描述」。

🎥 全自動動畫生成 (Sora-2)：整合高階影音生成 API，根據劇本自動渲染教學影片，呈現皮克斯（Pixar）風格的視覺體驗。

🃏 互動式學習卡片：AI 自動生成 Q&A 閃卡，幫助學生在觀看動畫後進行快速複習。

🛠️ 響應式現代介面：使用 Tailwind CSS 打造，完美適配行動端攝影與桌面端編輯。

🚀 技術堆棧

前端 (Frontend)

語言/框架：HTML5, Tailwind CSS, JavaScript (Vanilla)

圖標庫：Lucide-react

部署建議：Vercel / GitHub Pages

後端 (Backend)

框架：FastAPI (Python)

異步處理：Asyncio, BackgroundTasks (用於處理長耗時影片生成)

API 整合：

Qwen-VL-Plus: 影像分析與 OCR

DeepSeek-V3: 劇本與 JSON 結構化數據生成

Sora-2 (API): 影像生成與任務輪詢

部署建議：Render / Railway / Fly.io

🛠️ 安裝與設置

1. 獲取 API 金鑰

你需要準備以下環境變數（.env）：

QWEN_VL_API_KEY: 來自阿里雲 DashScope

DEEPSEEK_API_KEY: 來自 DeepSeek 開發者平台

SORA_API_KEY: 影片生成服務商金鑰

2. 本地開發環境設置

# 下載專案
git clone [https://github.com/your-username/kidani-math-ai.git](https://github.com/your-username/kidani-math-ai.git)
cd kidani-math-ai

# 安裝後端依賴
pip install -r requirements.txt

# 啟動後端伺服器 (預設 8000 端口)
python main.py


🗺️ 未來展望 (Roadmap)

[ ] 多語言支持：支援英文、日文等多國語言教學生成。

[ ] 雲端學習路徑：接入 Firestore 存儲學生錯題本，實現個性化複習建議。

[ ] 語音互動 (TTS)：讓 AI 導師具備多種聲音選擇，增強互動感。

[ ] AR 增強現實：掃描課本直接在手機螢幕上彈出 3D 數學模型。

📄 開源協議

本專案採用 MIT 協議開源。

KidAni - Transforming every math problem into a magical story.
