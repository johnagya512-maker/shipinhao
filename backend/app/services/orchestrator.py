"""任务编排引擎。串联各模块，管理状态机、成本与超时（PRD 5.3/9.x/11.x）。

执行顺序：A 清洗 → B 改写 → H 合规 → (D 识别) → E 配图 → F 分段。
G 视频合成在用户上传音频后单独触发（见 services/compose）。
"""
import time
from datetime import datetime
from pathlib import Path
from sqlalchemy.orm import Session

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


def _llm_step(db, task, cfg, llm_key, module, fn, started, pausable=True):
    """执行一个 LLM 模块：断点复用 + 重试 + 计费 + 限额检查。返回 output。
    pausable=True 时，仅在“本次新算完”后检查暂停点（缓存复用直接返回，不再暂停，
    保证 resume 能越过上次的暂停点继续）。"""
    existing = _get_result(db, task.id, module)
    if existing and existing.status == "success":
        return existing.output  # 断点续跑：复用已成功结果，不重复扣费，也不再触发暂停

    def _do():
        return fn()

    (output, llm_result), attempts = with_retry(_do, LLM_RETRY)
    c = cost_svc.actual_llm_cost(llm_result.tokens_in, llm_result.tokens_out, cfg.llm_provider)
    cost_svc.record_cost(db, task.id, module, cfg.llm_provider, c)
    task.total_cost = float(task.total_cost) + c
    db.commit()
    _save_result(db, task.id, module, "success", output=output, cost=c,
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

        # A 清洗（direct=「不改文案」跳过清洗，原文一字不改、不调 LLM、不计费；
        # semi_auto/full_auto 仍做清洗）
        if task.processing_mode == "direct":
            existing_a = _get_result(db, task.id, "A")
            if existing_a and existing_a.status == "success":
                cleaned = existing_a.output.get("cleaned_text", task.transcript)
            else:
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
                                                         lite=True, keyword=task.keyword or "",
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
                    cfg.llm_provider, cfg.llm_model, llm_key, script, task.keyword)
                if short_title and not (task.short_title or "").strip():
                    task.short_title = short_title
                if long_title and not (task.long_title or "").strip():
                    task.long_title = long_title
                if tags and not task.hashtags:
                    task.hashtags = tags
                db.commit()
            except Exception:
                db.rollback()

        # H 合规闸门（强制，按赛道词库；三种处理模式都跑——保留合规兜底）
        h_out = _llm_step(db, task, cfg, llm_key, "H",
                          lambda: tm.run_compliance(cfg.llm_provider, cfg.llm_model, llm_key,
                                                    script, track=task.track),
                          started)
        if not h_out["passed"]:
            task.status = "blocked"
            task.error_code = "E4002"
            task.error_message = "合规检查未通过"
            db.commit()
            return

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

        # E 配图（必选）。拆两步：P 提示词生成（不调绘图 API）→ E 批量生图。
        if "E" in task.modules:
            out_dir = storage_root(db) / task.id / "images"
            # 配图张数：先按文案字数估一个【建议分镜数】引导 SB（约 5 字/秒口播 ×
            # 赛道秒/张，中老年舒适节奏约 40 字/张），但最终图数【跟随 SB 实际分镜数】，
            # 不再用字数硬算后截断分镜——那会导致图数与分镜数打架、用下标硬凑出图文错位。
            est_dur = len(script) / cost_svc.CHARS_PER_SECOND
            suggest_images = im.count_for_duration(est_dur, seconds_per_image=tracks.seconds_per_image(task.track))

            # Step3 提示词生成（"P"）：组装绘图任务列表，落库供暂停时预览。无 LLM 计费。
            # 参考图 data URI 不落库（体积大），每次现编码，供人物镜头图生图用。
            ref_uri = None
            if task.reference_image:
                try:
                    from app.services.image import _encode_reference
                    ref_uri = _encode_reference(task.reference_image)
                except Exception:
                    ref_uri = None
            existing_p = _get_result(db, task.id, "P")
            if existing_p and existing_p.status == "success":
                prompts_list = [(p["prompt"], p["sub_type"], Path(p["out_path"]), p["duration"],
                                 ref_uri if p.get("has_char") else None)
                                for p in existing_p.output["prompts"]]
            else:
                # 反推人物特征（"CP"）：先于画面脚本。有参考图时用视觉模型看一次参考图，
                # 生成稳定外貌特征文字（文字锚定，供 SB 写进分镜）；参考图 data URI（上面已编码）
                # 留作图生图入参（人物镜头保持主角一致：同一个人、不同场景）。失败不阻断。
                character_desc = None
                if ref_uri:
                    try:
                        cp_out = _llm_step(db, task, cfg, llm_key, "CP",
                                           lambda: tm.run_character_profile(
                                               cfg.image_provider, cfg.vision_model,
                                               img_key, ref_uri,
                                               proxy=(getattr(cfg, "proxy_url", None) or "").strip() or None,
                                               base_url=getattr(cfg, "image_base_url", None)),
                                           started)
                        character_desc = (cp_out.get("profile") or "").strip() or None
                    except Exception as e:
                        _save_result(db, task.id, "CP", "failed", output={"error": str(e)})

                # 画面脚本（"SB"）：【配音严格用原文】模式——先用程序把 script 按句子合并成
                # 约 40 字一段的原文切片（一字不改），再让 SB 为【每一片】配 desc_prompt。
                # cap 由程序强制填为原文片，LLM 不碰文案 → 配音/字幕念的就是你的原文、且与画面
                # 按段一一对齐。失败不阻断——回退 build_image_prompts 的 segment 截字。
                seg_texts = tm.split_for_storyboard(script)
                # 夹到 [MIN,MAX] 张：过多则合并尾部、过少不补（由切片自然决定）。
                if len(seg_texts) > im.MAX_IMAGES:
                    # 极长文案：把超出部分并入最后一段，保证不丢原文、张数不爆。
                    head = seg_texts[:im.MAX_IMAGES - 1]
                    tail = "".join(seg_texts[im.MAX_IMAGES - 1:])
                    seg_texts = head + [tail]
                sb_segments = [{"text": t} for t in seg_texts]
                scenes = None
                try:
                    sb_out = _llm_step(db, task, cfg, llm_key, "SB",
                                       lambda: tm.run_storyboard_for_segments(
                                           cfg.llm_provider, cfg.llm_model, llm_key,
                                           sb_segments,
                                           rewrite_focus=tracks.get_track(task.track).get("rewrite_focus", ""),
                                           character_desc=character_desc),
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
                if raw_scenes:
                    n_images = max(im.MIN_IMAGES, min(im.MAX_IMAGES, len(raw_scenes)))
                    # 分镜比上限多时截断（极少见，MAX_IMAGES=48）；比下限少时不补，
                    # 由 build_image_prompts 直接按现有分镜数出图。
                    aligned_scenes = raw_scenes[:n_images]
                else:
                    n_images = max(im.MIN_IMAGES, min(im.MAX_IMAGES, suggest_images))
                    aligned_scenes = []

                prompts_list, used_items = im.build_image_prompts(
                    book_info, segments, out_dir,
                    image_count=n_images,
                    track=task.track, image_style=task.image_style,
                    scenes=aligned_scenes or None,
                    character_desc=character_desc,
                    ref_uri=ref_uri)
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
            existing_e = _get_result(db, task.id, "E")
            if not (existing_e and existing_e.status == "success"):
                # 生图是最烧钱的步骤，开跑前再查一次取消/超限——用户点了取消就别再发这批图。
                _check_limits(db, task, started)
                _img_proxy = (getattr(cfg, "proxy_url", None) or "").strip() or None
                if mode == "grid":
                    grid_style = tracks.get_style(task.image_style, task.track)
                    # 后台首次生成不自动改写重发：九宫格失败即整组占位+原因，
                    # 等用户在画廊手动「重新组图」(可顺便改文案)。是否再花钱由用户决定，
                    # 不在后台用看不见的多次改写反复烧钱。故不传 rewrite_fn。
                    images = im.render_images_grouped(cfg.image_provider, img_key, prompts_list,
                                                      model=cfg.image_model,
                                                      aspect_ratio=task.aspect_ratio,
                                                      grid_mode=True, style=grid_style,
                                                      base_url=getattr(cfg, "image_base_url", None),
                                                      proxy=_img_proxy)
                else:
                    # 逐张模式：每张内部已有瞬时故障(超时/限流)退避重试+失败占位，
                    # 不再套外层整批重发(后台反复烧钱)。审核拦截的图保留占位+原因等手动重生。
                    images = im.render_images(cfg.image_provider, img_key, prompts_list,
                                              model=cfg.image_model,
                                              concurrency=cfg.concurrency,
                                              aspect_ratio=task.aspect_ratio,
                                              base_url=getattr(cfg, "image_base_url", None),
                                              proxy=_img_proxy,
                                              grayscale=im.is_monochrome_style(
                                                  tracks.get_style(task.image_style, task.track)))
                # 失败不在后台自动改写重生（那样会在用户看不见的地方反复烧钱，
                # task_472 一次烧 16 次就是这么来的）。被审核拦截的图保留占位+原因，
                # 等用户在画廊手动「重新生成」或「重新组图」——花不花钱、花几次由用户决定。
                # 成本（重算而非累加，与画廊重试/重组同口径）：E 成本恒等于当前产物实际成本。
                # 九宫格一次请求出 9 张只算 1 张钱（按 grid 标记折算 ceil(张数/9)）；逐张按实际张数。
                img_cost = cost_svc.image_cost(images, cfg.image_provider,
                                               getattr(cfg, "image_unit_price", None))
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
        # 无 TTS Key 时降级为 awaiting_audio，等用户手动上传音频。
        tts_key = decrypt(cfg.tts_api_key_enc) if cfg.tts_api_key_enc else ""
        audio_path = None
        # 配音/字幕分段【统一用分镜 cap】（与图片同源，保证图-字-音三轨一一对齐）：
        # 从 P 产物读 scenes 的 cap 作为配音分段；cap 缺失/无分镜时回退 F 的 segments（老路）。
        tts_segments, tts_seg_source = _voice_segments_from_scenes(db, task.id, segments)
        if tts_key:
            try:
                audio_dir = storage_root(db) / task.id / "audio"
                existing_tts = _get_result(db, task.id, "T")
                if existing_tts and existing_tts.status == "success":
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
            task.title = cr.title[:200]
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
