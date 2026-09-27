from datetime import datetime
import os
import subprocess
import time

WATCH_DIR = os.path.dirname(os.path.abspath(__file__))
IGNORE_EXT = [".pyc", ".log", ".tmp", ".git"]


def get_last_mtime():
  max_mtime = 0
  for root, dirs, files in os.walk(WATCH_DIR):
    if ".git" in root or "__pycache__" in root:
      continue
    for f in files:
      if any(f.endswith(ext) for ext in IGNORE_EXT) or f in [
          ".env",
          "my_portfolio.json",
      ]:
        continue
      try:
        mt = os.path.getmtime(os.path.join(root, f))
        if mt > max_mtime:
          max_mtime = mt
      except Exception:
        pass
  return max_mtime


def auto_git_push():
  now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
  print(f"[{now}] 检测到代码更新，正在自动推送至 GitHub...")
  subprocess.run(["git", "add", "."], cwd=WATCH_DIR, capture_output=True)
  subprocess.run(
      ["git", "commit", "-m", f"auto-sync: {now}"],
      cwd=WATCH_DIR,
      capture_output=True,
  )
  res = subprocess.run(
      ["git", "push", "origin", "main"], cwd=WATCH_DIR, capture_output=True
  )
  if res.returncode == 0:
    print(f"[{now}] ✅ 自动同步成功！")


if __name__ == "__main__":
  last_time = get_last_mtime()
  print(">>> 自动监听与无感同步已启动（修改文件并保存后将自动提交）...")
  while True:
    time.sleep(5)  # 每 5 秒轮询一次
    current_time = get_last_mtime()
    if current_time > last_time:
      time.sleep(2)  # 等待保存完成
      auto_git_push()
      last_time = get_last_mtime()