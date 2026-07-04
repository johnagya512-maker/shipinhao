"""文案类模块 A/B/D/F/H。每个函数接收输入与 LLM 配置，返回结果与 token 用量。"""
import difflib
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


def run_clean(provider, model, key, transcript, keyword=None, title=None, author=None,
              protected_terms=None):
    prompt = _render(prompts.MODULE_A, transcript=transcript, keyword=keyword,
                     title=title, author=author,
                     protected_terms=protected_terms or "（无）")
    r: LLMResult = call_llm(provider, model, key, prompt)
    return {"cleaned_text": r.text.strip()}, r


def run_gen_title(provider, model, key, script, keyword=None):
    """生成简短钩子标题，用于自动命名剪映草稿/下载文件。
    返回 (标题 str, LLMResult)。截稿过长部分，标题净化去引号/换行/超长。"""
    snippet = (script or "")[:800]  # 标题只需开头大意，省 token
    prompt = _render(prompts.VIDEO_TITLE, script=snippet, keyword=keyword or "")
    r: LLMResult = call_llm(provider, model, key, prompt, temperature=1.0)
    title = r.text.strip().splitlines()[0] if r.text.strip() else ""
    title = title.strip().strip('"').strip("「」《》").strip()
    return title[:30], r


def run_gen_title_tags(provider, model, key, script, keyword=None, track="character_story"):
    """生成发布用的【短标题】+【长标题】+【热门话题标签】，供成品一并输出。
    按赛道注入对应的标题钩子风格（title_style），不同赛道标题调性不同。
    返回 ((short_title str, long_title str, hashtags list[str]), LLMResult)。
    锦上添花，解析失败时返回 ('', '', [])，不阻断出片。"""
    from app.modules import tracks
    snippet = (script or "")[:1000]
    title_style = tracks.get_track(track).get("title_style") or "制造好奇、勾起点击的悬念或反差钩子。"
    prompt = _render(prompts.TITLE_TAGS, script=snippet, keyword=keyword or "",
                     title_style=title_style)
    r: LLMResult = call_llm(provider, model, key, prompt, temperature=1.0)
    short_title, long_title, tags = "", "", []
    try:
        data = _extract_json(r.text)
        short_title = str(data.get("short_title") or "").strip().strip('"').strip("「」《》").strip()[:15]
        long_title = str(data.get("long_title") or "").strip().strip('"').strip("「」《》").strip()[:60]
        raw_tags = data.get("hashtags") or data.get("tags") or []
        if isinstance(raw_tags, list):
            for t in raw_tags:
                t = str(t).strip().lstrip("#").strip()
                if t and t not in tags:
                    tags.append(t[:12])
            tags = tags[:6]
    except Exception:
        pass
    return (short_title, long_title, tags), r


def run_gen_comment_cta(provider, model, key, script, book_title="", keyword=None):
    """生成发布后【评论区置顶下单引导话术】，仅图书带货模式调用。
    返回 ((pinned str, price_scarcity str, second_seed str), LLMResult)。
    锦上添花，解析失败时返回 ('', '', '')，不阻断出片。"""
    snippet = (script or "")[:1200]
    prompt = _render(prompts.COMMENT_CTA, script=snippet,
                     book_title=book_title or "", keyword=keyword or "")
    r: LLMResult = call_llm(provider, model, key, prompt)
    pinned, price_scarcity, second_seed = "", "", ""
    try:
        data = _extract_json(r.text)
        pinned = str(data.get("pinned") or "").strip().strip('"').strip("「」").strip()[:120]
        price_scarcity = str(data.get("price_scarcity") or "").strip().strip('"').strip("「」").strip()[:120]
        second_seed = str(data.get("second_seed") or "").strip().strip('"').strip("「」").strip()[:120]
    except Exception:
        pass
    return (pinned, price_scarcity, second_seed), r


def run_structure(provider, model, key, source_text):
    """拆解爆款文案结构，返回 ({structure: {...骨架...}}, LLMResult)。
    骨架是 why_viral/hook/structure/ending/rhythm/duration_hint 的 JSON。
    解析失败时返回空骨架（不阻断流程，B 改写自动退回无骨架模式）。"""
    prompt = _render(prompts.MODULE_STRUCTURE, source_text=source_text)
    r: LLMResult = call_llm(provider, model, key, prompt)
    try:
        data = _extract_json(r.text)
    except Exception:
        data = {}
    return {"structure": data}, r


