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
IMAGE_RETRY = 2


class TaskAborted(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class _Paused(Exception):
    """内部信号：命中暂停点，需停下等用户确认（非错误）。"""
    def __init__(self, step: str):
        self.step = step


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


def _rescue_failed_images(db, task, cfg, img_key, images, prompts_list, proxy, started):
    """逐张模式失败补救：对被内容审核拦截的占位图，自动用 LLM 递进改写提示词重生。
    最多改写 2 次（一次比一次激进，逐步抛弃敏感元素）；非审核类失败（Key 无效等）不改写。
    源头已用 SB 强化 + 词典预净化消掉大部分敏感，这里兜底救漏网的，不必等用户手动重试。
    返回更新后的 images 列表（救回的替换占位图，救不回的保持占位）。"""
    style = tracks.get_style(task.image_style, task.track)
    llm_key = decrypt(cfg.llm_api_key_enc) if cfg and cfg.llm_api_key_enc else ""

    def _is_audit(rsn):
        return rsn and ("sensitive" in rsn.lower() or "审核" in rsn or "拒绝" in rsn)

    for i, r in enumerate(images):
        meta = r.meta or {}
        if not meta.get("fallback") or not _is_audit(meta.get("reason")):
            continue  # 只救审核失败的；非审核失败/成功图跳过
        if i >= len(prompts_list):
            continue
        _wrapped, sub_type, out_path, duration, ref_uri = prompts_list[i]
        # 裸主体：从 wrap 过的 prompt 剥出（去风格三层），作为 LLM 改写基底
        base = im._strip_wrap(style, _wrapped)
        for attempt in range(1, 3):
            try:
                safe_subj, _ = tm.run_safe_rewrite(cfg.llm_provider, cfg.llm_model,
                                                   llm_key, base, attempt=attempt)
                safe_subj = im._sanitize_imagery(safe_subj)
            except Exception:
                break  # LLM 改写失败，保持占位
            if not safe_subj or safe_subj == base:
                continue
            text = im._wrap(style, safe_subj)
            if ref_uri:
                text = im._wrap(style, f"参考图中的同一个人物，保持其面部特征、五官、气质不变，"
                                       f"但改为以下全新画面（不同的姿势、表情、构图）：{safe_subj}")
            try:
                new_r = im._gen_with_fallback(cfg.image_provider, img_key, text, sub_type,
                                              Path(out_path) if not isinstance(out_path, Path) else out_path,
                                              duration, cfg.image_model,
                                              aspect_ratio=task.aspect_ratio, ref_uri=ref_uri,
                                              base_url=getattr(cfg, "image_base_url", None), proxy=proxy,
                                              grayscale=im.is_monochrome_style(style))
            except Exception:
                break
            base = safe_subj
            nm = new_r.meta or {}
            if not nm.get("fallback"):
                images[i] = new_r  # 救回成功
                break
            if not _is_audit(nm.get("reason")):
                break  # 换成非审核错误，停止
        try:
            _check_limits(db, task, started)  # 改写重生也耗时，复查超时/成本上限
        except TaskAborted:
            break
    return images


def _check_limits(db: Session, task: Task, started: float):
    """超时与成本上限检查（PRD 11.2）。"""
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
            # S2 结构拆解：creation_mode != none 时，先拆出爆款结构骨架，
            # 供 B 改写复刻其节奏。拆解失败不阻断（骨架为空即退回普通改写）。
            structure = None
            if getattr(task, "creation_mode", "same_topic") != "none":
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
                d_out = _llm_step(db, task, cfg, llm_key, "D",
                                  lambda: tm.run_identify(cfg.llm_provider, cfg.llm_model, llm_key, script),
                                  started)
                book_info = im.pick_main_book(d_out["books"])
            except Exception as e:
                _save_result(db, task.id, "D", "failed", output={"error": str(e)})

        # E 配图（必选）。拆两步：P 提示词生成（不调绘图 API）→ E 批量生图。
        if "E" in task.modules:
            out_dir = storage_root(db) / task.id / "images"
            # 张数按改写后文案字数自动匹配（约 5 字/秒口播），每张图停留秒数按赛道
            # （tracks.seconds_per_image，中老年友好 6-9 秒/张），不依赖 F 分段数
            # （其段数/估时波动大），保证节奏稳定。
            est_dur = len(script) / cost_svc.CHARS_PER_SECOND
            n_images = im.count_for_duration(est_dur, seconds_per_image=tracks.seconds_per_image(task.track))

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
                                               proxy=(getattr(cfg, "proxy_url", None) or "").strip() or None),
                                           started)
                        character_desc = (cp_out.get("profile") or "").strip() or None
                    except Exception as e:
                        _save_result(db, task.id, "CP", "failed", output={"error": str(e)})

                # 画面脚本（"SB"）：把口播稿转成 N 个分镜，每个含 cap/desc_prompt/has_character，
                # 让配图有镜头变化、贴合文案、人物按需出场。含质检（雷同尾巴自动打回重写）。
                # 失败不阻断——build_image_prompts 会回退到 segment 截字。
                scenes = None
                try:
                    sb_out = _llm_step(db, task, cfg, llm_key, "SB",
                                       lambda: tm.run_storyboard(cfg.llm_provider, cfg.llm_model,
                                                                 llm_key, script, n_scenes=max(1, n_images - 2),
                                                                 rewrite_focus=tracks.get_track(task.track).get("rewrite_focus", ""),
                                                                 character_desc=character_desc),
                                       started)
                    scenes = sb_out.get("scenes") or None
                except Exception as e:
                    _save_result(db, task.id, "SB", "failed", output={"error": str(e)})

                prompts_list = im.build_image_prompts(book_info, segments, out_dir,
                                                      image_count=n_images,
                                                      track=task.track, image_style=task.image_style,
                                                      scenes=scenes, character_desc=character_desc,
                                                      ref_uri=ref_uri)
                # P 产物带上 scenes（含 cap/desc_prompt/has_character），供前端分镜画廊逐句编辑。
                # scenes 必须与实际内容图数（n_images-2）一一对应：SB 分镜可能多于/少于配图数，
                # 这里按配图数截断/补齐，否则前端画廊格子数与实际图数对不上，多出的显示"未生成"。
                n_content = max(0, n_images - 2)
                raw_scenes = scenes or []
                if raw_scenes:
                    aligned_scenes = [raw_scenes[min(i, len(raw_scenes) - 1)] for i in range(n_content)]
                else:
                    aligned_scenes = []
                _save_result(db, task.id, "P", "success",
                             output={"prompts": [{"prompt": p, "sub_type": st,
                                                  "out_path": str(op), "duration": sd,
                                                  "has_char": bool(rf)}
                                                 for (p, st, op, sd, rf) in prompts_list],
                                     "scenes": aligned_scenes})
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
                _img_proxy = (getattr(cfg, "proxy_url", None) or "").strip() or None
                if mode == "grid":
                    grid_style = tracks.get_style(task.image_style, task.track)
                    # 九宫格失败补救：整组被审核拒时, 用 LLM 把每格裸 brief 改写成安全版重发。
                    def _grid_rewrite(brief, attempt):
                        try:
                            safe, _ = tm.run_safe_rewrite(cfg.llm_provider, cfg.llm_model,
                                                          llm_key, brief, attempt=attempt)
                            return safe or brief
                        except Exception:
                            return brief
                    images, _ = with_retry(
                        lambda: im.render_images_grouped(cfg.image_provider, img_key, prompts_list,
                                                         model=cfg.image_model,
                                                         aspect_ratio=task.aspect_ratio,
                                                         grid_mode=True, style=grid_style,
                                                         base_url=getattr(cfg, "image_base_url", None),
                                                         proxy=_img_proxy,
                                                         rewrite_fn=_grid_rewrite),
                        IMAGE_RETRY)
                else:
                    images, _ = with_retry(
                        lambda: im.render_images(cfg.image_provider, img_key, prompts_list,
                                                 model=cfg.image_model,
                                                 concurrency=cfg.concurrency,
                                                 aspect_ratio=task.aspect_ratio,
                                                 base_url=getattr(cfg, "image_base_url", None),
                                                 proxy=_img_proxy,
                                                 grayscale=im.is_monochrome_style(
                                                     tracks.get_style(task.image_style, task.track))),
                        IMAGE_RETRY)
                # 失败补救（逐张模式）：审核拦截的占位图，自动用 LLM 递进改写提示词重生，
                # 不必等用户手动在画廊点重试。九宫格是整组一张图、改写需整体重组，此处只救逐张
                # （画质优先模式，最该救）；九宫格失败仍占位，用户可在画廊手动重新组图。
                if mode != "grid":
                    images = _rescue_failed_images(db, task, cfg, img_key, images,
                                                   prompts_list, _img_proxy, started)
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
        if tts_key:
            try:
                audio_dir = storage_root(db) / task.id / "audio"
                existing_tts = _get_result(db, task.id, "T")
                if existing_tts and existing_tts.status == "success":
                    audio_path = existing_tts.output.get("audio_path")
                else:
                    r = tts_svc.synthesize(segments, cfg.tts_provider, tts_key,
                                           audio_dir, voice=(task.voice or cfg.tts_voice),
                                           appid=cfg.tts_appid,
                                           speed=float(task.voice_speed or 1.0))
                    audio_path = r.audio_path
                    _save_result(db, task.id, "T", "success",
                                 output={"audio_path": r.audio_path, "duration": r.duration,
                                         "segment_count": r.segment_count})
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
