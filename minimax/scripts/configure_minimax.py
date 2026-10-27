"""Interactive local setup: a masked prompt, no secrets in console output or Git."""
from getpass import getpass
import json
import os
from pathlib import Path

root = Path(__file__).resolve().parents[1]
print("MiniMax 本机配置 / Local-only credential setup")
print("请在官方控制台自己创建并复制 API Key，在下面粘贴。输入不会显示。")
print("无需把 Key 发到聊天；此配置位于 Git 忽略的 .local/ 目录。")
key = getpass("MiniMax API Key (hidden): ").strip()
if not key or "\n" in key or "\r" in key:
    raise SystemExit("No valid key entered; nothing was saved.")
target = root / ".local/credentials.json"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps({"api_key": key, "model": "MiniMax-M3",
                              "base_url": "https://api.minimax.cn/anthropic"}, indent=2), encoding="utf-8")
os.chmod(target, 0o600)
print("配置已保存；未进行模型调用。")
input("按回车关闭窗口 / Press Enter to close.")
