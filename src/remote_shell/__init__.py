"""远程模式瘦壳（Remote Mode shell）。

桌面壳开启远程模式后，不再启动完整后端，改跑本包：
``python -m src.remote_shell``。只伺服前端 SPA、录音代理、MCP Hub 与
壳级设置，见 ``app.py`` 与 development_doc/2026-09-21-remote-mode-design.md。
"""
