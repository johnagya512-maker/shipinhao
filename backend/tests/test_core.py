"""核心算法单测：时长对账（PRD 9.5）、字幕对齐（PRD 4.8）、成本预估（9.6）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_reconcile_sum_equals_audio():
    from app.modules.video_module import reconcile_durations
    durs = reconcile_durations([4, 10, 10, 10, 6], 40.0)
    assert abs(sum(durs) - 40.0) < 0.01


def test_reconcile_respects_min_when_too_many():
    from app.modules.video_module import reconcile_durations, MIN_DUR
    # 短音频 + 多图：每张被压到下限附近，总和仍等于音频
    durs = reconcile_durations([5] * 6, 10.0)
    assert abs(sum(durs) - 10.0) < 0.01


def test_reconcile_extends_last_when_too_few():
    from app.modules.video_module import reconcile_durations
    # 长音频 + 少图：总和必须铺满音频，不留黑屏
    durs = reconcile_durations([5, 5], 60.0)
    assert abs(sum(durs) - 60.0) < 0.01


def test_subtitle_alignment_covers_full_audio():
    from app.modules.video_module import align_subtitles
    segs = [{"text": "你好世界"}, {"text": "这是第二段内容"}]
    subs = align_subtitles(segs, 20.0)
    assert subs[0]["start"] == 0
    assert abs(subs[-1]["end"] - 20.0) < 0.01
    # 段间无缝衔接
    assert abs(subs[0]["end"] - subs[1]["start"]) < 0.01


def test_estimate_cost_under_limit():
    from app.services.cost import estimate_cost
    est = estimate_cost("这是一段测试逐字稿" * 50, ["A", "B", "E", "F", "G"], 5,
                        "deepseek", "doubao")
    assert est > 0
    assert est < 1.0  # MVP 目标成本


def test_pick_main_book_by_confidence():
    from app.modules.image_module import pick_main_book
    books = [{"title": "A", "confidence": 0.6, "extracted_from": "x"},
             {"title": "B", "confidence": 0.9, "extracted_from": "yy"}]
    assert pick_main_book(books)["title"] == "B"


def test_rule_match_catches_forbidden():
    """健康书单赛道：医疗违禁词被命中。"""
    from app.modules.text_modules import _rule_match
    m = _rule_match("这个方法可以根治糖尿病", "health_book")
    assert "根治" in m["high"]


def test_rule_match_ignores_negated():
    """否定语境下的违禁词不算违规（PRD 真实验证发现）。"""
    from app.modules.text_modules import _rule_match
    m = _rule_match("咱们不求\"根治\"，但求每天舒服一点", "health_book")
    assert "根治" not in m["high"]


def test_rule_match_mixed_negated_and_real():
    """同词既有否定也有肯定用法时，仍算命中。"""
    from app.modules.text_modules import _rule_match
    m = _rule_match("不求根治，但这个药能根治百病", "health_book")
    assert "根治" in m["high"]


def test_track_switches_compliance_vocab():
    """人物故事赛道放松医疗词，健康书单赛道仍拦（转赛道核心行为）。"""
    from app.modules.text_modules import _rule_match
    text = "他最后根治了顽疾"
    assert "根治" in _rule_match(text, "health_book")["high"]
    assert "根治" not in _rule_match(text, "character_story")["high"]


def test_normalize_risk_scales():
    """risk_score 归一化：兼容模型误用 0-10 / 0-100 标度（真实验证发现）。"""
    from app.modules.text_modules import _normalize_risk
    assert _normalize_risk(0.45) == 0.45      # 已在 [0,1]
    assert _normalize_risk(8.5) == 0.85       # 0-10 标度
    assert _normalize_risk(85) == 0.85        # 0-100 标度
    assert _normalize_risk(-1) == 0.0         # 负数兜底
    assert _normalize_risk("bad") == 0.0      # 非数字兜底
    assert _normalize_risk(999) == 1.0        # 超大值封顶


def test_mask_secret():
    from app.core.security import mask
    assert mask("sk-1234567890abcd") == "sk-****abcd"
    assert mask("short") == "****"


def test_jianying_draft_structure(tmp_path):
    """剪映草稿导出：三轨齐全、时长=音频、字幕段数=有效分段（手册优化）。"""
    import json, wave, struct
    from app.modules import jianying
    from PIL import Image

    # 造 3 张测试图 + 一段 6s 静音 wav
    imgs = []
    for i in range(3):
        p = tmp_path / f"img{i}.png"
        Image.new("RGB", (108, 192), (i * 40, 100, 150)).save(p)
        imgs.append(str(p))
    audio = tmp_path / "a.wav"
    with wave.open(str(audio), "w") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
        w.writeframes(struct.pack("<" + "h" * 48000, *([0] * 48000)))  # 6s

    segs = [{"text": "第一段"}, {"text": "第二段"}, {"text": ""}]  # 含空段
    r = jianying.build_draft(imgs, [2, 2, 2], str(audio), segs,
                             tmp_path / "draft", "t1")
    assert abs(r["duration"] - 6.0) < 0.2
    d = json.load(open(r["draft_path"], encoding="utf-8"))
    by_type = {t["type"]: len(t["segments"]) for t in d["tracks"]}
    assert by_type["video"] == 3       # 3 张图
    assert by_type["audio"] == 1       # 1 段音频
    assert by_type["text"] == 2        # 空段被跳过，只剩 2 条字幕
    assert d["canvas_config"]["width"] == 1080
