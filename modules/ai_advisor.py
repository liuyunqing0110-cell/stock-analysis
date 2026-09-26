import os
import json
import requests
import re
from openai import OpenAI

# 1. 自动加载 .env 密钥
def auto_load_env():
    possible_paths = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"),
        ".env"
    ]
    for p in possible_paths:
        if os.path.exists(p):
            for enc in ["utf-8-sig", "utf-8", "gbk"]:
                try:
                    with open(p, "r", encoding=enc) as f:
                        for line in f:
                            line = line.strip()
                            if line and not line.startswith("#") and "=" in line:
                                k, v = line.split("=", 1)
                                os.environ[k.strip().lstrip("\ufeff")] = v.strip().strip("'\"")
                    break
                except: pass
        if os.getenv("DEEPSEEK_API_KEY"): break

auto_load_env()

class DeepSeekAdvisor:
    def __init__(self):
        self.api_key = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
        self.base_url = (os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").strip()
        self.model = (os.getenv("AI_MODEL") or "deepseek-chat").strip()
        
        if not self.api_key:
            raise ValueError("未检测到有效 DEEPSEEK_API_KEY，请确认 .env 文件。")
            
        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        self.headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}

    # 准确获取个股最新现价与浮亏数据
    def get_realtime_data(self, portfolio):
        enriched = []
        for p in portfolio:
            code = str(p.get("代码", "")).strip()
            cost = float(p.get("成本价", 0.0))
            shares = int(p.get("持仓股数", 1000))
            name = p.get("名称", "股票")
            
            prefix = "sh" if code.startswith(("6", "9")) else "sz" if code.startswith(("0", "3")) else "bj"
            symbol = f"{prefix}{code}"
            
            curr_price = 0.0
            try:
                r = requests.get(f"http://hq.sinajs.cn/list={symbol}", headers=self.headers, timeout=4)
                r.encoding = "gbk"
                m = re.search(r'="([^"]+)"', r.text)
                if m:
                    fields = m.group(1).split(",")
                    name = fields[0] or name
                    idx_price = 3  # 下标 3 严格为当前最新现价
                    if len(fields) > idx_price and fields[idx_price]:
                        curr_price = float(fields[idx_price])
            except Exception as e:
                print(f"行情拉取提示: {e}")
                
            if curr_price <= 0:
                curr_price = cost
                
            loss_pct = round(((curr_price - cost) / cost) * 100, 2) if cost else 0.0
            total_loss = round((curr_price - cost) * shares, 2) if cost else 0.0
            needed_gain = round(((cost - curr_price) / curr_price) * 100, 2) if curr_price else 0.0
            
            enriched.append({
                "代码": code,
                "名称": name,
                "买入成本": cost,
                "当前现价": curr_price,
                "持仓股数": shares,
                "浮动盈亏比例": f"{loss_pct:+}%",
                "浮动盈亏金额": f"{total_loss:+} 元",
                "直接回本所需涨幅": f"+{needed_gain}%"
            })
        return enriched

    # 纯干货、无废话的 DeepSeek 诊断
    def ask_deepseek(self, portfolio_data):
        data_str = json.dumps(portfolio_data, ensure_ascii=False, indent=2)
        
        prompt = f"""
你是一名专业私募基金投资总监。请针对以下真实持仓数据，输出一份【极度精炼、纯干货、零废话】的操盘手实战执行策略：
{data_str}

【硬性表达要求】：
1. 严禁任何寒暄、称呼、情绪安慰或“掐烟/深呼吸/兄弟”等废话口水词；
2. 直奔主题，条理清晰，严格输出以下四个部分，数据全部量化到具体点位：

### 一、 盘口健康度与筹码结构定性
- 当前价格位置的安全边际、上方密集套牢抛压位、下方关键支撑位。

### 二、 亏损最小化实战：日内做 T 降本执行单
- 具体的做 T 回踩买入价、冲高交出卖出价；
- 测算每笔做 T 净赚差价与预期拉低成本幅度。

### 三、 反败为胜策略：金字塔企稳加仓与盈利测算
- 在什么支撑位可执行等额补仓？补仓后的新综合成本是多少？
- 给出加仓后的两个目标收割位及对应的净赚金额。

### 四、 涨乐财富通条件单设置清单（供直接照抄）
- 整理为清晰的表格，列出：条件单类型、触发价格、委托数量、有效期。
"""
        res = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2
        )
        return res.choices[0].message.content

def main():
    advisor = DeepSeekAdvisor()
    
    # 动态加载用户真实持仓文件，绝不写死任何股票
    possible_files = [
        "my_portfolio.json",
        "modules/my_portfolio.json",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "my_portfolio.json"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "my_portfolio.json")
    ]
    
    portfolio = []
    found_file = None
    for pf in possible_files:
        if os.path.exists(pf) and os.path.getsize(pf) > 10:
            try:
                with open(pf, "r", encoding="utf-8") as f:
                    portfolio = json.load(f)
                found_file = pf
                break
            except: pass
            
    if not portfolio:
        print("❌ 未检测到有效的 my_portfolio.json 持仓记录！请先运行 add_stock.py 添加股票。")
        return
        
    print(f"📂 已载入持仓文件 ({found_file})，共追踪 {len(portfolio)} 只股票")
    print("📈 正在获取真实盘口与浮亏数据...")
    realtime_data = advisor.get_realtime_data(portfolio)
    for p in realtime_data:
        print(f"  • {p['名称']}({p['代码']}): 成本 {p['买入成本']} -> 真实最新价 {p['当前现价']} | 盈亏: {p['浮动盈亏比例']} ({p['浮动盈亏金额']})")

    print("\n🤖 DeepSeek 首席操盘手实战执单生成中...\n" + "═"*60 + "\n")
    response = advisor.ask_deepseek(realtime_data)
    print(response)

if __name__ == "__main__":
    main()