def _structure_to_guide(structure: dict) -> str:
    """把结构骨架 dict 渲染成给 B 改写看的【手法级仿写指导】。空则返回空串。
    带上 why_viral、钩子原句+手法、逐段手法+心理、结尾原句+手法，让改写能照手法复刻。"""
    if not structure:
        return ""
    lines = []
    if structure.get("why_viral"):
        lines.append(f"◆ 这条为什么爆：{structure['why_viral']}")
        lines.append("  （改写时要复刻这个'爆点心理'，不只是套结构）")
    if structure.get("hook"):
        h = structure["hook"]
        lines.append(f"◆ 开头钩子（{h.get('type','')}）")
        if h.get("text"):
            lines.append(f"  范例原句：{h['text']}")
        if h.get("technique"):
            lines.append(f"  手法照搬：{h['technique']}")
        lines.append("  → 用同样的手法，给新内容写一个全新钩子（不要照抄原句，照搬的是'招式'）")
    parts = structure.get("structure") or []
    if parts:
        reorder = structure.get("_reorder", False)
        if reorder:
            lines.append("◆ 中段爆点手法（保留这些爆点机制，但自由重排顺序和叙述角度）：")
        else:
            lines.append("◆ 中段逐段手法（按顺序对照着写）：")
        for i, p in enumerate(parts, 1):
            seg = f"  {i}. 【{p.get('part','')}·{p.get('emotion','')}·{p.get('pace','')}】"
            if p.get("technique"):
                seg += f" 手法：{p['technique']}"
            if p.get("psychology"):
                seg += f"；要让观众感到：{p['psychology']}"
            lines.append(seg)
    if structure.get("ending"):
        e = structure["ending"]
        lines.append(f"◆ 结尾（{e.get('type','')}）")
        if e.get("text"):
            lines.append(f"  范例原句：{e['text']}")
        if e.get("technique"):
            lines.append(f"  手法照搬：{e['technique']}")
    if structure.get("rhythm"):
        lines.append(f"◆ 整体节奏：{structure['rhythm']}")
    return "\n".join(lines)


def run_rewrite(provider, model, key, cleaned_text, target_audience="50+女性", title=None,
                track="character_story", monetization_mode="revenue_share",
                rewrite_strength="medium", narrative_perspective="auto",
                structure_guide=None, lite=False, remix=False, book_remix=False,
                keyword="", author="", rewrite_notes=""):
    """B 改写。按赛道选提示词；人物故事赛道按变现模式切换结尾引导。
    rewrite_strength/narrative_perspective 作为附加指令注入提示词尾部。
    structure_guide（dict 骨架）非空时，要求改写严格复刻该爆款结构。
    lite=True 走手册轻量改写版（MODULE_B_LITE）：只改正文主体、保留原稿爆点、不激进重写、
    不杜撰，此模式忽略赛道/骨架/强度档（手册轻量流是单一通用提示词）。
    remix=True 走中度仿写版（MODULE_B_REMIX）：保留钩子类型/爆点顺序/情绪节奏，逐句重写措辞、
    连续雷同≤10字过查重；介于拆解结构(改最狠)与轻量(改最轻)之间，同样是单一通用提示词。
    book_remix=True 走图书带货深度二创版（MODULE_B_BOOK_REMIX）：保留开篇黄金钩子和末尾
    转化闭环100%，深度重构中段，通过信息重组/视角转换/句式重写等策略实现低相似度改写。"""
    from app.modules import tracks
    if lite:
        ending = (prompts.ENDING_BOOK_SALES if monetization_mode == "book_sales"
                  else prompts.ENDING_REVENUE_SHARE)
        prompt = _render(prompts.MODULE_B_LITE, cleaned_transcript=cleaned_text,
                         keyword=keyword or "", title=title or "", author=author or "",
                         rewrite_notes=rewrite_notes or "（无）",
                         ending_instruction=ending)
        r = call_llm(provider, model, key, prompt)
        return {"script": r.text.strip()}, r
    if remix:
        ending = (prompts.ENDING_BOOK_SALES if monetization_mode == "book_sales"
                  else prompts.ENDING_REVENUE_SHARE)
        prompt = _render(prompts.MODULE_B_REMIX, cleaned_transcript=cleaned_text,
                         keyword=keyword or "", title=title or "", author=author or "",
                         rewrite_notes=rewrite_notes or "（无）",
                         ending_instruction=ending)
        extra = _rewrite_directives(rewrite_strength, narrative_perspective)
        if extra:
            prompt = f"{prompt}\n\n【额外要求】\n{extra}"
        r = call_llm(provider, model, key, prompt, temperature=0.9)
        return {"script": r.text.strip()}, r
    if book_remix:
        ending = (prompts.ENDING_BOOK_SALES if monetization_mode == "book_sales"
                  else prompts.ENDING_REVENUE_SHARE)
        prompt = _render(prompts.MODULE_B_BOOK_REMIX, cleaned_transcript=cleaned_text,
                         keyword=keyword or "", title=title or "", author=author or "",
                         rewrite_notes=rewrite_notes or "（无）",
                         ending_instruction=ending)
        extra = _rewrite_directives(rewrite_strength, narrative_perspective)
        if extra:
            prompt = f"{prompt}\n\n【额外要求】\n{extra}"
        r = call_llm(provider, model, key, prompt, temperature=0.9)
        return {"script": r.text.strip()}, r
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
    guide = _structure_to_guide(structure_guide or {})
    if guide:
        # 有结构指导时，使用专用的"结构重写"prompt，不用基础赛道prompt。
        # 同时设置 _reorder=True，让 guide 去掉"按顺序"指令，改为"自由重排"。
        ending = (prompts.ENDING_BOOK_SALES if monetization_mode == "book_sales"
                  else prompts.ENDING_REVENUE_SHARE)
        reorder_guide = dict(structure_guide or {})
        reorder_guide["_reorder"] = True
        guide = _structure_to_guide(reorder_guide)
        prompt = _render(prompts.MODULE_B_STRUCTURE_REWRITE,
                         cleaned_text=cleaned_text,
                         target_audience=target_audience,
                         structure_guide=guide,
                         ending_instruction=ending)
        r = call_llm(provider, model, key, prompt, temperature=0.9)
        return {"script": r.text.strip()}, r
    r = call_llm(provider, model, key, prompt, temperature=0.7)
    return {"script": r.text.strip()}, r


