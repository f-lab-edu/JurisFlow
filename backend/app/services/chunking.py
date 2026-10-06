import re
from collections.abc import Callable


def chunk_markdown(text: str, size: int, overlap: int, fits: Callable[[str], bool]):
    text = text.strip()
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        # Enforce the actual embedding tokenizer limit, including its prefix.
        if not fits(text[start:end]):
            low, high = start + 1, end
            while low < high:
                mid = (low + high + 1) // 2
                if fits(text[start:mid]):
                    low = mid
                else:
                    high = mid - 1
            end = low
        if end < len(text):
            candidate = text[start:end]
            for pattern in (r"\n\s*\n", r"\n", r"[.!?。] +", r" +"):
                boundaries = [m.end() for m in re.finditer(pattern, candidate)]
                boundaries = [n for n in boundaries if n >= len(candidate) // 2]
                if boundaries:
                    end = start + boundaries[-1]
                    break
        chunk = text[start:end].strip()
        if chunk:
            yield chunk
        if end == len(text):
            break
        start = max(start + 1, end - min(overlap, (end - start) // 4))
