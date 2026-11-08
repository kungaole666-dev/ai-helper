import os
import json

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DEFAULT_CONFIG = {
    # 企业微信基础配置
    "CORP_ID": "wwffd531dbf792dd1d",
    "AGENT_ID": 1000002,
    "SECRET": "4KTaqBPlp9Ng_H4QiBiuM-OEuVrHwu6Ne9A0dh1jhUE",
    "TOKEN": "R8sO2RgY8kjrKD",
    "ENCODING_AES_KEY": "pU5e1FO8PkLrkb2tKJfipctzDXLGPcyoisPUj6xgXX2",

    # 微信客服专用配置 (免备案、免内网穿透)
    "KF_SECRET": "",
    "OPEN_KFID": "",

    # Google Gemini 官方模型配置
    "GEMINI_API_KEY": "AQ.Ab8RN6IHfo-95qMcYrGJU6f6K3B9MVPQMzj0PrMhTrbIIuVKog",
    "GEMINI_MODEL": "gemini-3-flash-preview",

    # 本地网络代理
    "PROXY": "http://127.0.0.1:7897",

    # 联网搜索配置
    "TAVILY_API_KEY": "",

    # 服务端口 (云平台会自动注入环境变量 PORT)
    "PORT": int(os.environ.get("PORT", 8000))
}

def load_config():
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=4, ensure_ascii=False)
        return DEFAULT_CONFIG
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            merged = DEFAULT_CONFIG.copy()
            merged.update(data)
            return merged
    except Exception as e:
        print(f"读取 config.json 出错，使用默认配置: {e}")
        return DEFAULT_CONFIG

cfg = load_config()