def run_dedup(provider, model, key, cleaned_text, keyword="", title="", author="",
              protected_terms=None):
    """C 轻量去重微调（手册）：同号/矩阵号二次发布同一篇爆款前用，做克制微调避免判搬运。
    不在主流水线，独立调用。保留爆点/节奏/事实，字数差异建议 ≤8%。返回 {"script": 微调后正文}。"""
    prompt = _render(prompts.MODULE_C, cleaned_transcript=cleaned_text,
                     keyword=keyword or "", title=title or "", author=author or "",
                     protected_terms=protected_terms or "（无）")
    r = call_llm(provider, model, key, prompt)
    return {"script": r.text.strip()}, r


# 改写强度 / 叙事视角 → 注入提示词的附加指令。
_STRENGTH_TEXT = {
    "light": "改写力度轻：尽量保留原文措辞与结构，仅做必要的口播顺滑化。",
    "medium": "",  # 默认，无附加约束
    "strong": "改写力度强：必须大改措辞与句式，换开头说法、换叙述角度、重组句子，"
              "强化戏剧冲突与钩子。和原文逐句比，措辞要明显不同，但保留人物、事实、数据不变。",
}
_PERSPECTIVE_TEXT = {
    "auto": "",
    "first": "用第一人称（“我”）视角叙述，增强代入感。",
    "third": "用第三人称（“他/她”）视角客观叙述。",
}


def _rewrite_directives(strength: str, perspective: str) -> str:
    lines = [t for t in (_STRENGTH_TEXT.get(strength, ""), _PERSPECTIVE_TEXT.get(perspective, "")) if t]
    return "\n".join(lines)


def run_identify(provider, model, key, script_text, min_confidence=0.5,
                 existing_title="", existing_author="", keyword="",
                 source_title="", source_description=""):
    """D 识别书籍。提示词按手册：单本输出 {book_title,book_author,confidence,evidence}，
    作者带全角国别前缀、书名去书名号/营销词。
    为兼容下游（pick_main_book / build_image_prompts 读 books 列表的 title/category），
    把单本结果映射回 {"books":[{title,author,confidence,extracted_from,category}]}。"""
    prompt = _render(prompts.MODULE_D, script_text=(script_text or "")[:2600],
                     min_confidence=min_confidence,
                     existing_title=existing_title or "", existing_author=existing_author or "",
                     keyword=keyword or "", source_title=source_title or "",
                     source_description=source_description or "")
    r = call_llm(provider, model, key, prompt)
    data = _extract_json(r.text)
    # 兼容两种返回：手册单本对象 / 旧多本数组。统一归一成 books 列表给下游。
    if isinstance(data, dict) and "books" in data:
        books = data.get("books") or []
    else:
        title = (data.get("book_title") or "").strip()
        books = []
        if title:
            books = [{
                "title": title,
                "author": (data.get("book_author") or "").strip(),
                "confidence": data.get("confidence", 0),
                "extracted_from": (data.get("evidence") or "").strip(),
                "category": "",
            }]
    return {"books": books}, r


