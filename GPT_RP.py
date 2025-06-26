from fastapi import FastAPI, APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone
from pathlib import Path
import yaml
import logging # 新增：導入 logging 模組，用於輸出日誌

# 配置日誌，方便除錯
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

"""
GPT_RP.py — 多角色混戰（N 角色一次回覆）
------------------------------------------------
* 依照 `characters: ["erwin", "lior", ...]` 陣列，逐一載入對應 YAML
* 沒傳 `characters` 時 fallback 到 `DEFAULT_CHAR`
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
    讀取角色卡及其掛載的 modules
    """
    lc_name = char_name.lower()
    
    # 【改進點 1】路徑驗證：如果 char_name 包含路徑分隔符，`Path(lc_name).name` 會是正確的檔案名，
    # 但 if `Path(lc_name) != Path(lc_name).name` 可能導致誤判。
    # 更好的做法是直接檢查是否存在路徑分隔符或路徑穿越字符。
    # 簡單的檢查是：如果 lc_name 包含了 '/' 或 '\'，則認為是非法路徑。
    if '/' in lc_name or '\\' in lc_name:
        logging.error(f"非法角色卡路徑！名稱中包含路徑分隔符: {char_name}")
        raise HTTPException(status_code=400, detail="非法角色卡路徑！名稱中包含路徑分隔符。")

    resolved = None # 初始化 resolved
    for ext in (".yaml", ".yml"):
        candidate = CHAR_DIR / f"{lc_name}{ext}"
        if candidate.exists():
            resolved = candidate.resolve() # 獲取絕對路徑
            break
    else:
        # 【改進點 2】日誌：當角色卡不存在時，輸出日誌以便追蹤
        logging.warning(f"角色卡 {char_name} 不存在於 {CHAR_DIR}。")
        raise HTTPException(status_code=404, detail=f"角色卡 {char_name} 不存在！")
    
    # 【改進點 3】路徑越界檢查：這行非常重要，確保載入的 YAML 文件都在 CHAR_DIR 內
    try:
        # resolved.relative_to(CHAR_DIR.resolve()) 確保解析後的路徑仍在指定目錄下
        # 這可以防止路徑穿越攻擊 (Path Traversal Attack)
        resolved.relative_to(CHAR_DIR.resolve())
    except ValueError:
        logging.error(f"角色卡路徑越界！嘗試存取 {resolved}，但不在 {CHAR_DIR.resolve()} 內。")
        raise HTTPException(status_code=400, detail="角色卡路徑越界！")

    data = {} # 初始化 data 字典
    try:
        with open(resolved, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            # 【改進點 4】空文件處理：如果 YAML 文件為空，yaml.safe_load 可能返回 None
            if data is None:
                data = {}
                logging.warning(f"角色卡文件 {resolved} 為空或內容無效。")
    except yaml.YAMLError as e:
        # 【改進點 5】YAML 解析錯誤：捕獲更具體的 YAML 錯誤，提供更詳細的提示
        logging.error(f"解析角色卡 {resolved} 失敗: {e}")
        raise HTTPException(status_code=500, detail=f"解析角色卡 {char_name}.yaml 時發生錯誤：{e}")
    except Exception as e:
        logging.error(f"讀取角色卡 {resolved} 時發生未知錯誤: {e}")
        raise HTTPException(status_code=500, detail=f"讀取角色卡 {char_name}.yaml 時發生未知錯誤。")

    # --------- 掛載 modules ----------
    # 【改進點 6】安全獲取模組列表：使用 .get() 並提供預設值，避免 KeyError
    modules = data.get("character_modules", {}).get("mounted_modules", [])
    modules_dir = CHAR_DIR / "modules"
    
    for modname in modules:
        mod_path = modules_dir / f"Module_{modname}.yaml"
        if mod_path.exists():
            try:
                with open(mod_path, "r", encoding="utf-8") as mf:
                    mod_data = yaml.safe_load(mf)
                    # 【改進點 7】空模組文件處理：同角色卡，如果模組文件為空，safe_load 可能返回 None
                    if mod_data is None:
                        mod_data = {}
                        logging.warning(f"模組文件 {mod_path} 為空或內容無效。")
                    
                    # 合併所有 key（同名 key 以模組內容為準）
                    # 注意：這裡的合併邏輯是淺層合併，如果模組和主角色卡有相同的嵌套字典，
                    # 模組的字典會完全覆蓋主角色卡的字典。如果需要深度合併，需要額外邏輯。
                    # 對於目前的 YAML 結構（主要是列表），淺層合併通常足夠。
                    for key, value in mod_data.items():
                        data[key] = value
            except yaml.YAMLError as e:
                logging.error(f"解析模組 {mod_path} 失敗: {e}")
                raise HTTPException(status_code=500, detail=f"解析模組 {modname}.yaml 時發生錯誤：{e}")
            except Exception as e:
                logging.error(f"讀取模組 {mod_path} 時發生未知錯誤: {e}")
                raise HTTPException(status_code=500, detail=f"讀取模組 {modname}.yaml 時發生未知錯誤。")
        else:
            # 【改進點 8】日誌：當模組不存在時，輸出日誌
            logging.error(f"模組 {modname} 不存在於 {modules_dir}。")
            raise HTTPException(status_code=500, detail=f"模組 {modname} 不存在！")

    if "basic_info" not in data:
        logging.error(f"角色卡 {char_name}.yaml（含模組）欄位不完整，缺 basic_info。")
        raise HTTPException(status_code=500, detail=f"{char_name}.yaml（含模組）欄位不完整，缺 basic_info")
    
    # speech_patterns 非必須（視你的pick_reply邏輯而定，若有模組會自動帶入）

    return data


def pick_reply(char_data: dict, user_msg: str) -> str:
    """
    根據使用者訊息與角色口吻回傳一句話（簡易範例）
    * 請根據你的模組內容調整這裡！*
    """
    low = user_msg.lower()
    
    # 假設 speech_patterns 是一個字典，鍵是 mood，值是模板字串
    # 如果 mood 對應的值是一個列表，可以從中隨機選擇一個
    speech_patterns = char_data.get("speech_patterns", {})

    mood_templates = {
        "angry": ["{name} 生氣地說：『{msg}』", "『{msg}』，{name} 語氣很差。"],
        "happy": ["{name} 開心地說：『{msg}』", "『{msg}』，{name} 笑得很燦爛。"],
        "neutral": ["{name} 說：『{msg}』", "『{msg}』，{name} 平靜地回覆。"]
    }
    
    # 合併角色數據中的 speech_patterns 和預設模板
    # 角色數據中的模板優先
    final_templates = {**mood_templates, **speech_patterns}


    mood = "neutral" # 預設情緒
    if any(x in low for x in ("angry", "mad", "怒", "生氣")):
        mood = "angry"
    elif any(x in low for x in ("happy", "love", "開心", "喜")):
        mood = "happy"
    else:
        mood = "neutral"

    # 【改進點 9】pick_reply 邏輯：支援多個模板隨機選擇
    # 獲取對應情緒的模板列表，如果沒有則使用 neutral，再沒有則使用預設的 "{msg}"
    tpl_list = final_templates.get(mood) or final_templates.get("neutral", ["{msg}"])

    if isinstance(tpl_list, list) and tpl_list:
        import random # 導入 random 模組
        tpl = random.choice(tpl_list)
    elif isinstance(tpl_list, str):
        tpl = tpl_list
    else:
        # 如果最終得到的不是列表也不是字串，退回最簡單的模板
        tpl = "{msg}"
        logging.warning(f"角色 {char_data.get('basic_info', {}).get('stage_name', '未知角色')} 的 '{mood}' 情緒模板格式無效。")

    name = char_data["basic_info"].get("stage_name", char_data["basic_info"].get("real_name", "角色"))
    
    # 確保模板中包含 {name} 和 {msg}，如果沒有則提供預設值
    formatted_msg = user_msg
    try:
        formatted_reply = tpl.format(name=name, msg=user_msg)
    except KeyError as e:
        logging.error(f"角色 {name} 的模板 '{tpl}' 缺少必要的佔位符: {e}")
        formatted_reply = f"{name} 回覆：{user_msg} (模板錯誤)"
    except Exception as e:
        logging.error(f"格式化角色 {name} 的回覆時發生未知錯誤: {e}")
        formatted_reply = f"{name} 回覆：{user_msg} (格式化錯誤)"

    return formatted_reply


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
            reply_text = pick_reply(char_data, payload.message)
            replies.append({"name": char_name, "reply": reply_text})
        except HTTPException as e:
            # 【改進點 10】單個角色載入失敗時，允許其他角色繼續回覆
            # 這裡可以選擇將錯誤訊息也包含在回覆中，或僅記錄日誌
            logging.error(f"處理角色 {char_name} 時發生 HTTP 錯誤: {e.detail}")
            # 如果希望在回覆中顯示錯誤，可以這樣做：
            replies.append({"name": char_name, "reply": f"錯誤：無法載入或處理角色 {char_name} ({e.detail})"})
        except Exception as e:
            logging.error(f"處理角色 {char_name} 時發生未知錯誤: {e}")
            replies.append({"name": char_name, "reply": f"錯誤：處理角色 {char_name} 時發生未知錯誤"})


    return {"replies": replies}

# health 與 list_roles 方便監控 / 除錯
@router.get("/health")
async def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}

@router.get("/list_roles")
async def list_roles():
    roles = []
    # 【改進點 11】list_roles 錯誤處理：避免在讀取目錄時因權限等問題崩潰
    try:
        for f in CHAR_DIR.iterdir():
            if f.is_file() and f.suffix.lower() in (".yaml", ".yml"): # 確保是文件而不是資料夾
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
    # 【改進點 12】uvicorn reload 模式：在生產環境中應避免使用 reload=True
    # reload 模式會監控文件變化並重啟應用，適合開發環境
    # 對於生產部署，通常會使用 gunicorn 等工具管理 Uvicorn worker
    uvicorn.run("GPT_RP:app", host="0.0.0.0", port=8000, reload=True)

