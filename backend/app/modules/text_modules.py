"""文案类模块 A/B/D/F/H。每个函数接收输入与 LLM 配置，返回结果与 token 用量。"""
import json
import re
from app.modules import prompts
from app.services.llm import call_llm, LLMResult


def _render(template: str, **kwargs) -> str:
    safe = {k: (v if v is not None else "") for k, v in kwargs.items()}
    return template.format(**safe)


def _extract_json(text: str) -> dict:
    """从 LLM 输出中提取 JSON，容忍 ```json 包裹。"""
    text = text.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        text = text[start:end + 1]
    return json.loads(text)


def run_clean(provider, model, key, transcript, keyword=None, title=None, author=None):
    prompt = _render(prompts.MODULE_A, transcript=transcript, keyword=keyword,
                     title=title, author=author)
    r: LLMResult = call_llm(provider, model, key, prompt)
    return {"cleaned_text": r.text.strip()}, r


def run_rewrite(provider, model, key, cleaned_text, target_audience="50+女性", title=None,
                track="character_story", monetization_mode="revenue_share"):
    """B 改写。按赛道选提示词；人物故事赛道按变现模式切换结尾引导。"""
    from app.modules import tracks
    if track == "character_story":
        ending = (prompts.ENDING_BOOK_SALES if monetization_mode == "book_sales"
                  else prompts.ENDING_REVENUE_SHARE)
        prompt = _render(prompts.MODULE_B_CHARACTER, cleaned_text=cleaned_text, title=title,
                         rewrite_focus=tracks.get_track(track)["rewrite_focus"],
                         ending_instruction=ending)
    else:
        prompt = _render(prompts.MODULE_B, cleaned_text=cleaned_text,
                         target_audience=target_audience, title=title)
    r = call_llm(provider, model, key, prompt)
    return {"script": r.text.strip()}, r


def run_identify(provider, model, key, script_text, min_confidence=0.5):
    prompt = _render(prompts.MODULE_D, script_text=script_text, min_confidence=min_confidence)
    r = call_llm(provider, model, key, prompt)
    data = _extract_json(r.text)
    return {"books": data.get("books", [])}, r


def run_split(provider, model, key, script_text, keyword=None, title=None, target_duration=26):
    prompt = _render(prompts.MODULE_F, script_text=script_text, keyword=keyword,
                     title=title, target_duration=target_duration)
    r = call_llm(provider, model, key, prompt)
    data = _extract_json(r.text)
    segs = data.get("segments", [])
    # 估算每段时长：中文约 5 字/秒
    segments = [{"text": s, "estimated_duration": max(1, round(len(s) / 5))} for s in segs]
    return {"segments": segments, "segment_count": len(segments)}, r


def run_compliance(provider, model, key, script, track="character_story"):
    """模块 H：先规则匹配（按赛道词库），再 LLM 语义判定，合并结果。"""
    matched = _rule_match(script, track)
    prompt = _render(prompts.MODULE_H, script=script)
    r = call_llm(provider, model, key, prompt)
    try:
        data = _extract_json(r.text)
    except (json.JSONDecodeError, ValueError):
        data = {"passed": True, "violations": [], "risk_score": 0.0}
    violations = data.get("violations", [])
    risk = _normalize_risk(data.get("risk_score", 0.0))
    # 规则命中高危词强制拉高风险
    if matched["high"]:
        risk = max(risk, 0.9)
        for w in matched["high"]:
            violations.append({"type": "违禁词", "snippet": w, "severity": "high",
                               "suggestion": "删除或改写"})
    passed = risk <= 0.7 and not any(v.get("severity") == "high" for v in violations)
    return {
        "passed": passed,
        "violations": violations,
        "risk_score": round(risk, 2),
        "matched_words": matched["high"] + matched["warn"],
        "needs_review": 0.3 < risk <= 0.7,
    }, r


def _normalize_risk(value) -> float:
    """把模型返回的 risk_score 归一化到 [0,1]。
    兼容模型误用 0-10 或 0-100 标度的情况。"""
    try:
        r = float(value)
    except (TypeError, ValueError):
        return 0.0
    if r < 0:
        return 0.0
    if r <= 1:
        return r
    if r <= 10:
        return r / 10
    if r <= 100:
        return r / 100
    return 1.0


# 违禁词库（PRD 10.2，可外置为配置文件）
_HIGH_WORDS = ["治愈", "根治", "100%有效", "包好", "彻底解决", "降血压", "治疗",
               "替代药物", "神效", "奇迹", "祖传秘方", "立竿见影",
               "加微信", "私信我", "点击主页"]
_WARN_WORDS = ["不看后悔", "致命"]

# 否定词：违禁词紧邻这些字眼时为合规表达（如"不求根治""无法治愈"），不算违规。
_NEGATIONS = ["不", "无", "非", "别", "勿", "没", "未"]


def _is_negated(text: str, pos: int) -> bool:
    """判断 text 中 pos 位置的违禁词是否处于否定语境（前 4 字内有否定词）。"""
    window = text[max(0, pos - 4):pos]
    return any(neg in window for neg in _NEGATIONS)


def _match_word(text: str, word: str) -> bool:
    """词出现且至少有一处非否定语境，才算命中。"""
    start = 0
    while True:
        pos = text.find(word, start)
        if pos == -1:
            return False
        if not _is_negated(text, pos):
            return True
        start = pos + len(word)


def _rule_match(text: str, track: str = "character_story"):
    """按赛道选词库。人物故事用赛道自带词库（史实/敏感），
    健康书单（或赛道未配词库）用内置医疗词库。"""
    from app.modules import tracks
    tk = tracks.get_track(track)
    high_words = tk.get("compliance_high") or _HIGH_WORDS
    warn_words = tk.get("compliance_warn") or _WARN_WORDS
    return {
        "high": [w for w in high_words if _match_word(text, w)],
        "warn": [w for w in warn_words if _match_word(text, w)],
    }