def run_split(provider, model, key, script_text, keyword=None, title=None, target_duration=26):
    prompt = _render(prompts.MODULE_F, script_text=script_text, keyword=keyword,
                     title=title, target_duration=target_duration)
    r = call_llm(provider, model, key, prompt)
    data = _extract_json(r.text)
    segs = data.get("segments", [])
    # 估算每段时长：中文约 5 字/秒
    segments = [{"text": s, "estimated_duration": max(1, round(len(s) / 5))} for s in segs]
    return {"segments": segments, "segment_count": len(segments)}, r


def run_split_for_storyboard(provider, model, key, script_text):
    """分镜专用分段：按视听叙事逻辑把文案拆成 50-80 字左右的原文段，用于"配音严格用原文"模式。
    失败时返回空列表，由调用方回退到规则切分（split_for_storyboard）。"""
    prompt = _render(prompts.MODULE_F_STORYBOARD, script_text=script_text)
    r = call_llm(provider, model, key, prompt)
    try:
        data = _extract_json(r.text)
        segs = data.get("segments", [])
    except Exception:
        segs = []
    return [str(s).strip() for s in segs if str(s).strip()]


def run_storyboard(provider, model, key, script_text, n_scenes, rewrite_focus="",
                   character_desc=None, character_keys=None):
    """Step「画面脚本」：把口播文案拆成 n_scenes 个分镜，每个分镜含：
      cap=对应口播原文, desc_prompt=完整绘图提示词, has_character=是否主角出场,
      character_key=出场角色标识（可选）。
    character_desc 非空时，要求人物出场的分镜把该特征写进 desc_prompt（角色一致性）。
    character_keys 非空时，要求 LLM 输出 character_key 以标识出场角色（匹配多参考图）。
    生成后做质检：检测批次内 desc_prompt 末尾模板化雷同，命中则让 LLM 整批重写一次。
    返回 {"scenes": [...], "diagnostic": {...}}, r。
    失败/数量不符时由调用方兜底。兼容老格式（纯字符串/desc 字段）。"""
    n = max(1, int(n_scenes))
    if character_desc:
        char_clause = (f"主角形象统一为：{character_desc}。"
                       "在 has_character=true 的分镜里，必须严格使用上述主角特征（尤其是性别）融进 desc_prompt，"
                       "【严禁改变主角性别】，保证是同一个人；不同分镜只变姿态/角度/环境/景别。"
                       f"主角性别必须是「{character_desc[:20]}」中描述的性别，不得生成异性角色。")
    else:
        char_clause = "若有主角贯穿，保持其外貌在各分镜一致（同一发型/脸型/服饰风格），只变姿态角度环境。"

    # 多角色参考图：让 LLM 输出 character_key 标识出场角色
    character_key_field, character_key_json = _build_character_key_clause(character_keys)

    prompt = _render(prompts.MODULE_S, script=script_text, n_scenes=n,
                     rewrite_focus=rewrite_focus or "", char_clause=char_clause,
                     character_key_field=character_key_field,
                     character_key_json=character_key_json)
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
                                     scenes_json=json.dumps(scenes, ensure_ascii=False),
                                     character_key_json=character_key_json)
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


def split_for_storyboard(script_text: str, target_chars: int = 40,
                         max_chars: int = 60) -> list[str]:
    """把文案按句子合并成约 target_chars 字一段的【原文切片】（一字不改），用于
    "配音严格用原文"模式：配音/字幕念这些切片，SB 为每片配画面，按段一一对齐。
    规则：先按句末标点切句，再贪心合并相邻短句到接近 target_chars；单句超 max_chars
    才在次级标点（逗号等）处断。保证每段都是原文连续片段、不增删字。"""
    import re as _re
    text = (script_text or "").strip()
    if not text:
        return []
    # 按句末标点切句（保留标点），跨行也算句界
    raw = _re.split(r"(?<=[。！？!?…])", _re.sub(r"[\r\n]+", "", text))
    sentences = [s.strip() for s in raw if s.strip()]
    if not sentences:
        sentences = [text]

    # 单句过长：在次级标点处再切，避免一段太长
    pieces: list[str] = []
    for s in sentences:
        if len(s) <= max_chars:
            pieces.append(s)
            continue
        sub = _re.split(r"(?<=[，,、；;：:])", s)
        buf = ""
        for p in sub:
            if not p:
                continue
            if len(buf) + len(p) <= max_chars:
                buf += p
            else:
                if buf:
                    pieces.append(buf)
                buf = p
        if buf:
            pieces.append(buf)

    # 贪心合并相邻短句到接近 target_chars（不超过 max_chars）
    out: list[str] = []
    buf = ""
    for p in pieces:
        if not buf:
            buf = p
        elif len(buf) + len(p) <= max_chars and len(buf) < target_chars:
            buf += p
        else:
            out.append(buf)
            buf = p
    if buf:
        out.append(buf)
    return out


