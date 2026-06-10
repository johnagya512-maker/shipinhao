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


def run_safe_rewrite(provider, model, key, prompt_text, attempt=1):
    """单句安全改写：把被绘图审核拦截的 desc_prompt 改写成含蓄安全版本。
    attempt 越大改写越激进（第2/3次彻底抛弃原画面、只保留情绪氛围）。
    返回 (新提示词 str, LLMResult)。失败时返回原文。"""
    if attempt >= 3:
        escalate = ("\n【这是第三次改写，前两次仍被拒】请彻底放弃原画面的具体物件，"
                    "只画一个纯安全的空镜/意境画面（如书桌、窗景、自然风光、光影），"
                    "保留与口播相符的情绪氛围即可，绝不能再出现任何地图/政治/暴力元素。\n")
    elif attempt == 2:
        escalate = ("\n【这是第二次改写，上一版仍被拒】请更大胆地删改，把所有可能敏感的"
                    "具体物件（尤其地图、标记、武器、人物姓名）整个替换成安全的环境/物件画面。\n")
    else:
        escalate = ""
    rendered = _render(prompts.SAFE_PROMPT_REWRITE, prompt=prompt_text, escalate=escalate)
    r: LLMResult = call_llm(provider, model, key, rendered)
    new_prompt = r.text.strip().strip('"').strip("「」").strip()
    return (new_prompt or prompt_text), r


def run_clean(provider, model, key, transcript, keyword=None, title=None, author=None):
    prompt = _render(prompts.MODULE_A, transcript=transcript, keyword=keyword,
                     title=title, author=author)
    r: LLMResult = call_llm(provider, model, key, prompt)
    return {"cleaned_text": r.text.strip()}, r


def run_gen_title(provider, model, key, script, keyword=None):
    """生成简短钩子标题，用于自动命名剪映草稿/下载文件。
    返回 (标题 str, LLMResult)。截稿过长部分，标题净化去引号/换行/超长。"""
    snippet = (script or "")[:800]  # 标题只需开头大意，省 token
    prompt = _render(prompts.VIDEO_TITLE, script=snippet, keyword=keyword or "")
    r: LLMResult = call_llm(provider, model, key, prompt)
    title = r.text.strip().splitlines()[0] if r.text.strip() else ""
    title = title.strip().strip('"').strip("「」《》").strip()
    return title[:30], r


def run_rewrite(provider, model, key, cleaned_text, target_audience="50+女性", title=None,
                track="character_story", monetization_mode="revenue_share",
                rewrite_strength="medium", narrative_perspective="auto"):
    """B 改写。按赛道选提示词；人物故事赛道按变现模式切换结尾引导。
    rewrite_strength/narrative_perspective 作为附加指令注入提示词尾部。"""
    from app.modules import tracks
    extra = _rewrite_directives(rewrite_strength, narrative_perspective)
    tk = tracks.get_track(track)
    # 结尾引导按变现模式切换，所有赛道通用：创作分成=引爆互动；图书带货=带出书籍。
    ending = (prompts.ENDING_BOOK_SALES if monetization_mode == "book_sales"
              else prompts.ENDING_REVENUE_SHARE)
    if track == "character_story":
        prompt = _render(prompts.MODULE_B_CHARACTER, cleaned_text=cleaned_text, title=title,
                         target_audience=target_audience,
                         rewrite_focus=tk["rewrite_focus"],
                         ending_instruction=ending)
    elif track == "health_book":
        prompt = _render(prompts.MODULE_B, cleaned_text=cleaned_text,
                         target_audience=target_audience, title=title,
                         ending_instruction=ending)
    else:
        # 其它赛道走通用提示词，注入该赛道的改写风格，不再套健康书单逻辑。
        prompt = _render(prompts.MODULE_B_GENERAL, cleaned_text=cleaned_text,
                         target_audience=target_audience, title=title,
                         rewrite_focus=tk["rewrite_focus"],
                         ending_instruction=ending)
    if extra:
        prompt = f"{prompt}\n\n【额外要求】\n{extra}"
    r = call_llm(provider, model, key, prompt)
    return {"script": r.text.strip()}, r


# 改写强度 / 叙事视角 → 注入提示词的附加指令。
_STRENGTH_TEXT = {
    "light": "改写力度轻：尽量保留原文措辞与结构，仅做必要的口播顺滑化。",
    "medium": "",  # 默认，无附加约束
    "strong": "改写力度强：大胆重组叙事、强化戏剧冲突与钩子，可显著改写措辞。",
}
_PERSPECTIVE_TEXT = {
    "auto": "",
    "first": "用第一人称（“我”）视角叙述，增强代入感。",
    "third": "用第三人称（“他/她”）视角客观叙述。",
}


def _rewrite_directives(strength: str, perspective: str) -> str:
    lines = [t for t in (_STRENGTH_TEXT.get(strength, ""), _PERSPECTIVE_TEXT.get(perspective, "")) if t]
    return "\n".join(lines)


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


