import httpx
from typing import List, Dict, Any
from config import cfg

def search_web(query: str, max_results: int = 5) -> str:
    """联网搜索工具，返回格式化好的搜索摘要"""
    tavily_key = cfg.get("TAVILY_API_KEY", "").strip()
    
    if tavily_key:
        try:
            url = "https://api.tavily.com/search"
            payload = {
                "api_key": tavily_key,
                "query": query,
                "search_depth": "basic",
                "include_answer": True,
                "max_results": max_results
            }
            with httpx.Client(timeout=10.0) as client:
                res = client.post(url, json=payload)
                if res.status_code == 200:
                    data = res.json()
                    answer = data.get("answer", "")
                    results = data.get("results", [])
                    out = []
                    if answer:
                        out.append(f"【快速摘要】: {answer}\n")
                    for idx, r in enumerate(results, 1):
                        title = r.get("title", "")
                        content = r.get("content", "")
                        url_link = r.get("url", "")
                        out.append(f"{idx}. {title}\n摘要: {content}\n链接: {url_link}")
                    return "\n\n".join(out)
        except Exception as e:
            print(f"[搜索异常] Tavily 调用失败: {e}")

    # Fallback: 如果未配置 Tavily API Key 或网络请求失败
    return (
        f"【联网搜索功能提示】：已触发关于「{query}」的搜索。\n"
        "如需获取实时最新网页搜索结果，请在 config.json 中配置 TAVILY_API_KEY（可免费申请：https://tavily.com 每月1000次免费额度）。"
    )