def run_storyboard_for_segments(provider, model, key, segments, rewrite_focus="",
                                character_desc=None, character_keys=None):
    """【配音严格用原文】模式的画面脚本：输入已切好的原文段列表，为每段配 desc_prompt。
    cap 由程序强制填为原文段（LLM 不碰文案），保证配音/字幕念的就是你的原文、一字不改，
    且画面与配音按段号一一对齐。
    segments: [{"text": "原文段"}, ...]。返回 {"scenes":[{cap,desc_prompt,has_character,character_key}],...}, r。
    character_keys 非空时，要求 LLM 输出 character_key 以标识出场角色（匹配多参考图）。
    LLM 输出数量/解析异常时兜底：用原文段做 cap、desc_prompt 兜底为原文截断。"""
    texts = [str((s.get("text") if isinstance(s, dict) else s) or "").strip() for s in segments]
    texts = [t for t in texts if t]
    n = len(texts)
    if n == 0:
        return {"scenes": [], "diagnostic": {"fell_back": True}}, LLMResult(text="", tokens_in=0, tokens_out=0)

    if character_desc:
        char_clause = (f"主角形象统一为：{character_desc}。"
                       "在 has_character=true 的段里，必须严格使用上述主角特征（尤其是性别）融进 desc_prompt，"
                       "【严禁改变主角性别】，保证是同一个人；不同段只变姿态/角度/环境/景别。"
                       f"主角性别必须是「{character_desc[:20]}」中描述的性别，不得生成异性角色。")
    else:
        char_clause = "若有主角贯穿，保持其外貌在各段一致（同一发型/脸型/服饰风格），只变姿态角度环境。"

    # 多角色参考图：让 LLM 输出 character_key 标识出场角色
    character_key_field, character_key_json = _build_character_key_clause(character_keys)

    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(texts))
    prompt = _render(prompts.MODULE_S_BY_SEG, n_scenes=n, rewrite_focus=rewrite_focus or "",
                     char_clause=char_clause, numbered_segments=numbered,
                     character_key_field=character_key_field,
                     character_key_json=character_key_json)
    r = call_llm(provider, model, key, prompt)

    # 解析 LLM 的画面，按 seg 段号对齐回原文段；缺失的段用兜底画面。cap 一律用原文段。
    desc_by_seg: dict[int, dict] = {}
    try:
        data = _extract_json(r.text)
        for item in (data.get("scenes") or []):
            if not isinstance(item, dict):
                continue
            seg = item.get("seg")
            try:
                idx = int(seg) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= idx < n:
                desc_by_seg[idx] = {
                    "desc_prompt": str(item.get("desc_prompt") or "").strip(),
                    "has_character": bool(item.get("has_character", True)),
                    "character_key": str(item.get("character_key", "") or "").strip() or None,
                }
    except (json.JSONDecodeError, ValueError):
        pass

    scenes = []
    for i, text in enumerate(texts):
        d = desc_by_seg.get(i) or {}
        dp = d.get("desc_prompt") or text[:40]  # LLM 漏配的段：用原文截断兜底，不留空
        scenes.append({"id": i + 1, "cap": text,  # cap 强制=原文段，LLM 不碰
                       "desc_prompt": dp,
                       "has_character": d.get("has_character", True),
                       "character_key": d.get("character_key")})
    fell_back = len(desc_by_seg) < n
    return {"scenes": scenes, "diagnostic": {"fell_back": fell_back,
                                             "missing": n - len(desc_by_seg)}}, r


def _parse_scenes(raw) -> list[dict]:
    """把 scenes 原始输出归一成 [{"id", "cap", "desc_prompt", "has_character", "character_key"}]。
    兼容历史格式：dict 里旧字段名 desc → desc_prompt；纯字符串 → desc_prompt，默认有人物。
    character_key 标识该镜头出场的角色（用于匹配对应参考图），可选字段。"""
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
                "character_key": str(x.get("character_key", "") or "").strip() or None,
            })
        else:
            s = str(x).strip()
            if s:
                out.append({"id": i + 1, "cap": "", "desc_prompt": s,
                            "has_character": True, "character_key": None})
    return out


def _build_character_key_clause(character_keys: list[str] | None) -> tuple[str, str]:
    """根据可用的角色 key 列表，生成 SB 提示词中 character_key 字段说明和 JSON 示例片段。
    返回 (character_key_field, character_key_json)：
      - character_key_field: 插入到字段列表中的说明文字（无多角色时返回空字符串）
      - character_key_json: 插入到 JSON 示例中的片段（如 ',"character_key": "霍英东"'）
    """
    if not character_keys:
        return "", ""
    # 过滤空值
    keys = [k.strip() for k in character_keys if k and k.strip()]
    if not keys:
        return "", ""
    keys_str = "、".join(keys)
    field = (f"- character_key：此镜头出场角色的标识（从以下角色中选一个：{keys_str}）。"
             f"无人物出场或无法确定时输出 null。")
    # JSON 示例片段
    json_seg = f',"character_key": "{keys[0]}"'
    return field, json_seg


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


