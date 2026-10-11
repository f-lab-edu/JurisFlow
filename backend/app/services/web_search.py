import re
from urllib.parse import urlsplit

from ddgs import DDGS


# 검색어에서 이메일·전화번호·URL을 제거하고 길이 제한
def safe_query(query: str) -> str:
    query = re.sub(r"https?://\S+|\S+@\S+|\b\d[\d\s()-]{6,}\d\b", " ", query)
    return " ".join(query.split())[:160]


# DuckDuckGo 검색 스니펫을 조회하고 중복·잘못된 URL 제외
def search_web(query: str, *, limit: int = 3, timeout: int = 10) -> list[dict]:
    query = safe_query(query)
    if not query:
        return []
    results = DDGS(timeout=timeout).text(
        query,
        backend="duckduckgo",
        max_results=limit,
    )
    sources, seen = [], set()
    for result in results:
        url = result.get("href", "")
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or url in seen
        ):
            continue
        seen.add(url)
        sources.append(
            {
                "kind": "web",
                "key": url,
                "title": result.get("title", "")[:200],
                "url": url,
                "text": result.get("body", "")[:1200],
            }
        )
        if len(sources) >= limit:
            break
    return sources
