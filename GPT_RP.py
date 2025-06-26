from fastapi import FastAPI, APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone
from pathlib import Path
import yaml

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

    - message:   必填，對角色說的話
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
    if Path(lc_name).name != lc_name:
        raise HTTPException(status_code=400, detail="非法角色卡路徑！")

    for ext in (".yaml", ".yml"):
        candidate = CHAR_DIR / f"{lc_name}{ext}"
        if candidate.exists():
            resolved = candidate.resolve()
            break
    else:
        raise HTTPException(status_code=404, detail=f"角色卡 {char_name} 不存在！")
    try:
        resolved.relative_to(CHAR_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="角色卡路徑越界！")

    with open(resolved, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    # --------- 掛載 modules ----------
    modules = data.get("character_modules", {}).get("mounted_modules", [])
    modules_dir = CHAR_DIR / "modules"
    for modname in modules:
        mod_path = modules_dir / f"{modname}.yaml"
        if mod_path.exists():
            with open(mod_path, "r", encoding="utf-8") as mf:
                mod_data = yaml.safe_load(mf)
                # 合併所有 key（同名 key 以模組內容為準）
                for key, value in mod_data.items():
                    data[key] = value
        else:
            raise HTTPException(status_code=500, detail=f"模組 {modname} 不存在！")

    if "basic_info" not in data:
        raise HTTPException(status_code=500, detail=f"{char_name}.yaml（含模組）欄位不完整，缺 basic_info")
    # speech_patterns 非必須（視你的pick_reply邏輯而定，若有模組會自動帶入）

    return data


def pick_reply(char_data: dict, user_msg: str) -> str:
    """
    根據使用者訊息與角色口吻回傳一句話（簡易範例）
    * 請根據你的模組內容調整這裡！*
    """
    low = user_msg.lower()
    if any(x in low for x in ("angry", "mad", "怒", "生氣")):
        mood = "angry"
    elif any(x in low for x in ("happy", "love", "開心", "喜")):
        mood = "happy"
    else:
        mood = "neutral"

    # speech_patterns 可直接在主檔或模組帶入
    tpl = char_data.get("speech_patterns", {}).get(mood) or char_data.get("speech_patterns", {}).get("neutral", "{msg}")
    name = char_data["basic_info"].get("stage_name", char_data["basic_info"].get("real_name", "角色"))
    return tpl.format(name=name, msg=user_msg)

# --------------------
# FastAPI + Router
# --------------------
router = APIRouter()

@router.post(
    "/respond",
    operation_id="respond_character",   # 🔑 與 OpenAPI/Actions 同名
    response_model=ReplyOut,
)
async def respond(payload: MessageIn):
    """主要對話入口──一次處理 N 角色"""
    char_list = payload.characters or [DEFAULT_CHAR]

    replies: List[ReplyAtom] = []
    for char_name in char_list:
        char_data = load_character_yaml(char_name)
        reply_text = pick_reply(char_data, payload.message)
        replies.append({"name": char_name, "reply": reply_text})

    return {"replies": replies}

# health 與 list_roles 方便監控 / 除錯
@router.get("/health")
async def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}

@router.get("/list_roles")
async def list_roles():
    roles = []
    for f in CHAR_DIR.iterdir():
        if f.suffix.lower() in (".yaml", ".yml"):
            roles.append(f.stem)
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
