"""任务编排引擎。串联各模块，管理状态机、成本与超时（PRD 5.3/9.x/11.x）。

执行顺序：A 清洗 → B 改写 → H 合规 → (D 识别) → E 配图 → F 分段。
G 视频合成在用户上传音频后单独触发（见 services/compose）。
"""
import logging
import time
from datetime import datetime
from pathlib import Path
from sqlalchemy.orm import Session

logger = logging.getLogger("uvicorn")

from app.models import Task, ModuleResult, Config
from app.core.config import settings
from app.core.paths import storage_root
from app.core.security import decrypt
from app.services import cost as cost_svc
from app.services import collect as collect_svc
from app.services import asr as asr_svc
from app.services import tts as tts_svc
from app.modules import text_modules as tm
from app.modules import image_module as im
from app.modules import tracks
from app.modules.retry import with_retry

LLM_RETRY = 2


class TaskAborted(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class _Paused(Exception):
    """内部信号：命中暂停点，需停下等用户确认（非错误）。"""
    def __init__(self, step: str):
        self.step = step


class _Cancelled(Exception):
    """内部信号：用户取消了任务，需立即停下后台 pipeline（非错误，状态已是 cancelled）。"""
    pass


def _step_order(task) -> list[str]:
    """本次任务实际会执行、且可作为暂停点的 step 序列（按执行先后）。
    B 改写仅 full_auto 跑；E 配图需在 modules 中。"""
    steps: list[str] = []
    if task.processing_mode == "full_auto":
        steps.append("B")          # Step1 智能改写
    steps += ["H", "F"]            # Step0 合规闸门 / Step2 分句分镜
    if "E" in (task.modules or []):
        steps += ["P", "E"]        # Step3 提示词 / Step4 批量生图
    steps.append("T")              # Step5 配音
    return steps


def _key_nodes(steps: list[str]) -> set[str]:
    """关键节点：改写稿（无改写则退回合规闸门）+ 批量生图。"""
    nodes = {"B" if "B" in steps else "H"}
    if "E" in steps:
        nodes.add("E")
    return nodes


def _should_pause(task, step: str) -> bool:
    """该 step 刚“新算完”后是否应暂停等确认。"""
    mode = task.pause_mode or "none"
    if mode == "none":
        return False
    steps = _step_order(task)
    if step not in steps:
        return False
    if mode == "every_step":
        return True
    if mode == "key_nodes":
        return step in _key_nodes(steps)
    if mode == "custom":
        return step in set(task.pause_steps or [])
    return False


def _maybe_pause(db: Session, task: Task, step: str):
    """新算完一个 step 后调用；命中暂停点则抛 _Paused。"""
    if _should_pause(task, step):
        raise _Paused(step)



def _get_result(db: Session, task_id: str, module: str) -> ModuleResult | None:
    return db.query(ModuleResult).filter_by(task_id=task_id, module=module).first()


def _merge_segments_evenly(parts: list[str], n: int) -> list[str]:
    """把 parts 个原文切片【均匀合并】成 n 段（相邻拼接，一字不改、不丢内容）。
    用于切片数超过出图上限时压到 n 段：前 (len%n) 段各多 1 个切片，其余各少 1 个，
    使每段长度尽量均衡。绝不把超出部分全堆到最后一段（否则最后一镜吞大半脚本→配音几百秒→
    视频里一张图定格几分钟）。返回非空段列表。"""
    n = max(1, n)
    total = len(parts)
    if total <= n:
        return list(parts)
    base, extra = divmod(total, n)
    out, idx = [], 0
    for g in range(n):
        size = base + (1 if g < extra else 0)
        chunk = "".join(parts[idx:idx + size])
        idx += size
        if chunk:
            out.append(chunk)
    return out


def _merge_scenes_evenly(scenes: list[dict], n: int) -> list[dict]:
    """把多个 SB 分镜均匀合并成 n 个分镜，保证末尾文案不被截掉。
    cap 拼接（保留全文），desc_prompt 取每组最后一个分镜的画面描述，
    has_character 只要组内有一个 True 就为 True。"""
    n = max(1, n)
    total = len(scenes)
    if total <= n:
        return list(scenes)
    base, extra = divmod(total, n)
    out, idx = [], 0
    for g in range(n):
        size = base + (1 if g < extra else 0)
        group = scenes[idx:idx + size]
        idx += size
        merged = {
            "cap": "".join(str(s.get("cap", "") or "") for s in group),
            "desc_prompt": str(group[-1].get("desc_prompt", "") or ""),
            "has_character": any(s.get("has_character", True) for s in group),
            "character_key": group[-1].get("character_key"),
        }
        out.append(merged)
    return out


def _voice_segments_from_scenes(db: Session, task_id: str, fallback_segments: list) -> tuple[list, str]:
    """配音/字幕分段统一取自分镜 cap（与图片同源 → 图-字-音三轨一一对齐）。
    从 P 产物读 scenes，每个分镜的 cap 作为一段配音文本；返回 ([{"text": cap}, ...], "scene")。
    若 P 无 scenes、或所有 cap 为空（SB 失败/老任务）→ 回退 F 的 segments，返回 (segments, "segment")。
    关键：返回的分段数必须 == 图数（分镜数），compose 才能按下标让图压在对应配音上。"""
    p = _get_result(db, task_id, "P")
    scenes = (p.output or {}).get("scenes") if p and p.output else None
    if scenes:
        segs = [{"text": str(s.get("cap", "") or "").strip()} for s in scenes]
        # 只要有任意一段有真实文案就用分镜源；cap 为空的段保留占位（计 0 时长，不打乱下标对齐）。
        if any(s["text"] for s in segs):
            return segs, "scene"
    return fallback_segments, "segment"


def _save_result(db: Session, task_id: str, module: str, status: str,
                 output=None, cost=0.0, tokens_in=None, tokens_out=None, retry=0):
    mr = _get_result(db, task_id, module)
    if not mr:
        mr = ModuleResult(task_id=task_id, module=module)
        db.add(mr)
    if mr.started_at is None:
        mr.started_at = datetime.utcnow()
    mr.status = status
    mr.output = output
    mr.cost = cost
    mr.tokens_in = tokens_in
    mr.tokens_out = tokens_out
    mr.retry_count = retry
    mr.finished_at = datetime.utcnow()
    db.commit()
    return mr


def _check_limits(db: Session, task: Task, started: float):
    """超时/成本上限/用户取消检查（PRD 11.2）。所有烧钱步骤前后调用，
    任一命中即抛异常停下后台 pipeline，避免取消/超限后还继续烧钱。"""
    # 取消检查：cancel 接口在另一线程改了 DB 状态，本线程的 task 是旧快照，必须重读。
    # 发现已被取消就立即停（抛 _Cancelled，由上层干净退出，不当失败处理）。
    db.refresh(task)
    if task.status == "cancelled":
        raise _Cancelled()
    if time.time() - started > task.time_limit:
        raise TaskAborted("TIMEOUT", "处理超时")
    if float(task.total_cost) > float(task.cost_limit):
        raise TaskAborted("COST_EXCEEDED", "成本超过上限")


def _llm_step(db, task, cfg, llm_key, module, fn, started, pausable=True, cache_key=None):
    """执行一个 LLM 模块：断点复用 + 重试 + 计费 + 限额检查。返回 output。
    pausable=True 时，仅在“本次新算完”后检查暂停点（缓存复用直接返回，不再暂停，
    保证 resume 能越过上次的暂停点继续）。
    cache_key 缺省时等于 module；同一 module 需要多份缓存（如多角色 CP 各存各的）时传入
    互不相同的 cache_key，同时 module 仍传原名以保持暂停点/计费口径按步骤名归并。"""
    ck = cache_key or module
    existing = _get_result(db, task.id, ck)
    if existing and existing.status == "success":
        return existing.output  # 断点续跑：复用已成功结果，不重复扣费，也不再触发暂停

    def _do():
        return fn()

    (output, llm_result), attempts = with_retry(_do, LLM_RETRY)
    c = cost_svc.actual_llm_cost(llm_result.tokens_in, llm_result.tokens_out, cfg.llm_provider)
    cost_svc.record_cost(db, task.id, module, cfg.llm_provider, c)
    task.total_cost = float(task.total_cost) + c
    db.commit()
    _save_result(db, task.id, ck, "success", output=output, cost=c,
                 tokens_in=llm_result.tokens_in, tokens_out=llm_result.tokens_out, retry=attempts)
    _check_limits(db, task, started)
    if pausable:
        _maybe_pause(db, task, module)
    return output


def run_pipeline(db: Session, task_id: str):
    """执行文案+配图流水线（不含 G，G 在音频上传后触发）。"""
    task = db.get(Task, task_id)
    cfg = db.get(Config, 1)
    if not task or not cfg:
        return
    # 排队期间可能已被取消：轮到执行时若已是终态，直接跳过。
    if task.status in ("cancelled", "completed", "failed"):
        return
    if cost_svc.daily_cap_reached(db):
        _fail(db, task, "E2003", "每日成本上限已达")
        return

    llm_key = decrypt(cfg.llm_api_key_enc) if cfg.llm_api_key_enc else ""
    img_key = decrypt(cfg.image_api_key_enc) if cfg.image_api_key_enc else ""
    started = time.time()
    task.status = "processing"
    db.commit()

    try:
        # 前置：抖音采集 + ASR（仅当提供了 douyin_url）。
        # 无对应 Key 时降级——要求 transcript 已手填，否则失败提示。
        if task.douyin_url and not (task.transcript or "").strip():
            _run_collect_asr(db, task, cfg, started)

        if not (task.transcript or "").strip():
            raise TaskAborted("E6001", "无逐字稿：请填写抖音链接（已配采集/ASR Key）或手动粘贴逐字稿")

        # 链接模式下提交时按占位长度估算；ASR 拿到真稿后按真实长度复估，
        # 超限则在烧 A/B 等 LLM 成本前快速失败（避免半途触发运行时闸门）。
        if task.douyin_url:
            real_est = cost_svc.estimate_cost(task.transcript, task.modules, None,
                                              cfg.llm_provider, cfg.image_provider,
                                              getattr(task, "image_gen_mode", "per_image"),
                                              getattr(cfg, "image_unit_price", None))
            if real_est > float(task.cost_limit):
                raise TaskAborted("E2002",
                                  f"采集转写后预估成本 {real_est} 元超过上限 {task.cost_limit} 元")

        # A 清洗（semi_auto/direct=「不改文案」跳过清洗，原文一字不改、不调 LLM、不计费；
        # full_auto 仍做清洗）
        # 注意：不能复用旧 A 结果——任务可能之前以 full_auto 跑过，旧 A 是 LLM 清洗版会丢末尾 CTA。
        if task.processing_mode in ("direct", "semi_auto"):
            cleaned = task.transcript
            _save_result(db, task.id, "A", "success", output={"cleaned_text": cleaned})
        else:
            a_out = _llm_step(db, task, cfg, llm_key, "A",
                              lambda: tm.run_clean(cfg.llm_provider, cfg.llm_model, llm_key,
                                                   task.transcript, task.keyword, task.title, task.author),
                              started, pausable=False)
            cleaned = a_out["cleaned_text"]

        # keyword 兜底提取（PRD 9.2）：未填则用 A 输出首句近似
        if not task.keyword:
            task.keyword = cleaned[:8]
            db.commit()

        # B 改写（仅 full_auto 跑；semi_auto/direct 直接用清洗稿，不改写）
        if task.processing_mode == "full_auto":
            _mode = getattr(task, "creation_mode", "same_topic")
            if _mode == "lite":
                # 手册轻量改写：只改正文主体、保留原稿爆点、不激进重写。跳过结构拆解。
                b_out = _llm_step(db, task, cfg, llm_key, "B",
                                  lambda: tm.run_rewrite(cfg.llm_provider, cfg.llm_model, llm_key,
                                                         cleaned, task.target_audience, task.title,
                                                         lite=True,
                                                         monetization_mode=task.monetization_mode,
                                                         keyword=task.keyword or "",
                                                         author=getattr(task, "author", "") or ""),
                                  started)
                script = b_out["script"]
            elif _mode == "remix":
                # 中度仿写：保留钩子类型/爆点顺序/情绪节奏，逐句重写措辞、连续雷同≤10字。跳过结构拆解。
                b_out = _llm_step(db, task, cfg, llm_key, "B",
                                  lambda: tm.run_rewrite(cfg.llm_provider, cfg.llm_model, llm_key,
                                                         cleaned, task.target_audience, task.title,
                                                         remix=True,
                                                         monetization_mode=task.monetization_mode,
                                                         rewrite_strength=task.rewrite_strength,
                                                         narrative_perspective=task.narrative_perspective,
                                                         keyword=task.keyword or "",
                                                         author=getattr(task, "author", "") or ""),
                                  started)
                script = b_out["script"]
            elif _mode == "book_remix":
                # 图书带货深度二创：保留开篇黄金钩子和末尾转化闭环100%，深度重构中段。跳过结构拆解。
                b_out = _llm_step(db, task, cfg, llm_key, "B",
                                  lambda: tm.run_rewrite(cfg.llm_provider, cfg.llm_model, llm_key,
                                                         cleaned, task.target_audience, task.title,
                                                         book_remix=True,
                                                         monetization_mode=task.monetization_mode,
                                                         rewrite_strength=task.rewrite_strength,
                                                         narrative_perspective=task.narrative_perspective,
                                                         keyword=task.keyword or "",
                                                         author=getattr(task, "author", "") or ""),
                                  started)
                script = b_out["script"]
            else:
                # S2 结构拆解：creation_mode != none 时，先拆出爆款结构骨架，
                # 供 B 改写复刻其节奏。拆解失败不阻断（骨架为空即退回普通改写）。
                structure = None
                if _mode != "none":
                    try:
                        s_out = _llm_step(db, task, cfg, llm_key, "S2",
                                          lambda: tm.run_structure(cfg.llm_provider, cfg.llm_model,
                                                                   llm_key, cleaned),
                                          started, pausable=False)
                        structure = (s_out or {}).get("structure") or None
                    except Exception:
                        structure = None
                b_out = _llm_step(db, task, cfg, llm_key, "B",
                                  lambda: tm.run_rewrite(cfg.llm_provider, cfg.llm_model, llm_key,
                                                         cleaned, task.target_audience, task.title,
                                                         track=task.track,
                                                         monetization_mode=task.monetization_mode,
                                                         rewrite_strength=task.rewrite_strength,
                                                         narrative_perspective=task.narrative_perspective,
                                                         structure_guide=structure),
                                  started)
                script = b_out["script"]
        else:
            script = cleaned

        # O 原创度检测（full_auto 且非 lite 模式）：改写后与原文对比，
        # 相似度过高则自动触发一次针对性降重。
        if task.processing_mode == "full_auto" and _mode != "lite":
            _o_max_rounds = 2
            for _o_round in range(_o_max_rounds):  # 首次检测 + 一次自动降重后复测
                o_result = tm.check_originality(cleaned, script,
                                                 max_similarity_ratio=0.40)
                _save_result(db, task.id, "O", "success", output=o_result)
                # 最后一轮：只检测不再改写，避免"改写完不复检"导致 O 结果与 B 最终稿脱节
                # （旧代码每轮先检后写，最后一轮写完循环即结束，O 停在改写前的旧结果上）。
                if o_result["passed"] or _o_round == _o_max_rounds - 1:
                    break
                try:
                    fix_out, _or = tm.run_rewrite_decrease_similarity(
                        cfg.llm_provider, cfg.llm_model, llm_key,
                        cleaned, script, o_result)
                    new_script = (fix_out or {}).get("script", "").strip()
                    if new_script and new_script != script:
                        script = new_script
                        # 回写 B 产物，让详情页/后续分句用降重后的稿
                        _save_result(db, task.id, "B", "success",
                                     output={"script": script})
                        # BUG-17: 用 rebill 替代累加，防止多次降重把 O 模块成本重复叠加
                        c = cost_svc.actual_llm_cost(_or.tokens_in, _or.tokens_out,
                                                     cfg.llm_provider)
                        cost_svc.rebill_module(db, task, "O", cfg.llm_provider, c)
                    else:
                        break
                except Exception:
                    break

        # 自动命名：用户没填标题时，让 LLM 读定稿文案生成一个钩子短标题，
        # 用于剪映草稿箱/下载文件名一眼识别（如「屠呦呦·190次失败」）。
        # title 过长（>30字）多半是误填或复用草稿残留的采集长描述/上一篇正文，
        # 视作"无有效短标题"一并重新生成，避免串台。锦上添花，失败不阻断。
        cur_title = (task.title or "").strip()
        if not cur_title or len(cur_title) > 30:
            try:
                gen_title, _tr = tm.run_gen_title(cfg.llm_provider, cfg.llm_model,
                                                  llm_key, script, task.keyword)
                task.title = (gen_title or task.keyword or "").strip() or None
                db.commit()
            except Exception:
                task.title = cur_title or (task.keyword or None)
                db.commit()

        # 成品物料：短标题 + 长标题 + 热门话题标签（发布时直接可用）。锦上添花，失败不阻断。
        if not (task.short_title or "").strip() or not (task.long_title or "").strip() or not task.hashtags:
            try:
                (short_title, long_title, tags), _tt = tm.run_gen_title_tags(
                    cfg.llm_provider, cfg.llm_model, llm_key, script, task.keyword,
                    track=task.track)
                if short_title and not (task.short_title or "").strip():
                    task.short_title = short_title
                if long_title and not (task.long_title or "").strip():
                    task.long_title = long_title
                if tags and not task.hashtags:
                    task.hashtags = tags
                db.commit()
            except Exception:
                db.rollback()

        # H 合规闸门：仅 full_auto 模式跑；不改写模式（direct/semi_auto）完全跳过，
        # 用户原文一字不动直接继续，不检测、不改写、不暂停。
        if task.processing_mode == "full_auto":
            h_out = _llm_step(db, task, cfg, llm_key, "H",
                              lambda: tm.run_compliance(cfg.llm_provider, cfg.llm_model, llm_key,
                                                        script, track=task.track),
                              started, pausable=False)
            # 不通过 → 自动合规化改写后重审，最多 2 轮；过了就用改后稿继续。
            # awaiting_user_confirm 标记：resume 复用 H 产物时跳过本段，避免无限改写/暂停。
            _fix_round = 0
            while (not h_out["passed"] and not h_out.get("awaiting_user_confirm")
                   and _fix_round < 2):
                _fix_round += 1
                try:
                    fix_out, _fr = tm.run_compliance_fix(
                        cfg.llm_provider, cfg.llm_model, llm_key,
                        script, h_out.get("violations") or [], track=task.track)
                except Exception:
                    break
                new_script = (fix_out or {}).get("script", "").strip()
                if not new_script or new_script == script:
                    break
                script = new_script
                _save_result(db, task.id, "B", "success", output={"script": script})
                h_chk = tm.run_compliance(cfg.llm_provider, cfg.llm_model, llm_key,
                                          script, track=task.track)
                h_out = h_chk[0] if isinstance(h_chk, tuple) else h_chk
                h_out["auto_fixed"] = _fix_round
                _save_result(db, task.id, "H", "success", output=h_out)
            # 仍不通过：停在文案确认关卡交用户定夺。
            if not h_out["passed"] and not h_out.get("awaiting_user_confirm"):
                h_out["awaiting_user_confirm"] = True
                _save_result(db, task.id, "H", "success", output=h_out)
                raise _Paused("H")

        # F 分段（必选）。direct 模式机械切分（不调 LLM、不计费）；其余走 LLM 分句。
        if task.processing_mode == "direct":
            existing_f = _get_result(db, task.id, "F")
            if existing_f and existing_f.status == "success":
                f_out = existing_f.output
            else:
                f_out = tm.mechanical_split(script)
                _save_result(db, task.id, "F", "success", output=f_out)
                _maybe_pause(db, task, "F")
        else:
            f_out = _llm_step(db, task, cfg, llm_key, "F",
                              lambda: tm.run_split(cfg.llm_provider, cfg.llm_model, llm_key,
                                                   script, task.keyword, task.title),
                              started)
        segments = f_out["segments"]

        # D 识别（可选，失败跳过）
        book_info = None
        if "D" in task.modules:
            try:
                _src_desc = ""
                if isinstance(task.source_meta, dict):
                    _src_desc = str(task.source_meta.get("desc")
                                    or task.source_meta.get("description") or "")
                d_out = _llm_step(db, task, cfg, llm_key, "D",
                                  lambda: tm.run_identify(cfg.llm_provider, cfg.llm_model, llm_key,
                                                          script, keyword=task.keyword or "",
                                                          source_title=task.title or "",
                                                          source_description=_src_desc),
                                  started)
                book_info = im.pick_main_book(d_out["books"])
            except Exception as e:
                _save_result(db, task.id, "D", "failed", output={"error": str(e)})

        # 评论区下单引导话术（仅图书带货模式）：发布后置顶用，强化价格/稀缺性 + 二次种草。
        # 接在 D 识别之后，能带上识别到的书名。锦上添花，失败不阻断。
        if task.monetization_mode == "book_sales" and not task.comment_cta:
            try:
                _book_title = (book_info or {}).get("title") if isinstance(book_info, dict) else ""
                (pinned, price_scarcity, second_seed), _cc = tm.run_gen_comment_cta(
                    cfg.llm_provider, cfg.llm_model, llm_key, script,
                    book_title=_book_title or "", keyword=task.keyword)
                if pinned or price_scarcity or second_seed:
                    task.comment_cta = {"pinned": pinned, "price_scarcity": price_scarcity,
                                        "second_seed": second_seed}
                    db.commit()
            except Exception:
                db.rollback()

        # E 配图（必选）。拆两步：P 提示词生成（不调绘图 API）→ E 批量生图。
        if "E" in task.modules:
            out_dir = storage_root(db) / task.id / "images"
            # 配图张数：先按文案字数估一个【建议分镜数】引导 SB（约 5 字/秒口播 ×
            # 赛道秒/张，中老年舒适节奏约 40 字/张），但最终图数【跟随 SB 实际分镜数】，
            # 不再用字数硬算后截断分镜——那会导致图数与分镜数打架、用下标硬凑出图文错位。
            est_dur = len(script) / cost_svc.CHARS_PER_SECOND
            suggest_images = im.count_for_duration(est_dur, seconds_per_image=tracks.seconds_per_image(task.track))

            # 固定张数模式：强制目标图数为用户指定值，覆盖时长估算
            _icm = getattr(task, "image_count_mode", None)
            is_fixed_5 = _icm in ("fixed", "fixed_5")
            fixed_count = max(1, min(20, getattr(task, "fixed_image_count", None) or 5))
            if is_fixed_5:
                suggest_images = fixed_count
                logger.info("[P] 固定张数模式：强制 suggest_images=%d", fixed_count)

            # Step3 提示词生成（"P"）：组装绘图任务列表，落库供暂停时预览。无 LLM 计费。
            # 参考图 data URI 不落库（体积大），每次现编码，供人物镜头图生图用。
            # 多参考图：构建 ref_map {key: ref_uri}，按分镜的 character_key 匹配
            ref_uri = None  # 单参考图（向后兼容）
            ref_map = {}    # 多参考图：{角色key: data_uri}
            character_keys = []  # 所有可用的角色 key，供 SB 提示词引用
            from app.services.image import _encode_reference

            # 多图模式：reference_images = [{"key": "霍英东", "path": "..."}, ...]
            if task.reference_images:
                for item in task.reference_images:
                    key = (item.get("key") or "").strip()
                    path = item.get("path") or ""
                    if not key or not path:
                        continue
                    try:
                        uri = _encode_reference(path)
                        if uri:
                            ref_map[key] = uri
                            character_keys.append(key)
                    except Exception:
                        pass
                if ref_map:
                    logger.info("[P] 多参考图加载: keys=%s", list(ref_map.keys()))
                # 兜底：如果没有多图或全部加载失败，尝试单图
                if not ref_map and task.reference_image:
                    try:
                        ref_uri = _encode_reference(task.reference_image)
                    except Exception:
                        pass
            elif task.reference_image:
                try:
                    ref_uri = _encode_reference(task.reference_image)
                except Exception:
                    ref_uri = None

            existing_p = _get_result(db, task.id, "P")
            if existing_p and existing_p.status == "success":
                prompts_list = [(p["prompt"], p["sub_type"], Path(p["out_path"]), p["duration"],
                                 ref_uri if p.get("has_char") else None)
                                for p in existing_p.output["prompts"]]
            else:
                # 反推人物特征（"CP"）：先于画面脚本。
                # 多参考图时：对每张图分别反推外貌特征；单图时只反推一张。
                character_desc = None
                character_profiles = {}  # {key: profile_text}
                cp_targets = []
                if ref_uri:
                    cp_targets.append(("_主角", ref_uri))
                for key, uri in ref_map.items():
                    cp_targets.append((key, uri))

                # 多个反推目标（多角色参考图）时，每个角色的结果必须各存各的缓存行，
                # 否则全部复用同一条 "CP" 记录会导致除首个角色外的人物拿到别人的特征。
                _multi_cp = len(cp_targets) > 1
                for ck, cu in cp_targets:
                    _cp_cache_key = f"CP_{ck}" if _multi_cp else "CP"
                    try:
                        logger.info("[CP] 反推人物特征: key=%s, ref_uri_len=%d", ck, len(cu))
                        cp_out = _llm_step(db, task, cfg, llm_key, "CP",
                                           lambda u=cu: tm.run_character_profile(
                                               cfg.image_provider, cfg.vision_model,
                                               img_key, u,
                                               proxy=(getattr(cfg, "proxy_url", None) or "").strip() or None,
                                               base_url=getattr(cfg, "image_base_url", None)),
                                           started, cache_key=_cp_cache_key)
                        profile = (cp_out.get("profile") or "").strip() or None
                        if profile:
                            character_profiles[ck] = profile
                            logger.info("[CP] 反推成功 [%s]: %s", ck, profile[:80])
                    except Exception as e:
                        logger.error("[CP] 反推失败 [%s]: %s", ck, str(e)[:200])
                        _save_result(db, task.id, _cp_cache_key, "failed", output={"error": str(e), "key": ck})

                if character_profiles:
                    # 多角色时拼成多段描述供 SB 使用
                    if len(character_profiles) > 1:
                        parts = [f"[{k}]：{v}" for k, v in character_profiles.items()]
                        character_desc = "；".join(parts)
                    else:
                        character_desc = next(iter(character_profiles.values()))
                elif ref_uri or ref_map:
                    logger.info("[CP] 反推全部失败，SB 将只用 key 文字匹配")
                else:
                    logger.info("[CP] 跳过: 无参考图")

                # 画面脚本（"SB"）：【配音严格用原文】模式——先用 LLM 按视听分镜逻辑
                # 把 script 拆成 50-80 字一段的原文切片（一字不改），失败再回退到程序规则切分。
                # cap 由程序强制填为原文片，LLM 不碰文案 → 配音/字幕念的就是你的原文、且与画面
                # 按段一一对齐。失败不阻断——回退 build_image_prompts 的 segment 截字。
                # 不改写模式（direct/semi_auto）：直接用规则切分，绕过 LLM 避免切分时顺手改字。
                if task.processing_mode in ("direct", "semi_auto"):
                    seg_texts = tm.split_for_storyboard(script)
                    logger.info("[SB] 不改写模式：跳过 LLM 分段，使用规则切分（%d段）", len(seg_texts))
                else:
                    seg_texts = tm.run_split_for_storyboard(
                        cfg.llm_provider, cfg.llm_model, llm_key, script)
                    # LLM 输出 token 超限时会截断，导致 seg_texts 总字数远少于原文。
                    # 用 85% 阈值检测截断：截断则回退规则切分，保证全文不丢字。
                    seg_total_chars = sum(len(t) for t in seg_texts)
                    if not seg_texts or seg_total_chars < len(script) * 0.85:
                        logger.warning(
                            "[SB] LLM 分段疑似截断（%d/%d 字），回退规则切分",
                            seg_total_chars, len(script))
                        seg_texts = tm.split_for_storyboard(script)
                # 夹到 [MIN,MAX] 张：过多则【均匀合并】成 MAX 段，过少不补。
                # 注意：绝不能把超出部分全倒进最后一段——那样最后一镜会吞掉大半脚本(实测 2520 字)，
                # 配音几百秒，视频里最后一张图定格好几分钟(task_7e246655893b 的 8 分钟定格教训)。
                # 正确做法：相邻段雨露均沾地合并，让 MAX 段时长大致均衡。
                if is_fixed_5 and len(seg_texts) > fixed_count:
                    seg_texts = _merge_segments_evenly(seg_texts, fixed_count)
                    logger.info("[P] 固定张数模式：seg_texts 合并为 %d 段", fixed_count)
                elif len(seg_texts) > im.MAX_IMAGES:
                    seg_texts = _merge_segments_evenly(seg_texts, im.MAX_IMAGES)
                sb_segments = [{"text": t} for t in seg_texts]
                scenes = None
                try:
                    sb_out = _llm_step(db, task, cfg, llm_key, "SB",
                                       lambda: tm.run_storyboard_for_segments(
                                           cfg.llm_provider, cfg.llm_model, llm_key,
                                           sb_segments,
                                           rewrite_focus=tracks.get_track(task.track).get("rewrite_focus", ""),
                                           character_desc=character_desc,
                                           character_keys=character_keys if character_keys else None),
                                       started)
                    scenes = sb_out.get("scenes") or None
                except Exception as e:
                    _save_result(db, task.id, "SB", "failed", output={"error": str(e)})

                # 图数【= SB 实际分镜数】：一段分镜 = 一张图，严格一一对应(image[i]=scene[i])，
                # 不再额外加封面/CTA、不再用下标硬凑（硬凑会把最后一个分镜重复贴到多张图、
                # 或丢弃多出的分镜 → 图文错位）。SB 失败无分镜时回退到字数估算的建议值，
                # 由 build_image_prompts 走 segment 截字兜底。
                # 先按 [MIN,MAX] 夹分镜数（与 build_image_prompts 内部同口径），保证
                # scenes 与 prompts 数量一致、不错位。
                raw_scenes = scenes or []
                if is_fixed_5:
                    # 固定张数：优先用 SB 分镜，数量不足/超限时退回 segment 截字兜底
                    if raw_scenes and len(raw_scenes) >= fixed_count:
                        n_images = fixed_count
                        if len(raw_scenes) == fixed_count:
                            aligned_scenes = raw_scenes
                        else:
                            merged = _merge_scenes_evenly(raw_scenes, fixed_count)
                            aligned_scenes = merged
                        logger.info("[P] 固定张数模式：SB %d 个分镜→合并为 %d", len(raw_scenes), fixed_count)
                    else:
                        n_images = fixed_count
                        aligned_scenes = []
                        logger.info("[P] 固定张数模式：SB 分镜不足 %d，回退 segment 截字", fixed_count)
                elif raw_scenes:
                    n_images = max(im.MIN_IMAGES, min(im.MAX_IMAGES, len(raw_scenes)))
                    # 分镜比上限多时均匀合并（保留全文内容，不截断丢失后段文案）；
                    # 比下限少时不补，由 build_image_prompts 直接按现有分镜数出图。
                    if len(raw_scenes) > n_images:
                        aligned_scenes = _merge_scenes_evenly(raw_scenes, n_images)
                        logger.info("[P] 分镜 %d 超限 %d，均匀合并", len(raw_scenes), n_images)
                    else:
                        aligned_scenes = raw_scenes
                else:
                    n_images = max(im.MIN_IMAGES, min(im.MAX_IMAGES, suggest_images))
                    aligned_scenes = []

                # 唱歌·MV模式强制逐张（不支持九宫格，因每张需用对应角色的参考图）
                is_music = getattr(task, "video_mode", "vlog") == "music"
                if is_music:
                    task.image_gen_mode = "per_image"
                    logger.info("[P] 唱歌·MV模式强制逐张生图")

                prompts_list, used_items = im.build_image_prompts(
                    book_info, segments, out_dir,
                    image_count=n_images,
                    track=task.track, image_style=task.image_style,
                    scenes=aligned_scenes or None,
                    character_desc=character_desc,
                    ref_uri=ref_uri,
                    ref_map=ref_map if ref_map else None,  # 多参考图映射
                    character_keys=character_keys if character_keys else None)
                # SB 失败/分镜不足时 used_items 的 cap 全空 → _voice_segments_from_scenes
                # 会退回 F 原始分段，TTS 段数与图数不等 → jianying 对齐失败 → 结尾循环复用重复帧。
                # 修复：cap 全空时从 F 分段均匀合并填入，保证 scenes.cap 非空、三轨能对齐。
                if used_items and not any(it.get("cap") for it in used_items):
                    merged_caps = _merge_segments_evenly(
                        [s.get("text", "") for s in segments], len(used_items))
                    merged_caps += [""] * (len(used_items) - len(merged_caps))
                    for it, cap in zip(used_items, merged_caps):
                        it["cap"] = cap
                    logger.info("[P] SB 回退：均匀合并 F 分段填入 cap（%d段→%d张）",
                                len(segments), len(used_items))
                # P 产物带上 scenes（含 cap/desc_prompt/has_character），供前端分镜画廊逐句编辑、
                # 及配音/字幕取 cap。用 build_image_prompts 实际使用的 used_items 落库（不是
                # aligned_scenes）——保证 P.scenes 与图（prompts_list）严格同源同长，配音段数
                # 永远==图数，jianying 不会因数量不等而静默退回错位逻辑。
                # used_items 每项含 cap/desc_prompt/has_character；回退(无分镜)时为 segment 截字项、cap 为空。
                p_scenes = [{"id": i + 1, "cap": it.get("cap", ""),
                             "desc_prompt": it.get("desc_prompt", ""),
                             "has_character": bool(it.get("has_character", True))}
                            for i, it in enumerate(used_items)]
                _save_result(db, task.id, "P", "success",
                             output={"prompts": [{"prompt": p, "sub_type": st,
                                                  "out_path": str(op), "duration": sd,
                                                  "has_char": bool(rf)}
                                                 for (p, st, op, sd, rf) in prompts_list],
                                     "scenes": p_scenes})
                _maybe_pause(db, task, "P")

            # Step4 批量生图（"E"）：按任务的生图模式分流——
            #   per_image（逐张，画质优先）：每张单独出图，正确套用所选画风、人物镜头走图生图
            #     （传人物参考图保持主角一致），失败逐张占位、不影响整批。画质最稳，成本按张算。
            #   grid（九宫格省成本）：每 ≤9 张走「3×3 模板图生图」一次出 1 张大图本地切割
            #     （按 1 张计费，省约 89%），风格由统一圣经管。竖版需中心裁切，清晰度略降。
            #     失败整组占位、不回退逐张（控成本），用户可在画廊手动重新组图。
            mode = (getattr(task, "image_gen_mode", None) or "per_image")
            img_ratio = tracks.image_ratio_for(task)  # center_h 版式强制 16:9，与画布解耦
            # center_h 中央横图：gpt 便宜线只能出 3:2，强裁 16:9 会切头。no_crop 让归一化
            # 只等比缩放不裁切、保留完整横图（黑边交剪映 center_h 自动加，见 image._normalize）。
            img_no_crop = getattr(task, "layout", "full") == "center_h"
            # 横版九宫格：gpt 1536x1024 / 豆包 3072x1728，都支持（commit 5d36faa已验证）
            # 按原设计，gpt和豆包都省成本89%，不再强制降级
            existing_e = _get_result(db, task.id, "E")
            if not (existing_e and existing_e.status == "success"):
                # 生图是最烧钱的步骤，开跑前再查一次取消/超限——用户点了取消就别再发这批图。
                _check_limits(db, task, started)
                _img_proxy = (getattr(cfg, "proxy_url", None) or "").strip() or None
                if mode == "skip":
                    logger.info("[E] 跳过生图: %d 张全部使用占位图，零成本", len(prompts_list))
                    images = [im.placeholder_result(op, st, sd, reason="skip")
                              for (_, st, op, sd, _) in prompts_list]
                elif mode == "grid":
                    grid_style = tracks.get_style(task.image_style, task.track)
                    logger.info("[E] 九宫格模式: image_style=%s, style=%s, grayscale=%s",
                                task.image_style, grid_style.get("prefix", "None") if grid_style else "None",
                                im.is_monochrome_style(grid_style))
                    # 后台首次生成不自动改写重发：九宫格失败即整组占位+原因，
                    # 等用户在画廊手动「重新组图」(可顺便改文案)。是否再花钱由用户决定，
                    # 不在后台用看不见的多次改写反复烧钱。故不传 rewrite_fn。
                    images = im.render_images_grouped(cfg.image_provider, img_key, prompts_list,
                                                      model=cfg.image_model,
                                                      aspect_ratio=img_ratio,
                                                      grid_mode=True, style=grid_style,
                                                      base_url=getattr(cfg, "image_base_url", None),
                                                      proxy=_img_proxy, no_crop=img_no_crop)
                else:
                    # 逐张模式：每张内部已有瞬时故障(超时/限流)退避重试+失败占位，
                    # 不再套外层整批重发(后台反复烧钱)。审核拦截的图保留占位+原因等手动重生。
                    _style = tracks.get_style(task.image_style, task.track)
                    _grayscale = im.is_monochrome_style(_style)
                    logger.info("[E] 逐张模式: image_style=%s, style_prefix=%s, grayscale=%s",
                                task.image_style, _style.get("prefix", "None") if _style else "None",
                                _grayscale)
                    images = im.render_images(cfg.image_provider, img_key, prompts_list,
                                              model=cfg.image_model,
                                              concurrency=cfg.concurrency,
                                              aspect_ratio=img_ratio,
                                              base_url=getattr(cfg, "image_base_url", None),
                                              proxy=_img_proxy,
                                              grayscale=_grayscale,
                                              no_crop=img_no_crop)
                # 失败不在后台自动改写重生（那样会在用户看不见的地方反复烧钱，
                # task_472 一次烧 16 次就是这么来的）。被审核拦截的图保留占位+原因，
                # 等用户在画廊手动「重新生成」或「重新组图」——花不花钱、花几次由用户决定。
                # 成本（重算而非累加，与画廊重试/重组同口径）：E 成本恒等于当前产物实际成本。
                # 九宫格一次请求出 9 张只算 1 张钱（按 grid 标记折算 ceil(张数/9)）；逐张按实际张数。
                img_cost = cost_svc.image_cost(images, cfg.image_provider,
                                               getattr(cfg, "image_unit_price", None),
                                               model=cfg.image_model)
                cost_svc.rebill_module(db, task, "E", cfg.image_provider, img_cost)
                _save_result(db, task.id, "E", "success",
                             output={"images": [{"path": r.path, "sub_type": r.sub_type,
                                                 "suggested_duration": r.suggested_duration,
                                                 "fallback": bool((r.meta or {}).get("fallback")),
                                                 "grid": bool((r.meta or {}).get("grid")),
                                                 # 失败图保留原因（审核拒绝/超时等），供画廊显示，不再空白
                                                 **({"fail_reason": (r.meta or {}).get("reason")}
                                                    if (r.meta or {}).get("fallback") else {})}
                                                for r in images]},
                             cost=img_cost)
                _check_limits(db, task, started)
                _maybe_pause(db, task, "E")

        # F 分段完成后：尝试自动 TTS 配音 → 自动成片。
        # 唱歌·MV模式：使用上传的音频文件，跳过 TTS 配音
        # 无 TTS Key 时降级为 awaiting_audio，等用户手动上传音频。
        tts_key = decrypt(cfg.tts_api_key_enc) if cfg.tts_api_key_enc else ""
        audio_path = None
        # 配音/字幕分段【统一用分镜 cap】（与图片同源，保证图-字-音三轨一一对齐）：
        # 从 P 产物读 scenes 的 cap 作为配音分段；cap 缺失/无分镜时回退 F 的 segments（老路）。
        tts_segments, tts_seg_source = _voice_segments_from_scenes(db, task.id, segments)

        # 唱歌·MV模式：使用上传的音频文件，按歌词对齐生成分段时长
        if task.video_mode == "music" and task.audio_file:
            audio_path = task.audio_file
            try:
                from app.services.lyrics_align import align_lyrics, _get_audio_duration
                total_duration = _get_audio_duration(task.audio_file)
                seg_texts, seg_durations, paragraph_breaks = align_lyrics(task.lyrics or "", task.audio_file)
                _save_result(db, task.id, "T", "success",
                             output={"audio_path": task.audio_file, "duration": total_duration,
                                     "segment_count": len(seg_texts),
                                     "seg_durations": seg_durations,
                                     "seg_texts": seg_texts,
                                     "paragraph_breaks": paragraph_breaks,
                                     "seg_source": "lyrics"})
            except Exception as e:
                _save_result(db, task.id, "T", "failed", output={"error": str(e)})
                audio_path = None

        if tts_key and not audio_path:
            try:
                audio_dir = storage_root(db) / task.id / "audio"
                existing_tts = _get_result(db, task.id, "T")
                # 缓存复用前校验段数：T 的 seg_durations 段数必须与当前分镜数一致，
                # 否则图数变了（如切换固定5张）但 T 是旧的，对齐会失败 → 强制重合成。
                _tts_seg_ok = (
                    existing_tts and existing_tts.status == "success"
                    and len((existing_tts.output or {}).get("seg_durations") or []) == len(tts_segments)
                )
                if _tts_seg_ok:
                    audio_path = existing_tts.output.get("audio_path")
                else:
                    r = tts_svc.synthesize(tts_segments, cfg.tts_provider, tts_key,
                                           audio_dir, voice=(task.voice or cfg.tts_voice),
                                           appid=cfg.tts_appid,
                                           speed=float(task.voice_speed or 1.0))
                    audio_path = r.audio_path
                    # 存 seg_durations(每个分镜的真实配音时长)和分段文本，供 compose 让
                    # 图片轨/字幕轨共用这套分镜时长，实现三轨严格对齐。
                    _save_result(db, task.id, "T", "success",
                                 output={"audio_path": r.audio_path, "duration": r.duration,
                                         "segment_count": r.segment_count,
                                         "seg_durations": r.seg_durations,
                                         "seg_texts": [s.get("text", "") for s in tts_segments],
                                         "seg_source": tts_seg_source})
            except tts_svc.TTSUnavailable:
                audio_path = None
            except Exception as e:
                _save_result(db, task.id, "T", "failed", output={"error": str(e)})
                audio_path = None

        if audio_path:
            # 自动成片（剪映草稿模式）。compose 内部会把 task 置为 completed。
            from app.services import compose as compose_svc
            # BGM 路径：task.bgm（文件名）+ cfg.bgm_dir（目录）拼成绝对路径，供 mp4 混音。
            bgm_path = None
            if task.bgm and (cfg.bgm_dir or "").strip():
                from pathlib import Path as _P
                cand = _P(cfg.bgm_dir) / task.bgm
                if cand.is_file():
                    bgm_path = str(cand)
            compose_svc.compose_video(db, task.id, audio_path,
                                      task.enable_subtitles, task.enable_animations,
                                      output_mode="jianying", bgm_path=bgm_path)
        else:
            # 降级：文案+配图完成，等待音频上传触发 G
            task.status = "awaiting_audio"
            db.commit()

    except _Paused as p:
        # 命中暂停点：置 awaiting_confirm，记录停在哪个 step，等用户确认后 resume。
        task = db.get(Task, task.id) or task
        task.status = "awaiting_confirm"
        task.paused_at = p.step
        db.commit()
    except _Cancelled:
        # 用户取消：状态已是 cancelled，干净退出，不当失败处理、不覆盖状态。
        return
    except TaskAborted as e:
        _fail(db, task, e.code, e.message)
    except Exception as e:
        _fail(db, task, "E5001", str(e))


def resume_pipeline(db: Session, task_id: str):
    """用户确认后从暂停点继续。复用 run_pipeline——已成功的 step 走缓存不重算、
    不重复扣费，且缓存命中不再触发暂停，自然越过上次的暂停点继续往后跑。"""
    task = db.get(Task, task_id)
    if not task:
        return
    task.paused_at = None
    db.commit()
    run_pipeline(db, task_id)


def _run_collect_asr(db: Session, task: Task, cfg: Config, started: float):
    """前置采集 + ASR：抖音链接 → 元数据 + 原始逐字稿，写入 task。

    采集/ASR 任一未配 Key 时静默降级（不抛错），让后续逻辑回落到
    "要求手填 transcript"。仅在真正调用且失败时记错误结果。
    """
    collect_key = decrypt(cfg.collect_api_key_enc) if cfg.collect_api_key_enc else ""
    asr_key = decrypt(cfg.asr_api_key_enc) if cfg.asr_api_key_enc else ""
    proxy = (getattr(cfg, "proxy_url", None) or "").strip() or None

    # 采集：拿元数据 + 无水印视频地址
    video_url = ""
    try:
        cr = collect_svc.fetch_video(task.douyin_url, cfg.collect_provider, collect_key, proxy=proxy)
        video_url = cr.video_url
        task.source_meta = {"title": cr.title, "author": cr.author,
                            "play_count": cr.play_count, "digg_count": cr.digg_count,
                            "platform": cr.platform,
                            **cr.raw_meta}
        if cr.title and not task.title:
            # 采集原标题常带换行/制表符，折叠成单空格再存——否则下游拼草稿目录会触发
            # WinError 123（目录名语法错误）；草稿名另有 _safe_name 兜底，这里先把 title 本身清干净。
            import re as _re
            task.title = _re.sub(r"\s+", " ", cr.title).strip()[:200]
        if cr.author and not task.author:
            task.author = cr.author[:100]
        db.commit()
        _save_result(db, task.id, "S", "success",
                     output={"title": cr.title, "author": cr.author,
                             "play_count": cr.play_count, "video_url": bool(video_url)})
    except collect_svc.CollectUnavailable:
        return  # 未配采集 Key，降级
    except Exception as e:
        _save_result(db, task.id, "S", "failed", output={"error": str(e)})
        return

    # ASR：视频 → 逐字稿（传候选地址+时长，内部校验抽全、必要时换地址重试）
    try:
        ar = asr_svc.transcribe_url(cr.video_url_candidates or video_url,
                                    cfg.asr_provider, asr_key, proxy=proxy,
                                    expect_ms=cr.duration_ms)
        if ar.text.strip():
            task.transcript = ar.text.strip()
            db.commit()
            _save_result(db, task.id, "R", "success",
                         output={"chars": len(ar.text), "preview": ar.text[:100]})
    except asr_svc.ASRUnavailable:
        return  # 未配 ASR Key，降级
    except Exception as e:
        _save_result(db, task.id, "R", "failed", output={"error": str(e)})


def _fail(db, task, code, message):
    # 上一步异常可能使会话处于待回滚状态，先回滚再写失败状态。
    try:
        db.rollback()
    except Exception:
        pass
    task = db.get(Task, task.id)
    if not task:
        return
    task.status = "failed"
    task.error_code = code
    task.error_message = message[:500]
    db.commit()