def run_character_profile(provider, model, key, image_data_uri, proxy=None, base_url=None):
    """反推主角参考图特征：用视觉模型看一次参考图，生成一段稳定外貌特征文字，
    用于后续「需要人物出场」的画面文字锚定角色一致性。返回 {"profile": str}, r。
    失败由调用方兜底（回退到通用一致性短语）。
    proxy 非空时视觉模型请求走代理（豆包 ark 域名受限网络需代理）。
    base_url 非空时走中转站（否则中转站 key 打官方会 401）。"""
    from app.services.llm import call_vision
    r = call_vision(provider, model, key, prompts.CHARACTER_PROFILE, image_data_uri,
                    proxy=proxy, base_url=base_url)
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
            # 必须含可朗读字符（汉字/字母/数字），跳过被切出的纯标点碎片（如单独的引号），
            # 否则下游 TTS 对纯标点段会报 No readable text。
            if p and re.search(r"[\w一-鿿]", p):
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
    # 合规拦截【只认规则词库的高危违禁词】（如奇迹/根治/病好了，确定性强、是真红线）。
    # LLM 的语义判定（修辞、转述、带书介绍等常被误判为违规）【仅作提醒】，
    # 保留在 violations 里展示，但不参与 passed —— 避免主观误判把正常文案卡死。
    if matched["high"]:
        risk = max(risk, 0.9)
        for w in matched["high"]:
            violations.append({"type": "违禁词", "snippet": w, "severity": "high",
                               "suggestion": "删除或改写"})
    # 通过条件：只要没命中规则库高危词就放行（LLM 主观高分不再硬拦，仅提示）。
    passed = not matched["high"]
    return {
        "passed": passed,
        "violations": violations,
        "risk_score": round(risk, 2),
        "matched_words": matched["high"] + matched["warn"],
        "needs_review": bool(violations) and passed,
    }, r


def run_compliance_fix(provider, model, key, script, violations, track="character_story"):
    """H 自动合规化改写：把违规文案按违规清单改成合规版，返回 {"script": 改后正文}。
    只软化违规表达，保留事实/数字/人名/书名/故事线。改后需由调用方重新跑 run_compliance 复审。"""
    vlist = "\n".join(
        f"- [{v.get('severity','')}] {v.get('type','')}：{v.get('snippet','')}"
        f"（建议：{v.get('suggestion','')}）"
        for v in (violations or [])
    ) or "（无具体清单，按通用合规规则全文软化）"
    prompt = _render(prompts.MODULE_H_FIX, script=script, violations=vlist)
    r = call_llm(provider, model, key, prompt)
    return {"script": r.text.strip()}, r


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


# 违禁词库（PRD 10.2 + 微信视频号运营规范，可外置为配置文件）
# 注：「治疗」不入库——它中性，正向引导（"配合治疗""及时就医治疗"）远多于违规用法，
# 裸词匹配会误杀合规的就医引导；真正的疗效承诺由"治愈/根治/包好/神效/替代药物"等覆盖，
# 另有 LLM 语义判定兜底。
_HIGH_WORDS = [
    # 医疗承诺/疗效断言（规范 5.15-5.17）
    "治愈", "根治", "100%有效", "包好", "彻底解决", "降血压",
    "替代药物", "神效", "奇迹", "祖传秘方", "立竿见影",
    "一招见效", "根治根除", "包治百病", "灵丹妙药",
    # 诱导导流（规范 4.4）
    "加微信", "私信我", "点击主页", "关注领取", "扫码进群",
    "点赞领取", "分享抽奖", "转发有礼", "集赞", "拆礼盒",
    # 胁迫煽动（规范 4.4.2）
    "不点赞不是", "不转不是", "是中国人就",
    # 低俗/性暗示（规范 5.9）
    "裸体", "暴露", "性暗示", "情趣", "隐私部位",
    # 伪科学/迷信（规范 5.13）
    "食物相克", "风水", "运势", "排毒功效",
    # 卖惨扮丑（规范 5.10）
    "卖惨", "扮丑",
    # 绝对化/夸大（规范 5.5）
    "暴瘦", "逆转", "飙升", "第一", "唯一", "最",
    # 血腥暴力（规范 5.8）
    "自杀", "自残", "尸体", "家暴",
]
_WARN_WORDS = [
    "不看后悔", "致命", "震惊", "速转", "紧急通知",
    "不看吃亏", "错过后悔", "赶紧转", "火速转发",
]

