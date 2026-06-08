"""PyInstaller 打包入口：把 FastAPI 后端冻结成单可执行，供 Electron 作为 sidecar 拉起。
读 Electron 主进程注入的 APP_HOST/APP_PORT 环境变量启动 uvicorn。"""
import os
import uvicorn


def main():
    host = os.environ.get("APP_HOST", "127.0.0.1")
    port = int(os.environ.get("APP_PORT", "8765"))
    # 冻结环境下不能用 reload / 字符串 import 路径，直接传 app 对象。
    from app.main import app
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