def run_storyboard(provider, model, key, script_text, n_scenes, rewrite_focus="",
                   character_desc=None):
    """Step「画面脚本」：把口播文案拆成 n_scenes 个分镜，每个分镜含：
      cap=对应口播原文, desc_prompt=完整绘图提示词, has_character=是否主角出场。
    character_desc 非空时，要求人物出场的分镜把该特征写进 desc_prompt（角色一致性）。
    生成后做质检：检测批次内 desc_prompt 末尾模板化雷同，命中则让 LLM 整批重写一次。
    返回 {"scenes": [...], "diagnostic": {...}}, r。
    失败/数量不符时由调用方兜底。兼容老格式（纯字符串/desc 字段）。"""
    n = max(1, int(n_scenes))
    if character_desc:
        char_clause = (f"主角形象统一为：{character_desc}。"
                       "在 has_character=true 的分镜里，把这个主角特征自然融进 desc_prompt，"
                       "保证是同一个人；不同分镜只变姿态/角度/环境/景别。")
    else:
        char_clause = "若有主角贯穿，保持其外貌在各分镜一致（同一发型/脸型/服饰风格），只变姿态角度环境。"

    prompt = _render(prompts.MODULE_S, script=script_text, n_scenes=n,
                     rewrite_focus=rewrite_focus or "", char_clause=char_clause)
    r = call_llm(provider, model, key, prompt)
    try:
        data = _extract_json(r.text)
        scenes = _parse_scenes(data.get("scenes", []))
    except (json.JSONDecodeError, ValueError):
        scenes = []

    diagnostic = {"attempts": [], "fell_back": False}
    # 质检：批次内 desc_prompt 末尾模板化雷同 = LLM 偷懒，画面会同质化，打回重写一次。
    reason = _detect_template_tail(scenes)
    if reason and scenes:
        diagnostic["attempts"].append({"kind": "validator_reject", "reason": reason})
        try:
            rewrite_prompt = _render(prompts.MODULE_S_REWRITE, reason=reason,
                                     char_clause=char_clause,
                                     scenes_json=json.dumps(scenes, ensure_ascii=False))
            r2 = call_llm(provider, model, key, rewrite_prompt)
            data2 = _extract_json(r2.text)
            rewritten = _parse_scenes(data2.get("scenes", []))
            if rewritten and not _detect_template_tail(rewritten):
                scenes = rewritten
            else:
                diagnostic["fell_back"] = True
            # 重写这次的 token 也计入（累加到返回的 LLMResult）
            r = LLMResult(text=r.text, tokens_in=r.tokens_in + r2.tokens_in,
                          tokens_out=r.tokens_out + r2.tokens_out)
        except (json.JSONDecodeError, ValueError, Exception):
            diagnostic["fell_back"] = True

    return {"scenes": scenes, "diagnostic": diagnostic}, r


def _parse_scenes(raw) -> list[dict]:
    """把 scenes 原始输出归一成 [{"id", "cap", "desc_prompt", "has_character"}]。
    兼容历史格式：dict 里旧字段名 desc → desc_prompt；纯字符串 → desc_prompt，默认有人物。"""
    out = []
    for i, x in enumerate(raw):
        if isinstance(x, dict):
            dp = str(x.get("desc_prompt") or x.get("desc") or "").strip()
            if not dp:
                continue
            out.append({
                "id": i + 1,
                "cap": str(x.get("cap", "")).strip(),
                "desc_prompt": dp,
                "has_character": bool(x.get("has_character", True)),
            })
        else:
            s = str(x).strip()
            if s:
                out.append({"id": i + 1, "cap": "", "desc_prompt": s, "has_character": True})
    return out


def _detect_template_tail(scenes, min_batch=4, tail_len=12) -> str | None:
    """质检：检测批次内多数 desc_prompt 末尾相同（模板化尾巴），返回打回原因，否则 None。
    竞品 step3-diagnostic 同思路——LLM 偷懒会给每句套同一个机械结尾，导致画面同质化。"""
    prompts_list = [s.get("desc_prompt", "") for s in scenes if s.get("desc_prompt")]
    if len(prompts_list) < min_batch:
        return None
    from collections import Counter
    tails = Counter(p[-tail_len:] for p in prompts_list if len(p) >= tail_len)
    if not tails:
        return None
    tail, cnt = tails.most_common(1)[0]
    # 超过半数句子末尾雷同 → 判定模板化
    if cnt >= max(min_batch, len(prompts_list) // 2 + 1):
        return (f"批次内 {cnt}/{len(prompts_list)} 条 desc_prompt 末尾相同 “…{tail}”，"
                "明显是模板化尾巴，会导致画面同质化、缺乏视觉变化。")
    return None


def run_character_profile(provider, model, key, image_data_uri):
    """反推主角参考图特征：用视觉模型看一次参考图，生成一段稳定外貌特征文字，
    用于后续「需要人物出场」的画面文字锚定角色一致性。返回 {"profile": str}, r。
    失败由调用方兜底（回退到通用一致性短语）。"""
    from app.services.llm import call_vision
    r = call_vision(provider, model, key, prompts.CHARACTER_PROFILE, image_data_uri)
    profile = r.text.strip().strip('"').strip()
    return {"profile": profile}, r


def mechanical_split(script_text: str):
    """直接出片模式的机械切分：按换行/句末标点切句，不调 LLM、不计费。
    用于 processing_mode=direct——保留原文措辞，仅做口播分段。
    返回与 run_split 同构的 dict（无 LLMResult）。"""
    raw_lines = [ln.strip() for ln in re.split(r"[\r\n]+", script_text) if ln.strip()]
    segs: list[str] = []
    for line in raw_lines:
        parts = re.split(r"(?<=[。！？!?…；;])", line)
        for p in parts:
            p = p.strip()
            if p:
                segs.append(p)
    if not segs:  # 无标点的整段：兜底按长度切，约 30 字一句
        segs = [script_text[i:i + 30] for i in range(0, len(script_text), 30)] or [script_text]
    segments = [{"text": s, "estimated_duration": max(1, round(len(s) / 5))} for s in segs]
    return {"segments": segments, "segment_count": len(segments)}


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