# 否定词：违禁词紧邻这些字眼时为合规表达（如"不求根治""无法治愈"），不算违规。
_NEGATIONS = [
    # 单字否定
    "不", "无", "非", "别", "勿", "没", "未",
    # 多字否定
    "没有", "无法", "并非", "从不", "不必", "无须", "难以", "不能", "不会", "未曾",
    "无须", "不用", "不该", "不可", "未曾", "并未",
]


def _is_negated(text: str, pos: int) -> bool:
    """判断 text 中 pos 位置的违禁词是否处于否定语境（前 12 字内有否定词）。"""
    window = text[max(0, pos - 12):pos]
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
    """按赛道选词库。优先用赛道自带词库（tracks.py 配置），
    未配置或空列表时回退到内置通用词库。"""
    from app.modules import tracks
    tk = tracks.get_track(track)
    high_words = tk.get("compliance_high") or _HIGH_WORDS
    warn_words = tk.get("compliance_warn") or _WARN_WORDS
    return {
        "high": [w for w in high_words if _match_word(text, w)],
        "warn": [w for w in warn_words if _match_word(text, w)],
    }


def _normalize_for_compare(text: str) -> str:
    """去除标点、空格、数字，保留汉字和字母；用 '|' 保留句界，
    避免跨句拼接后误把两段不相关的话算成连续雷同。"""
    sentences = re.split(r"(?<=[。！？!?；;…])", text)
    out: list[str] = []
    for s in sentences:
        s = re.sub(r"[\s\d]+", "", s)
        s = re.sub(r"[^\u4e00-\u9fa5a-zA-Z]", "", s)
        if s:
            out.append(s)
    return "|".join(out).lower()


def _split_sentences(text: str) -> list[str]:
    """按句末标点把文本切成句子列表，保留有效汉字/字母。"""
    raw = re.split(r"(?<=[。！？!?；;…])", text)
    out: list[str] = []
    for s in raw:
        s = re.sub(r"[^\u4e00-\u9fa5a-zA-Z]", "", s)
        if s:
            out.append(s)
    return out


def _sentence_pair_similarity(s1: str, s2: str) -> float:
    """单句相似度：基于最长公共子串 / 平均句长。"""
    if not s1 or not s2:
        return 0.0
    sm = difflib.SequenceMatcher(None, s1, s2)
    m = sm.find_longest_match(0, len(s1), 0, len(s2))
    avg_len = (len(s1) + len(s2)) / 2
    if avg_len == 0:
        return 0.0
    return m.size / avg_len


def _sentence_level_similarity(a: str, b: str) -> float:
    """句子级相似度：对改写稿每句，找原文最相似的句子，取平均。
    能发现"整句照搬但中间插了几个字"的改写偷懒。"""
    a_sents = _split_sentences(a)
    b_sents = _split_sentences(b)
    if not a_sents or not b_sents:
        return 0.0
    total = 0.0
    for bs in b_sents:
        best = max(_sentence_pair_similarity(bs, ast) for ast in a_sents)
        total += best
    return total / len(b_sents)


def _lcs_penalty(longest: int, avg_len: float) -> float:
    """长段直接复制的惩罚分：连续雷同越长惩罚越重，超过 25 字接近顶格。"""
    if longest < 8:
        return 0.0
    return min(1.0, longest / 25.0) * min(1.0, longest / max(avg_len, 1.0))


def _find_long_matches(a: str, b: str, min_len: int) -> list[str]:
    """用 SequenceMatcher 循环找出 a 与 b 中长度 >= min_len 的公共子串。
    每次找到后遮蔽该区域，避免重复命中同一位置。"""
    matches: list[str] = []
    a_work = a
    while True:
        sm = difflib.SequenceMatcher(None, a_work, b)
        m = sm.find_longest_match(0, len(a_work), 0, len(b))
        if m.size < min_len:
            break
        matches.append(a_work[m.a:m.a + m.size])
        a_work = a_work[:m.a] + "\x00" * m.size + a_work[m.a + m.size:]
    return matches


def _ngram_similarity(a: str, b: str, n: int = 3) -> float:
    """计算 a、b 的 n-gram 集合重叠率（以较小集合为分母）。"""
    if len(a) < n or len(b) < n:
        return 0.0
    a_grams = {a[i:i + n] for i in range(len(a) - n + 1)}
    b_grams = {b[i:i + n] for i in range(len(b) - n + 1)}
    if not a_grams or not b_grams:
        return 0.0
    inter = len(a_grams & b_grams)
    return inter / min(len(a_grams), len(b_grams))


