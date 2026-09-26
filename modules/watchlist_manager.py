import json
import os
import time

class WatchlistManager:
    """
    模块 2：多用户父子自选股管理器
    职责：本地 JSON 存储、支持自主注册新账号、用户数据绝对隔离、多级树形分组
    """
    def __init__(self, data_path=None):
        if not data_path:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            data_dir = os.path.join(base_dir, "data")
            os.makedirs(data_dir, exist_ok=True)
            self.data_file = os.path.join(data_dir, "watchlist.json")
        else:
            self.data_file = data_path
        self.data = self._load()

    def _load(self):
        if not os.path.exists(self.data_file):
            self._save({})
            return {}
        try:
            with open(self.data_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save(self, data):
        with open(self.data_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ---------------- 账号自主注册 ----------------
    def register_user(self, name: str, user_id: str = None) -> tuple:
        """用户自主注册账号"""
        name = name.strip()
        if not name:
            return False, "用户姓名不能为空"

        if not user_id:
            user_id = f"user_{int(time.time())}"

        for uid, uinfo in self.data.items():
            if isinstance(uinfo, dict) and uinfo.get("name") == name:
                return False, f"用户【{name}】已存在，无需重复注册"

        self.data[user_id] = {
            "name": name,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "folders": {
                "⚡ 短线博弈池": {
                    "T+1 重点冲高": [],
                    "放量突破观察": []
                },
                "🛡️ 长线价值底仓": {
                    "高股息稳健分红": [],
                    "低价高性价比潜伏": []
                }
            }
        }
        self._save(self.data)
        return True, f"用户【{name}】注册成功！账号ID: {user_id}"

    def get_users(self) -> dict:
        """安全获取所有已注册用户：返回 {user_id: 用户名}"""
        return {uid: uinfo.get("name", uid) for uid, uinfo in self.data.items() if isinstance(uinfo, dict)}

    def get_folders(self, user_id: str) -> dict:
        """获取指定用户独有的所有父子文件夹"""
        return self.data.get(user_id, {}).get("folders", {})

    # ---------------- 自选股与文件夹管理 ----------------
    def create_folder(self, user_id: str, parent_folder: str, child_folder: str = None):
        """新建父文件夹或子文件夹"""
        user_folders = self.data.setdefault(user_id, {"name": user_id, "folders": {}}).setdefault("folders", {})
        if child_folder:
            parent = user_folders.setdefault(parent_folder, {})
            parent.setdefault(child_folder, [])
        else:
            user_folders.setdefault(parent_folder, {})
        self._save(self.data)
        return True, "文件夹创建成功"

    def add_stock(self, user_id: str, parent_folder: str, child_folder: str, code: str):
        """向指定用户的某个子文件夹中添加股票"""
        code = str(code).strip()
        user_folders = self.data.setdefault(user_id, {"name": user_id, "folders": {}}).setdefault("folders", {})
        parent = user_folders.setdefault(parent_folder, {})
        stocks = parent.setdefault(child_folder, [])
        if code not in stocks:
            stocks.append(code)
            self._save(self.data)
            return True, f"股票 {code} 已加入 [{parent_folder} -> {child_folder}]"
        return False, "该股票已在该分组中"

    def remove_stock(self, user_id: str, parent_folder: str, child_folder: str, code: str):
        """从指定用户的分组中移出股票"""
        try:
            self.data[user_id]["folders"][parent_folder][child_folder].remove(code)
            self._save(self.data)
            return True, "删除成功"
        except Exception:
            return False, "未找到该股票"

# ----------------- 独立验收测试 -----------------
if __name__ == "__main__":
    print(">>> 正在独立测试 [模块 2：多用户自选管理器]...")
    wm = WatchlistManager()

    # 1. 注册测试
    wm.register_user("本人", user_id="user_admin")
    wm.register_user("家庭成员A", user_id="user_family_a")

    # 2. 模拟各自独立添加自选股（数据完全隔离）
    wm.add_stock("user_admin", "⚡ 短线博弈池", "T+1 重点冲高", "002475")
    wm.add_stock("user_family_a", "🛡️ 长线价值底仓", "高股息稳健分红", "600900")

    print("\n✅ 注册测试完成！已注册用户名单:")
    for uid, uname in wm.get_users().items():
        print(f"   • 账号ID: {uid} | 姓名: {uname}")

    print("\n📂 家庭成员A 的专属自选股（完全独立）:")
    print(json.dumps(wm.get_folders("user_family_a"), ensure_ascii=False, indent=2))