"""全链路真实验证脚本。

用法：
  1. 设置环境变量（不要写进代码）：
       export LLM_API_KEY=sk-xxxx          # 必填，LLM 密钥
       export LLM_PROVIDER=deepseek        # 可选，默认 deepseek
       export LLM_MODEL=deepseek-chat      # 可选
       export IMAGE_API_KEY=               # 可选，留空走 mock 占位图
       export IMAGE_PROVIDER=doubao        # 可选
  2. 运行：python verify_e2e.py

流程：配置写库 → 创建任务 → 跑 A/B/H/F/E → 生成测试音频 → 上传触发 G → 校验 MP4。
"""
import os
import sys
import time
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.core.database import init_db, SessionLocal
from app.core.security import encrypt
from app.models import Config, Task, ModuleResult
from app.services import orchestrator, compose

TRANSCRIPT = (
    "今天给大家讲一个传奇女子的故事她叫刘娥是北宋第一位临朝称制的太后"
    "刘娥出身贫寒早年甚至以击鼗卖艺为生后来被卖给银匠龚美带到了京城"
    "机缘巧合下她认识了当时还是襄王的赵恒也就是后来的宋真宗"
    "两人一见倾心可这段感情遭到了宋太宗的强烈反对刘娥被赶出了王府"
    "但赵恒始终没有放弃她偷偷把她安置在外宅一藏就是十五年"
    "等到赵恒登基为帝刘娥终于被接回宫中凭借过人的才智一步步从美人做到皇后"
    "真宗晚年多病朝政几乎都由刘娥处理她去世后宋仁宗才知道自己的身世"
    "这就是狸猫换太子传说的历史原型你觉得刘娥这一生是幸运还是不幸呢"
)


def setup_config(db):
    cfg = db.get(Config, 1) or Config(id=1)
    cfg.llm_provider = os.environ.get("LLM_PROVIDER", "deepseek")
    cfg.llm_model = os.environ.get("LLM_MODEL", "deepseek-chat")
    cfg.llm_api_key_enc = encrypt(os.environ["LLM_API_KEY"])
    cfg.image_provider = os.environ.get("IMAGE_PROVIDER", "doubao")
    img_key = os.environ.get("IMAGE_API_KEY", "")
    cfg.image_api_key_enc = encrypt(img_key) if img_key else None
    db.add(cfg)
    db.commit()
    print(f"[config] LLM={cfg.llm_provider}/{cfg.llm_model} "
          f"image={cfg.image_provider} ({'real' if img_key else 'mock'})")


def make_test_audio(path: Path, seconds: int = 30):
    """用 imageio-ffmpeg 自带二进制生成一段静音测试音频。"""
    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([ff, "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                    "-t", str(seconds), str(path)], capture_output=True)


def show_module(db, task_id, module):
    mr = db.query(ModuleResult).filter_by(task_id=task_id, module=module).first()
    if not mr:
        print(f"  [{module}] (未执行)")
        return
    out = mr.output
    preview = ""
    if isinstance(out, dict):
        if "cleaned_text" in out:
            preview = out["cleaned_text"][:60] + "..."
        elif "script" in out:
            preview = out["script"][:60] + "..."
        elif "segments" in out:
            preview = f"{len(out['segments'])} 段"
        elif "passed" in out:
            preview = f"passed={out['passed']} risk={out.get('risk_score')}"
        elif "images" in out:
            preview = f"{len(out['images'])} 张图"
    print(f"  [{module}] {mr.status} cost={float(mr.cost)} {preview}")


def main():
    if not os.environ.get("LLM_API_KEY"):
        print("ERROR: 请先设置环境变量 LLM_API_KEY")
        sys.exit(1)

    init_db()
    db = SessionLocal()
    try:
        setup_config(db)

        task = Task(id="task_verify_e2e", transcript=TRANSCRIPT,
                    modules=["A", "B", "D", "E", "F", "G"],
                    target_audience="50+女性", cost_limit=2.0, time_limit=900,
                    enable_subtitles=True, enable_animations=True, status="pending")
        # 清理上次遗留
        old = db.get(Task, task.id)
        if old:
            db.query(ModuleResult).filter_by(task_id=task.id).delete()
            db.delete(old)
            db.commit()
        db.add(task)
        db.commit()
        print(f"[task] created {task.id}")

        print("[pipeline] 运行文案+配图链路 (A→B→H→F→D→E)...")
        t0 = time.time()
        orchestrator.run_pipeline(db, task.id)
        db.refresh(task)
        print(f"[pipeline] 完成 status={task.status} "
              f"cost={float(task.total_cost):.4f}元 用时={time.time()-t0:.1f}s")
        for m in ["A", "B", "H", "F", "D", "E"]:
            show_module(db, task.id, m)

        if task.status == "blocked":
            print("[result] 文案被合规闸门拦截，链路正确触发 blocked。验证结束。")
            return
        if task.status != "awaiting_audio":
            print(f"[result] 链路未到合成阶段，status={task.status} "
                  f"err={task.error_code}/{task.error_message}")
            return

        audio = Path("storage") / task.id / "audio" / "test.mp3"
        print("[audio] 生成 30s 测试音频...")
        make_test_audio(audio, 30)

        print("[compose] 触发视频合成...")
        t1 = time.time()
        res = compose.compose_video(db, task.id, str(audio), True, True)
        print(f"[compose] 完成 用时={time.time()-t1:.1f}s -> {res}")

        # 校验输出
        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
        r = subprocess.run([ff, "-i", res["video_path"]], capture_output=True, text=True)
        for line in r.stderr.splitlines():
            if "Stream" in line or "Duration" in line:
                print("  " + line.strip())
        db.refresh(task)
        print(f"[result] 全链路完成 status={task.status} "
              f"总成本={float(task.total_cost):.4f}元")
    finally:
        db.close()


if __name__ == "__main__":
    main()
