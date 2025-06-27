from fastapi import FastAPI, APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone
from pathlib import Path
import yaml
import logging 
import re # 導入 re 模組，用於正規表達式清洗 (雖然模組讀取簡化了，但保留以防萬一角色卡自身需要淨化)

# 配置日誌，方便除錯
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

"""
GPT_RP.py — 多角色混戰（N 角色一次回覆）
------------------------------------------------
* 依照 `characters: ["erwin", "lior", ...]` 陣列，逐一載入對應 YAML
* **模組現在由 GPTS 知識庫提供，本服務不再負責載入模組文件。**
* 回傳格式：
  {
    "replies": [
      {"name": "erwin", "reply": "..."},
      {"name": "lior", "reply": "..."}
    ]
  }
* GPT 前端只要把 replies 迭代顯示即可
"""

# --------------------
# 常數設定
# --------------------
CHAR_DIR = Path("Characters")  # 存放角色卡的資料夾
DEFAULT_CHAR = "lior"          # 沒帶 characters 時的預設角色

# --------------------
# 資料結構
# --------------------
class MessageIn(BaseModel):
    """使用者輸入結構

    - message:     必填，對角色說的話
    - characters: 選填，角色 list；若缺則使用 DEFAULT_CHAR
    """
    message: str
    characters: Optional[List[str]] = None

class ReplyAtom(BaseModel):
    name: str
    reply: str

class ReplyOut(BaseModel):
    """API 回傳結構──一次回多句"""
    replies: List[ReplyAtom]

# --------------------
# 工具函式
# --------------------

def load_character_yaml(char_name: str):
    """
    讀取角色卡 (不再載入模組，模組由GPTS知識庫提供)
    """
    lc_name = char_name.lower()
    
    # 路徑驗證
    if '/' in lc_name or '\\' in lc_name:
        logging.error(f"非法角色卡路徑！名稱中包含路徑分隔符: {char_name}")
        raise HTTPException(status_code=400, detail="非法角色卡路徑！名稱中包含路徑分隔符。")

    resolved = None 
    for ext in (".yaml", ".yml"):
        candidate = CHAR_DIR / f"{lc_name}{ext}"
        if candidate.exists():
            resolved = candidate.resolve()
            break
    else:
        logging.warning(f"角色卡 {char_name} 不存在於 {CHAR_DIR}。")
        raise HTTPException(status_code=404, detail=f"角色卡 {char_name} 不存在！")
    
    # 路徑越界檢查
    try:
        resolved.relative_to(CHAR_DIR.resolve())
    except ValueError:
        logging.error(f"角色卡路徑越界！嘗試存取 {resolved}，但不在 {CHAR_DIR.resolve()} 內。")
        raise HTTPException(status_code=400, detail="角色卡路徑越界！")

    data = {} 
    try:
        with open(resolved, "r", encoding="utf-8") as f:
            raw_content = f.read()
            # 對角色卡內容進行溫和淨化，以防萬一角色卡自身也存在隱藏字符
            cleaned_content = re.sub(r'[\u200b\u200c\u200d\uFEFF]|\[cite:[^\]]+\]|<\/?cite_start>', '', raw_content, flags=re.UNICODE)
            preprocessed_content = re.sub(r'^\?([^\s]?)', r'# ?\1', cleaned_content, flags=re.MULTILINE)
            
            data = yaml.safe_load(preprocessed_content)
            
            if data is None:
                data = {}
                logging.warning(f"角色卡文件 {resolved} 為空或內容無效。")
    except yaml.YAMLError as e:
        logging.error(f"解析角色卡 {resolved} 失敗 (YAML Error): {e}")
        raise HTTPException(status_code=500, detail=f"解析角色卡 {char_name}.yaml 時發生錯誤：{e}")
    except Exception as e:
        logging.error(f"讀取角色卡 {resolved} 時發生未知錯誤: {e}")
        raise HTTPException(status_code=500, detail=f"讀取角色卡 {char_name}.yaml 時發生未知錯誤。")

    # 注意：這裡不再有模組載入邏輯了！

    if "basic_info" not in data:
        logging.error(f"角色卡 {char_name}.yaml 欄位不完整，缺 basic_info。")
        raise HTTPException(status_code=500, detail=f"{char_name}.yaml 欄位不完整，缺 basic_info")
    
    return data


def pick_reply(char_data: dict, user_msg: str) -> str:
    """
    根據使用者訊息與角色口吻回傳一句話 (這部分由LLM處理，僅作為示例)
    * 這部分邏輯將主要由外部 LLM (GPTS) 根據知識庫中的模組和角色數據來處理。
    * 這裡僅提供一個非常基礎的 fallback 回覆，實際功能會通過LLM的指令觸發。
    """
    name = char_data["basic_info"].get("stage_name", char_data["basic_info"].get("real_name", "角色"))
    
    # 這裡的邏輯非常基礎，因為大部分語氣和事件處理將由GPTS接管
    if "你好" in user_msg or "hi" in user_msg.lower():
        return f"{name} 說：『你好，我是{name}。請問有什麼可以幫妳的嗎？』"
    else:
        return f"{name} 接收到：『{user_msg}』。由於模組已整合至LLM知識庫，更複雜的語氣與事件將由LLM生成。"


# --------------------
# FastAPI + Router
# --------------------
router = APIRouter()

@router.post(
    "/respond",
    operation_id="respond_character",    # 🔑 與 OpenAPI/Actions 同名
    response_model=ReplyOut,
)
async def respond(payload: MessageIn):
    """主要對話入口──一次處理 N 角色"""
    char_list = payload.characters or [DEFAULT_CHAR]

    replies: List[ReplyAtom] = []
    for char_name in char_list:
        try:
            char_data = load_character_yaml(char_name)
            # 將角色數據和用戶消息傳遞給 pick_reply。
            # 實際在GPTS中，LLM將讀取知識庫中的所有模組數據，結合角色卡來生成回覆。
            reply_text = pick_reply(char_data, payload.message) 
            replies.append({"name": char_name, "reply": reply_text})
        except HTTPException as e:
            logging.error(f"處理角色 {char_name} 時發生 HTTP 錯誤: {e.detail}")
            replies.append({"name": char_name, "reply": f"錯誤：無法載入或處理角色 {char_name} ({e.detail})"})
        except Exception as e:
            logging.error(f"處理角色 {char_name} 時發生未知錯誤: {e}")
            replies.append({"name": char_name, "reply": f"錯誤：處理角色 {char_name} 時發生未知錯誤。"})


    return {"replies": replies}

# health 與 list_roles 方便監控 / 除錯
@router.get("/health")
async def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}

@router.get("/list_roles")
async def list_roles():
    roles = []
    try:
        for f in CHAR_DIR.iterdir():
            if f.is_file() and f.suffix.lower() in (".yaml", ".yml"):
                roles.append(f.stem)
    except Exception as e:
        logging.error(f"列出角色時發生錯誤: {e}")
        raise HTTPException(status_code=500, detail=f"列出角色時發生錯誤: {e}")
    return {"roles": roles}

# --------------------
# FastAPI 應用實例
# --------------------
app = FastAPI(title="Multi‑Character RP", version="1.1.0")
app.include_router(router)

# --------------------
# 直接執行時（本地測試）
# --------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("GPT_RP:app", host="0.0.0.0", port=8000, reload=True)