def check_originality(original: str, rewritten: str,
                      min_overlap_chars: int = 12,
                      max_similarity_ratio: float = 0.40) -> dict:
    """本地原创度检测：多维度综合评估改写稿与原文的相似度。

    维度：
    - 3-gram / 5-gram / 7-gram 集合重叠率（捕捉词汇级、短语级雷同）
    - 句子级平均相似度（捕捉整句照搬/小改）
    - 最长连续公共子串（捕捉直接复制长段）

    综合相似度 = 0.30*sim3 + 0.25*sim5 + 0.20*sim7 + 0.15*sentence_sim + 0.10*lcs_penalty
    通过条件：综合相似度 < max_similarity_ratio 且 longest_overlap < min_overlap_chars。
    """
    a = _normalize_for_compare(original or "")
    b = _normalize_for_compare(rewritten or "")
    if not a or not b:
        return {
            "passed": True,
            "similarity_ratio": 0.0,
            "longest_overlap": 0,
            "overlap_fragments": [],
            "details": {"reason": "空文本，跳过检测"},
        }

    fragments = _find_long_matches(a, b, min_overlap_chars)
    longest = len(fragments[0]) if fragments else 0

    sim_3 = _ngram_similarity(a, b, n=3)
    sim_5 = _ngram_similarity(a, b, n=5)
    sim_7 = _ngram_similarity(a, b, n=7)
    sentence_sim = _sentence_level_similarity(a, b)
    avg_len = (len(a) + len(b)) / 2
    lcs_pen = _lcs_penalty(longest, avg_len)

    similarity_ratio = (
        0.30 * sim_3 +
        0.25 * sim_5 +
        0.20 * sim_7 +
        0.15 * sentence_sim +
        0.10 * lcs_pen
    )
    similarity_ratio = round(similarity_ratio, 4)

    # 长段直接复制做兜底：即使综合得分没超，连续雷同过长也判不通过
    passed = longest < min_overlap_chars and similarity_ratio < max_similarity_ratio

    return {
        "passed": bool(passed),
        "similarity_ratio": similarity_ratio,
        "longest_overlap": longest,
        "overlap_fragments": fragments,
        "details": {
            "min_overlap_chars": min_overlap_chars,
            "max_similarity_ratio": max_similarity_ratio,
            "3gram_similarity": round(sim_3, 4),
            "5gram_similarity": round(sim_5, 4),
            "7gram_similarity": round(sim_7, 4),
            "sentence_similarity": round(sentence_sim, 4),
            "lcs_penalty": round(lcs_pen, 4),
            "fragment_count": len(fragments),
        },
    }


def run_rewrite_decrease_similarity(provider, model, key, original: str, rewritten: str,
                                    check_result: dict) -> tuple[dict, LLMResult]:
    """根据原创度检测结果，对雷同片段进行针对性降重改写。
    返回 {"script": 改写后正文}, LLMResult。"""
    fragments = check_result.get("overlap_fragments") or []
    similarity_ratio = check_result.get("similarity_ratio", 0.0)
    longest = check_result.get("longest_overlap", 0)

    if fragments:
        fragments_text = "\n".join(f"- {f}" for f in fragments[:10])
    else:
        fragments_text = "（全文表达与原文过于接近，需要整体换说法）"

    prompt = f"""你是一位短视频口播文案降重专家。下面这段改写稿经程序检测与原文相似度过高，需要进一步降低字面相似度，但**必须保留爆款结构和情绪内核**，不能为了降重把稿子改废。

【原文】
{original}

【当前改写稿】
{rewritten}

【检测结果】
- 综合相似度：{similarity_ratio:.1%}
- 最长连续雷同：{longest} 字
- 必须改写的雷同片段：
{fragments_text}

【降重要求】
1. **只改字面和句式，不改爆款骨架**：
   - 保留开篇黄金钩子的「类型」和「心理机制」（悬念/反差/痛点/数字），可换措辞，不可把钩子改弱或改没。
   - 保留原文的情绪节奏和爆点推进顺序，不可打乱叙事逻辑。
   - 保留结尾转化/互动结构，只变表达。
2. **彻底改写雷同片段**：换句式、换角度、换措辞、调整信息顺序，禁止直接复制原文连续12字以上。
3. **保持核心事实、人物、时间、数据不变**；保持情绪内核和口播节奏。
4. 适合短视频口播，口语化自然，不要AI腔。
5. 输出完整改写后的正文，不要只输出修改部分，也不要输出解释。

请直接输降重后的完整正文："""

    r = call_llm(provider, model, key, prompt, temperature=0.9)
    return {"script": r.text.strip()}, r
