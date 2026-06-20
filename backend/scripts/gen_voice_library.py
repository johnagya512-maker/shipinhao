"""从 volcano_timbres.json(账号真实授权清单)生成 voices.py 的 VOICE_LIBRARY 块。

把全部授权的中文 uranus 2.0 音色按 Categories 自动归类、去重，原地替换
voices.py 里 `VOICE_LIBRARY = [` ... `]` 之间的内容。杜绝手动精选导致漏音色。
"""
import json
import os
import re

HERE = os.path.dirname(__file__)
JSON_PATH = os.path.join(HERE, "volcano_timbres.json")
VOICES_PY = os.path.join(HERE, "..", "app", "services", "voices.py")

CAT_NAME = {"narration": "视频配音", "male": "通用男声",
            "female": "通用女声", "character": "角色扮演"}


def cat_of(it):
    cs = set()
    for c in (it.get("Categories") or []):
        cs.update(c.get("Categories") or [])
    if "角色扮演" in cs:
        return "character"
    if cs & {"视频配音", "有声阅读", "教学场景"}:
        return "narration"
    return "male" if (it.get("Gender") or "") == "男" else "female"


def first_cat_label(it):
    for c in (it.get("Categories") or []):
        for name in (c.get("Categories") or []):
            return name
    return ""


def main():
    data = json.load(open(JSON_PATH, encoding="utf-8"))
    seen = {}
    for it in data:
        vt = it.get("VoiceType") or ""
        if vt.startswith("zh_") and vt not in seen:
            seen[vt] = it

    buckets = {"narration": [], "male": [], "female": [], "character": []}
    for it in seen.values():
        buckets[cat_of(it)].append(it)

    lines = ["VOICE_LIBRARY = ["]
    for key in ["narration", "male", "female", "character"]:
        lines.append(f"    # ── {CAT_NAME[key]}（账号授权·uranus 2.0）──")
        for it in sorted(buckets[key], key=lambda x: x["VoiceType"]):
            vt = it["VoiceType"]
            name = (it.get("Name") or "").replace(" 2.0", "").strip()
            tag = first_cat_label(it) or "通用"
            lines.append(
                f'    {{"id": "{vt}", "name": "{name}", '
                f'"tag": "{tag}", "category": "{key}"}},')
    lines.append("]")
    block = "\n".join(lines)

    src = open(VOICES_PY, encoding="utf-8").read()
    new = re.sub(r"VOICE_LIBRARY = \[.*?\n\]",
                 block, src, count=1, flags=re.DOTALL)
    open(VOICES_PY, "w", encoding="utf-8").write(new)
    total = sum(len(v) for v in buckets.values())
    print(f"已写入 {total} 个音色:",
          {k: len(v) for k, v in buckets.items()})


if __name__ == "__main__":
    main()
