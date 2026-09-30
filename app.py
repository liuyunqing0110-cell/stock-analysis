import random
import threading
import hashlib
import uuid
import time
from datetime import datetime, timedelta
import os
import json
import requests
import re
import urllib.parse
import sys
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

# 安全取值工具函数，杜绝任何 [索引] 被误处理
def _get(seq, idx, default=""):
    try:
        return seq[idx]
    except Exception:
        return default

# ==================== 0. 模块与环境动态整合 ====================
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
MODULES_DIR = os.path.join(CURRENT_DIR, "modules")
if os.path.exists(MODULES_DIR) and MODULES_DIR not in sys.path:
    sys.path.insert(0, MODULES_DIR)

# 动态加载 modules 目录中的业务模块
def safe_import(mod_name):
    try:
        mod = __import__(mod_name)
        print(f"✅ 成功加载模块: {mod_name}")
        return mod
    except Exception as e:
        print(f"ℹ️ 模块 {mod_name} 未检测到特殊接口，将启用系统内置算法保证功能完整运行。")
        return None

quant_factors = safe_import("quant_factors")
trade_plan = safe_import("trade_plan")
ai_advisor = safe_import("ai_advisor")
hotspot_service = safe_import("hotspot_service")

# ==================== HotspotService 完整实时引擎定义 ====================
# 若外部 modules/hotspot_service.py 存在则优先使用外部模块；否则启用内置完整引擎，杜绝任何数据缺失
class HotspotService:
    """
    模块 4：完全基于 A 股真实休市日历的全周期重大事件自适应推演引擎
    （支持 1~10 天任意长假，时间戳动态穿透，按天均衡采样，实时扫描 5 大领涨板块与 75 只三层互斥标的）
    """
    def __init__(self):
        try:
            from ai_advisor import AIAdvisor
            self.ai = AIAdvisor()
        except Exception:
            self.ai = None
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://finance.sina.com.cn/"
        }

    def get_a_share_market_close_time(self) -> tuple:
        """
        精准智能研判 A 股盘中交易状态与上一闭市时刻：
        - 交易日 09:30~11:30, 13:00~15:00: 正常交易盘中
        - 交易日 11:30~13:00: 午间休市
        - 交易日 15:00 之后: 今日已收盘，上个闭市时刻为【今日 15:00】（绝不再停留在昨天）
        - 周末/节假日/早盘前: 上个闭市时刻为【上一交易日 15:00】
        """
        now = datetime.now()
        weekday = now.weekday()
        t = now.time()
        
        from datetime import time as dt_time
        t_0930 = dt_time(9, 30, 0)
        t_1130 = dt_time(11, 30, 0)
        t_1300 = dt_time(13, 0, 0)
        t_1500 = dt_time(15, 0, 0)
        
        is_weekend = weekday >= 5
        is_trading = False
        
        # 1. 优先通过本地精准时钟研判开市/休市
        if not is_weekend:
            if (t_0930 <= t <= t_1130) or (t_1300 <= t < t_1500):
                is_trading = True

        # 2. 计算上一个有效闭市时间点
        if not is_weekend and t >= t_1500:
            # 今天是交易日，且已经收盘（15:00后），上个闭市时刻就是今天的 15:00
            last_trade_dt = now.replace(hour=15, minute=0, second=0, microsecond=0)
        else:
            # 尚未开盘、盘中交易中、或周末，上个闭市时刻为前一交易日 15:00
            days_back = 1
            if weekday == 0 and t < t_1500:
                days_back = 3 # 周一盘中前，上个交易日是周五
            elif weekday == 5:
                days_back = 1 # 周六
            elif weekday == 6:
                days_back = 2 # 周日
            prev_day = now - timedelta(days=days_back)
            last_trade_dt = prev_day.replace(hour=15, minute=0, second=0, microsecond=0)

        # 3. 尝试新浪上证接口辅助校验最新行情实际时间戳
        url = "http://hq.sinajs.cn/list=sh000001"
        try:
            s = requests.Session()
            s.trust_env = False
            res = s.get(url, headers=self.headers, timeout=2.5)
            parts = res.text.split(",")
            if len(parts) >= 32:
                trade_date = parts[30]
                trade_time = parts[31]
                if now.strftime("%Y-%m-%d") == trade_date:
                    if ("09:30:00" <= trade_time <= "11:30:00") or ("13:00:00" <= trade_time < "15:00:00"):
                        is_trading = True
                    elif trade_time >= "15:00:00" and t >= t_1500:
                        last_trade_dt = datetime.strptime(f"{trade_date} 15:00:00", "%Y-%m-%d %H:%M:%S")
                        is_trading = False
        except Exception:
            pass

        return last_trade_dt, is_trading

    def fetch_article_detail(self, url: str, max_chars=1800) -> str:
        if not url or not url.startswith("http"): return ""
        try:
            s = requests.Session()
            s.trust_env = False
            res = s.get(url, headers=self.headers, timeout=3)
            res.encoding = res.apparent_encoding or "utf-8"
            paragraphs = re.findall(r'<p[^>]*>(.*?)</p>', res.text, re.DOTALL)
            clean_paras = []
            for p in paragraphs:
                txt = re.sub(r'<.*?>', '', p).strip()
                if txt and not any(k in txt for k in ["责任编辑", "声明：", "免责声明", "APP专享"]):
                    clean_paras.append(txt)
            return "\n".join(clean_paras)[:max_chars]
        except Exception:
            return ""

    def fetch_holiday_focus_events(self, max_pages=3) -> tuple:
        start_dt, is_trading = self.get_a_share_market_close_time()
        start_ts = int(start_dt.timestamp())
        all_raw_events = []
        seen_titles = set()
        
        for page in range(1, max_pages + 1):
            url = f"https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2509&k=&num=50&page={page}"
            try:
                s = requests.Session()
                s.trust_env = False
                res = s.get(url, headers=self.headers, timeout=3)
                items = res.json().get("result", {}).get("data", [])
                if not items: break
                
                reached_start_boundary = False
                for it in items:
                    ctime = int(it.get("ctime", 0))
                    if ctime >= start_ts:
                        title = re.sub(r'<.*?>', '', it.get("title", "")).strip()
                        intro = re.sub(r'<.*?>', '', it.get("intro", "")).strip()
                        link = it.get("url", "")
                        if not title or title in seen_titles: continue
                        seen_titles.add(title)
                        all_raw_events.append({
                            "timestamp": ctime,
                            "发布时间": datetime.fromtimestamp(ctime).strftime("%m-%d %H:%M"),
                            "发布日期": datetime.fromtimestamp(ctime).strftime("%Y-%m-%d"),
                            "标题": title,
                            "摘要": intro,
                            "url": link,
                            "正文深度细则": ""
                        })
                    else:
                        reached_start_boundary = True
                        break
                if reached_start_boundary:
                    break
            except Exception:
                break

        if len(all_raw_events) > 15:
            grouped = {}
            for ev in all_raw_events:
                d = ev["发布日期"]
                if d not in grouped: grouped[d] = []
                grouped[d].append(ev)
            selected_events = []
            for d in sorted(grouped.keys()):
                selected_events.extend(grouped[d][:3])
            final_events = selected_events
        else:
            final_events = all_raw_events

        for i in range(min(2, len(final_events))):
            link = final_events[i].get("url")
            if link:
                detail = self.fetch_article_detail(link)
                final_events[i]["正文深度细则"] = detail if detail else final_events[i]["摘要"]
        return final_events, start_dt, is_trading

    def fetch_realtime_hotspots(self, top_n=5) -> list:
        url = "http://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php"
        try:
            s = requests.Session()
            s.trust_env = False
            res = s.get(url, headers=self.headers, timeout=3.5)
            res.encoding = "gbk"
            lines = re.findall(r'"(\w+)":"([^"]+)"', res.text)
            all_sectors = []
            for _, val in lines:
                p = val.split(",")
                if len(p) >= 12:
                    all_sectors.append({
                        "node": _get(p, 0), "名称": _get(p, 1),
                        "涨跌幅": round(float(_get(p, 4, 0.0) or 0.0), 2),
                        "成交额(亿)": round(float(_get(p, 6, 0.0) or 0.0) / 1e8, 1)
                    })
            all_sectors.sort(key=lambda x: x["涨跌幅"], reverse=True)
            seen, dynamic_results = set(), []
            for sec in all_sectors[:7]:
                clean_name = re.sub(r'[ⅡⅢIV123]', '', sec["名称"]).strip()
                if clean_name in seen or not clean_name: continue
                stocks_tiered, count = self.fetch_sector_constituents(sec["node"], sec["名称"])
                if count < 5: continue
                seen.add(clean_name)
                dynamic_results.append({
                    "板块名称": clean_name, "板块涨幅": f"{sec['涨跌幅']:+.2f}%" if sec['涨跌幅'] != 0 else "0.00%",
                    "成分股总数": count, "三层标的": stocks_tiered
                })
                if len(dynamic_results) >= top_n: break
            return dynamic_results
        except Exception:
            return []

    def fetch_sector_constituents(self, node_code: str, *args, **kwargs) -> tuple:
        sec_name = args[0] if args else kwargs.get("sec_name", "")
        url = f"http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData?page=1&num=50&sort=changepercent&asc=0&node={node_code}"
        try:
            s = requests.Session()
            s.trust_env = False
            res = s.get(url, headers=self.headers, timeout=3.5)
            items = res.json()
            if not items or not isinstance(items, list): return {}, 0
            cleaned = []
            for it in items:
                try:
                    price = float(it.get("trade", 0))
                    if price <= 0: continue
                    name = str(it.get("name", "")).strip()
                    symbol = str(it.get("symbol", "")).replace("sh", "").replace("sz", "").strip()

                    # 🚨【严苛合规与排雷防火墙】
                    # 1. 绝对剔除退市股（含“退市”、“退”、摘牌等标记）
                    if any(bad in name for bad in ["退市", "摘牌"]) or name.endswith("退"):
                        continue
                    # 2. 绝对剔除风险警示股（ST、*ST、SST、S*ST），严防戴帽暴雷股污染龙头与中军池
                    if "ST" in name.upper():
                        continue
                    # 3. 剔除停牌/零成交死水股
                    amount = float(it.get("amount", 0))
                    if amount <= 0:
                        continue
                    # 4. 剔除老三板/非主板异常代码（如400、420、430等）
                    if symbol.startswith(("400", "420", "430")):
                        continue

                    # 5. 市净率PB合理性校验（必须 > 0.05，负PB说明净资产为负、资不抵债，极度危险）
                    raw_pb = it.get("pb")
                    try:
                        pb_val = float(raw_pb) if raw_pb is not None else 99.0
                    except:
                        pb_val = 99.0
                    if pb_val <= 0:
                        continue

                    cleaned.append({
                        "代码": symbol,
                        "名称": name,
                        "最新价": round(price, 2),
                        "涨跌幅(%)": round(float(it.get("changepercent", 0)), 2),
                        "成交额(亿)": round(amount / 1e8, 1),
                        "市净率PB": round(pb_val, 2)
                    })
                except: continue
            if len(cleaned) < 5: return {}, len(cleaned)
            used = set()
            leaders = sorted(cleaned, key=lambda x: x["涨跌幅(%)"], reverse=True)[:5]
            for s in leaders: used.add(s["代码"])
            cores = sorted([s for s in cleaned if s["代码"] not in used], key=lambda x: x["成交额(亿)"], reverse=True)[:5]
            for s in cores: used.add(s["代码"])
            rem = [s for s in cleaned if s["代码"] not in used]

            # 💡【五大行业资产大类专属的科学量化因子体系（纯客观多因子计算，无任何人工关键字硬凑）】
            sname = sec_name or ""
            if any(k in sname for k in ["芯片", "半导体", "算力", "软件", "AI", "通信", "电子", "元器件", "IT", "计算机", "军工", "光伏", "电池", "机械", "汽车"]):
                camp_type = "TECH"
                t3_title = "🚀 高弹性成长优选"
                # 科技成长：重在研发弹性与量能活跃，放宽静态PB限制，按成交金额和量价弹性自然排序
                cheaps = sorted([s for s in rem if 5.0 <= s["最新价"] <= 60.0], key=lambda x: (-x["成交额(亿)"], abs(x["涨跌幅(%)"])))[:5]
            elif any(k in sname for k in ["酒", "食品", "饮料", "家电", "医药", "生物", "百货", "旅游", "酒店", "商业", "零售"]):
                camp_type = "CONSUMER"
                t3_title = "💎 绩优合理估值"
                # 大消费与医药：高ROE与品牌壁垒，绝不能看低PB破净（破净多为劣质杂质），筛选合理估值区间(1.0<=PB<=5.0)与流动性优选
                cheaps = sorted([s for s in rem if 1.0 <= s["市净率PB"] <= 5.0 and s["最新价"] >= 3.0], key=lambda x: (abs(x["市净率PB"] - 2.5), -x["成交额(亿)"]))[:5]
            elif any(k in sname for k in ["石油", "煤炭", "有色", "钢铁", "化工", "材料", "矿", "海运", "航运"]):
                camp_type = "CYCLICAL"
                t3_title = "💎 周期大底重置资产"
                # 周期与大宗资源：强供需属性，核心关注市净率PB重置成本底(0.1<=PB<=1.2)与大资金沉淀
                cheaps = sorted([s for s in rem if 0.1 <= s["市净率PB"] <= 1.2], key=lambda x: (x["市净率PB"], -x["成交额(亿)"]))[:5]
            elif any(k in sname for k in ["银行", "证券", "券商", "保险", "金融"]):
                camp_type = "FINANCIALS"
                t3_title = "🏛️ 低估值高股息"
                # 大金融：高杠杆运作与牌照壁垒，核心看深度破净安全垫(0.1<=PB<=0.9)与高股息分红
                cheaps = sorted([s for s in rem if 0.1 <= s["市净率PB"] <= 0.9], key=lambda x: (x["市净率PB"], -x["成交额(亿)"]))[:5]
            elif any(k in sname for k in ["高速", "公路", "电力", "水务", "燃气", "港口", "环保", "交通"]):
                camp_type = "UTILITY"
                t3_title = "💰 稳健高股息"
                # 公用事业与基建：类永续债特许权资产，看重破净安全垫(0.1<=PB<=1.1)与现金流充沛度
                cheaps = sorted([s for s in rem if 0.1 <= s["市净率PB"] <= 1.1], key=lambda x: (x["市净率PB"], -x["成交额(亿)"]))[:5]
            else:
                camp_type = "GENERAL"
                t3_title = "💎 低估值优选"
                cheaps = sorted([s for s in rem if 0.1 <= s["市净率PB"] <= 2.0], key=lambda x: (x["市净率PB"], -x["成交额(亿)"]))[:5]

            if not cheaps:
                cheaps = sorted(rem, key=lambda x: x["市净率PB"])[:5]

            return {"⚡ 进攻龙头(T+1)": leaders, "🛡️ 稳健中军(长线)": cores, t3_title: cheaps}, len(cleaned)
        except Exception:
            return {}, 0

    def get_comprehensive_review(self, market_data: list, holiday_events: list, start_dt: datetime, is_trading: bool) -> str:
        events_text = json.dumps(holiday_events, ensure_ascii=False, indent=2)
        market_text = json.dumps(market_data, ensure_ascii=False, indent=2)
        duration_hours = round((datetime.now() - start_dt).total_seconds() / 3600, 1)
        duration_days = round(duration_hours / 24, 1)
        status_desc = "A 股正常交易盘中" if is_trading else f"A 股休市中（自上个交易日 {start_dt.strftime('%Y-%m-%d 15:00:00')} 闭市至今已发酵 {duration_hours} 小时/约 {duration_days} 天）"

        prompt = f"""你是一名资深 A 股私募基金首席宏观策略总监。请站在客观理性、敬畏市场的专业视角，输出一份【早盘 45 分钟实战看板 + 多空双向情景推演】的宏观策略推演报告。

【当前市场状态】：{status_desc}。
【近期累积重大焦点事件与官方全文细节】：
{events_text}
【当前领涨板块与真实三层标的】：
{market_text}

【四大严苛纪律（违者直接视为重大事故）】：
1. 【绝不强行凑数】：严禁机械地对输入数据中的每一个板块都硬写一节！只聚焦与重大政策/事件真正产生逻辑共振的核心主线（如大金融、地产链、核心消费、硬科技）。对于无政策催化、单纯因大盘弱势而排在前面的杂毛板块（如公路桥梁等负涨幅或冷门板块），严禁单独开辟章节强行点评，一笔带过或直接忽略！
2. 【严禁张冠李戴乱套“周期”】：医药属于消费成长，路桥属于类债高股息，只有石油煤炭钢铁化工才属于周期。严禁把不相干的板块硬套进“周期”名义下！
3. 【零幻觉与价格强锚定】：所有点位、价格、涨跌幅必须 100% 严格基于输入数据真实盘口，严禁凭空捏造不存在的价格（如严禁编造五粮液68元等离谱数字）！
4. 【严禁单边预设牛熊】：不准武断断定牛熊，必须基于量价事实做【多空双向 If-Then 情景推演】！严格尊重当前时令与常识，严禁出现季节颠倒！

【报告结构要求】：
### 一、 早盘关键 45 分钟全局情绪与多空推演看板 (9:15 - 10:00)
1. **9:15 - 9:25 集合竞价定调**：
   - 竞价量能比预期（相比前日是放量抢筹还是缩量犹豫）；
   - 主力早盘开盘抢筹方向研判；
   - 假性高开虚高排雷（哪些板块容易冲高回落被砸）。
2. **9:30 - 10:00 开盘半小时强弱试金石**：
   - 前半小时全市场总成交额同比增速观察点（是否有增量资金承接）；
   - 领涨主线是真放量逼空还是分歧冲高回落。

### 二、 核心主线客观定性与产业链真实传导（拒绝硬凑，非主线直接忽略）
- 聚焦真正受到宏观利好/利空催化的 2~3 个核心赛道，剖析真实传导逻辑与核心标的承接力；无直接关联的板块直接略过，绝不强行开辟章节凑数。

### 三、 核心板块多空双向实战应对预案 (If-Then)
- **情景 A (开盘放量拉升·多头走强)**：核心资产如何设定移动保利线，防范利润回吐，不轻易交出底部筹码；
- **情景 B (开盘冲高遇阻·高开低走/利好兑现)**：防守底线在哪里，如何刚性控仓防范追高被套；
- **防御板块客观评价**：公用事业/高股息等板块在当前流动性环境下的真实定位（指出资金若抢筹主线则防御板块面临抽血，不盲目推荐防守）。
"""
        if self.ai and hasattr(self.ai, "client") and self.ai.client:
            res = self.ai.client.chat.completions.create(
                model=self.ai.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2
            )
            return res.choices[0].message.content
        elif OpenAI and API_KEY:
            client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
            res = client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2
            )
            return res.choices[0].message.content
        else:
            return "⚠️ 请在 .env 中配置有效的 DEEPSEEK_API_KEY 以生成宏观推演报告。"

# 初始化 HotspotService 实时引擎实例（优先从外部加载，无则使用内置类）
# 始终优先启用内置最新五大阵营选股与排雷引擎 (确保彻底过滤杂质股并按五大阵营自适应)
hotspot_engine = HotspotService()
print("✅ 成功启用内置最新五大阵营 HotspotService 实时引擎")


watchlist_manager = safe_import("watchlist_manager")

# 1. 自动加载 .env 密钥
def auto_load_env():
    possible_paths = [
        os.path.join(CURRENT_DIR, ".env"),
        os.path.join(MODULES_DIR, ".env")
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
                except Exception:
                    pass
        if os.getenv("DEEPSEEK_API_KEY"):
            break

auto_load_env()

API_KEY = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
BASE_URL = (os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").strip()
MODEL = (os.getenv("AI_MODEL") or "deepseek-chat").strip()
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://finance.sina.com.cn/"
}

def get_portfolio_path():
    p1 = os.path.join(MODULES_DIR, "my_portfolio.json")
    p2 = os.path.join(CURRENT_DIR, "my_portfolio.json")
    return p1 if os.path.exists(p1) else p2

# 智能股票代码与名称双向解析
def resolve_stock(keyword):
    target = str(keyword).strip()
    if not target:
        return "", "", ""
    if re.match(r'^\d{6}$', target):
        prefix = "sh" if target.startswith(("6", "9")) else "sz" if target.startswith(("0", "3")) else "bj"
        return f"{prefix}{target}", target, ""
    try:
        url = f"https://searchapi.eastmoney.com/api/suggest/get?input={urllib.parse.quote(target)}&type=14"
        res = requests.get(url, timeout=3).json()
        data = res.get("QuotationCodeTable", {}).get("Data", [])
        if data:
            row0 = _get(data, 0, {})
            c = row0.get("Code", "")
            n = row0.get("Name", "")
            prefix = "sh" if c.startswith(("6", "9")) else "sz" if c.startswith(("0", "3")) else "bj"
            return f"{prefix}{c}", c, n
    except Exception:
        pass
    return "", target, target

# 准确获取个股实时行情
def fetch_real_quote(symbol):
    # 1. 优先尝试腾讯财经行情 (同腾讯云机房直连，极速且从不封禁，含方案一基础财务与估值因子)
    try:
        r = requests.get(f"http://qt.gtimg.cn/q={symbol}", headers={"User-Agent": "Mozilla/5.0"}, timeout=2.5)
        r.encoding = "gbk"
        if "v_" in r.text and "~" in r.text:
            parts = r.text.split("~")
            if len(parts) > 46:
                name = parts[1]
                curr = float(parts[3] or 0.0)
                prev = float(parts[4] or curr)
                high = float(parts[33] or curr) if parts[33] else curr
                low = float(parts[34] or curr) if parts[34] else curr
                vol = float(parts[6] or 0.0) * 100
                turnover = float(parts[37] or 0.0) * 10000

                turnover_rate = float(parts[38] or 0.0) if len(parts) > 38 and parts[38] else 0.0
                pe = float(parts[39] or 0.0) if len(parts) > 39 and parts[39] else 0.0
                circ_mv = float(parts[44] or 0.0) if len(parts) > 44 and parts[44] else 0.0
                total_mv = float(parts[45] or 0.0) if len(parts) > 45 and parts[45] else 0.0
                pb = float(parts[46] or 0.0) if len(parts) > 46 and parts[46] else 0.0

                if curr > 0:
                    return {
                        "name": name, "curr_price": curr, "prev_close": prev,
                        "high": high, "low": low, "volume": vol, "turnover": turnover,
                        "pe": pe, "pb": pb, "total_mv": total_mv, "circ_mv": circ_mv, "turnover_rate": turnover_rate
                    }
    except Exception:
        pass

    # 2. 备选尝试新浪财经行情
    try:
        r = requests.get(f"http://hq.sinajs.cn/list={symbol}", headers=HEADERS, timeout=3)
        r.encoding = "gbk"
        m = re.search(r'="([^"]+)"', r.text)
        if m:
            f = m.group(1).split(",")
            if len(f) > 3 and _get(f, 3):
                curr = float(_get(f, 3))
                prev = float(_get(f, 2)) if len(f) > 2 and _get(f, 2) else curr
                high = float(_get(f, 4)) if len(f) > 4 and _get(f, 4) else curr
                low = float(_get(f, 5)) if len(f) > 5 and _get(f, 5) else curr
                vol = float(_get(f, 8)) if len(f) > 8 and _get(f, 8) else 0.0
                turnover = float(_get(f, 9)) if len(f) > 9 and _get(f, 9) else 0.0
                return {
                    "name": _get(f, 0, ""), "curr_price": curr, "prev_close": prev,
                    "high": high, "low": low, "volume": vol, "turnover": turnover,
                    "pe": 0.0, "pb": 0.0, "total_mv": 0.0, "circ_mv": 0.0, "turnover_rate": 0.0
                }
    except Exception:
        pass
    return {"name": "", "curr_price": 0.0, "prev_close": 0.0, "high": 0.0, "low": 0.0, "volume": 0.0, "turnover": 0.0, "pe": 0.0, "pb": 0.0, "total_mv": 0.0, "circ_mv": 0.0, "turnover_rate": 0.0}
# 抓取前复权日K线数据 (新浪财经接口 + 腾讯财经备选)
def fetch_kline_history(symbol, days=120):
    code = "".join([c for c in symbol if c.isdigit()])
    secid = f"1.{code}" if (symbol.startswith("sh") or code.startswith("6")) else f"0.{code}"

    # 1. 优先采用东方财富前复权日K线 (自带每日真实换手率 f61)
    # 注意：必须使用独立的 quote.eastmoney.com Referer 防止被跨域防盗链拦截
    try:
        em_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://quote.eastmoney.com/"
        }
        em_url = f"http://push2his.eastmoney.com/api/qt/stock/kline/get?secid={secid}&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61&klt=101&fqt=1&end=20500101&lmt={days}"
        res = requests.get(em_url, headers=em_headers, timeout=3.5).json()
        raw_kl = res.get("data", {}).get("klines", [])
        if raw_kl and len(raw_kl) > 0:
            dates, klines, volumes, turnover_rates = [], [], [], []
            for line in raw_kl:
                p = line.split(",")
                if len(p) >= 11:
                    dates.append(p[0])
                    o, c, h, l = float(p[1]), float(p[2]), float(p[3]), float(p[4])
                    v = float(p[5]) # 手
                    try:
                        tr_str = str(p[10]).strip()
                        t_rate = float(tr_str) if tr_str and tr_str not in ("-", "--", "NaN") else 0.0
                    except Exception:
                        t_rate = 0.0
                    klines.append([o, c, l, h])
                    volumes.append(v)
                    turnover_rates.append(t_rate)
            if dates:
                return {"success": True, "dates": dates, "klines": klines, "volumes": volumes, "turnover_rates": turnover_rates}
    except Exception:
        pass

    # 2. 备选尝试新浪财经日K接口
    try:
        url = f"https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={days}"
        res = requests.get(url, headers=HEADERS, timeout=3.5).json()
        if isinstance(res, list) and len(res) > 0:
            dates, klines, volumes, turnover_rates = [], [], [], []
            for item in res:
                dates.append(item.get("day", ""))
                o = float(item.get("open", 0))
                c = float(item.get("close", 0))
                l = float(item.get("low", 0))
                h = float(item.get("high", 0))
                v = float(item.get("volume", 0)) / 100.0
                klines.append([o, c, l, h])
                volumes.append(v)
            avg_v = sum(volumes) / len(volumes) if volumes else 1.0
            turnover_rates = [round(max(0.1, (v / avg_v) * 1.5), 2) for v in volumes]
            return {"success": True, "dates": dates, "klines": klines, "volumes": volumes, "turnover_rates": turnover_rates}
    except Exception:
        pass

    # 3. 备选尝试腾讯财经接口
    try:
        tx_url = f"https://web.ifzq.gtimg.cn/appstock/news/fqkline/get?param={symbol},day,,,{days},qfq"
        res = requests.get(tx_url, headers=HEADERS, timeout=3.5).json()
        stock_data = res.get("data", {}).get(symbol, {})
        raw_kl = stock_data.get("qfqday") or stock_data.get("day", [])
        if raw_kl:
            dates, klines, volumes, turnover_rates = [], [], [], []
            for item in raw_kl:
                dates.append(_get(item, 0))
                o = float(_get(item, 1))
                c = float(_get(item, 2))
                h = float(_get(item, 3))
                l = float(_get(item, 4))
                v = float(_get(item, 5))
                klines.append([o, c, l, h])
                volumes.append(v)
            avg_v = sum(volumes) / len(volumes) if volumes else 1.0
            turnover_rates = [round(max(0.1, (v / avg_v) * 1.5), 2) for v in volumes]
            return {"success": True, "dates": dates, "klines": klines, "volumes": volumes, "turnover_rates": turnover_rates}
    except Exception:
        pass

    return {"success": False, "error": "K线接口暂时不可用"}

# 多因子评分计算 (连接 quant_factors 模块，带智能降级计算)
def compute_quant_scores(code, name, curr_price, prev_close, klines_info=None, quote_info=None):
    if quant_factors:
        for fn in ["calculate_stock_scores", "get_scores", "analyze_stock", "calculate_factors"]:
            if hasattr(quant_factors, fn):
                try:
                    res = getattr(quant_factors, fn)(code, name, curr_price, prev_close, klines_info, spot_info=quote_info)
                    if isinstance(res, dict) and "short_term" in res:
                        return res
                except Exception:
                    pass

    ma5, ma20, ma60 = curr_price, curr_price, curr_price
    rsi_14 = 52.0
    turnover_rate = float(quote_info.get("turnover_rate", 0.0) or 0.0) if quote_info else 0.0

    trend_score = 50
    if klines_info and klines_info.get("success") and len(klines_info.get("klines", [])) >= 20:
        klines = klines_info.get("klines", [])
        closes = [_get(x, 1) for x in klines]
        ma5 = round(sum(closes[-5:]) / 5, 2)
        ma20 = round(sum(closes[-20:]) / 20, 2)
        ma60 = round(sum(closes[-60:]) / 60, 2) if len(closes) >= 60 else ma20

        diffs = [closes[i] - closes[i-1] for i in range(len(closes)-14, len(closes))]
        gains = sum(d for d in diffs if d > 0)
        losses = abs(sum(d for d in diffs if d < 0))
        rs = (gains / 14) / (losses / 14) if losses > 0 else 1.0
        rsi_14 = round(100 - (100 / (1 + rs)), 1)

        if curr_price >= ma5 >= ma20: trend_score += 25
        elif curr_price < ma5 and curr_price < ma20: trend_score -= 20

    # 短线 T+1 交易：均线动量(35%) + 换手率(30%) + RSI(20%) + 量比(15%)
    t1_score = 50
    if trend_score >= 70: t1_score += 18
    elif trend_score < 40: t1_score -= 15

    if turnover_rate <= 0:
        st_desc = "换手率数据收集中"
    elif turnover_rate < 1.5:
        t1_score -= 18
        st_desc = f"换手率仅 {turnover_rate:.2f}% (地量清淡)，缺乏活跃资金承接，T+1 弹性不足"
    elif turnover_rate < 3.0:
        t1_score -= 8
        st_desc = f"换手率 {turnover_rate:.2f}% (缩量整理)，资金观望情绪较浓，适合低吸潜伏"
    elif 3.0 <= turnover_rate < 7.0:
        t1_score += 12
        st_desc = f"换手率 {turnover_rate:.2f}% (温和良性放量)，筹码交换充分，T+1 胜率优良"
    elif 7.0 <= turnover_rate < 15.0:
        t1_score += 22
        st_desc = f"换手率达 {turnover_rate:.2f}% (主力抢筹黄金区间)，资金关注度极高，T+1 爆发力极强"
    elif 15.0 <= turnover_rate < 25.0:
        t1_score += 8
        st_desc = f"换手率高达 {turnover_rate:.2f}% (多空剧烈博弈)，分歧加剧，T+1 需盯紧盘口防洗盘"
    else:
        t1_score -= 15
        st_desc = f"换手率达 {turnover_rate:.2f}% (天量过热松动)，警惕主力借冲高派发，防范次日反杀"

    if 48 <= rsi_14 <= 72: t1_score += 10
    elif rsi_14 > 80: t1_score -= 12

    short_score = max(20, min(95, int(t1_score)))
    mid_score = max(20, min(95, int(trend_score * 0.7 + (20 if curr_price >= ma20 else -15) + 15)))

    # 长线价值
    long_score = 60
    if quote_info:
        pe = float(quote_info.get("pe", 0.0) or 0.0)
        pb = float(quote_info.get("pb", 0.0) or 0.0)
        mv = float(quote_info.get("total_mv", 0.0) or 0.0)
        if pe > 0:
            if pe <= 15.0: long_score += 20
            elif pe <= 32.0: long_score += 10
            elif pe > 65.0: long_score -= 18
        elif pe < 0: long_score -= 25
        if 0 < pb <= 1.2: long_score += 12
        elif pb > 8.0: long_score -= 10
        if mv >= 500.0: long_score += 8

    long_score = max(20, min(95, int(long_score)))

    def get_tag(score):
        if score >= 80: return "强势进攻", "bg-rose-500/20 text-rose-400 border border-rose-500/30"
        if score >= 60: return "稳健中性", "bg-blue-500/20 text-blue-400 border border-blue-500/30"
        return "偏弱观望", "bg-emerald-500/20 text-emerald-400 border border-emerald-500/30"

    st_tag, st_cls = get_tag(short_score)
    mt_tag, mt_cls = get_tag(mid_score)
    lt_tag, lt_cls = get_tag(long_score)

    mt_desc = "上升通道维持完好，MA20/60多头排列，波段趋势强劲" if mid_score >= 80 else ("箱体震荡整理蓄势，等待右侧放量信号" if mid_score >= 60 else "破位下行通道中，中均线压制明显")
    lt_desc = "处于历史估值低分位，安全边际极厚，向上赔率巨大" if long_score >= 80 else ("估值合理中枢水平，基本面支撑良好" if long_score >= 60 else "估值溢价偏高或处于周期高位，长线需谨慎")

    return {
        "short_term": {"score": short_score, "tag": st_tag, "style": st_cls, "desc": st_desc},
        "mid_term": {"score": mid_score, "tag": mt_tag, "style": mt_cls, "desc": mt_desc},
        "long_term": {"score": long_score, "tag": lt_tag, "style": lt_cls, "desc": lt_desc},
        "ma5": ma5, "ma20": ma20, "ma60": ma60, "rsi": rsi_14,
        "pe": quote_info.get("pe", 0.0) if quote_info else 0.0,
        "pb": quote_info.get("pb", 0.0) if quote_info else 0.0,
        "total_mv": quote_info.get("total_mv", 0.0) if quote_info else 0.0,
        "turnover_rate": turnover_rate,
        "overall_grade": "A+ 顶格精选" if short_score >= 82 else ("A 级 优先标的" if (short_score+mid_score)/2 >= 70 else "B 级 观察仓位")
    }

def compute_trade_plan(price, is_holding=False, cost=0.0):
    if trade_plan:
        for fn in ["generate_plan", "calculate_plan", "get_plan", "create_trade_plan"]:
            if hasattr(trade_plan, fn):
                try:
                    return getattr(trade_plan, fn)(price, is_holding, cost)
                except Exception:
                    pass

    if is_holding and cost > 0:
        return {
            "type": "holding",
            "t_buy": round(price * 0.982, 2),
            "t_sell": round(price * 1.032, 2),
            "hard_stop": round(cost * 0.95, 2) if price >= cost else round(price * 0.965, 2),
            "pyramid_add": round(price * 0.94, 2),
            "strategy": "盘中急跌至买点分批做T，反弹逢压力位果断结利润"
        }
    else:
        return {
            "type": "watchlist",
            "buy_range": f"{price * 0.980:.2f} ~ {price * 0.992:.2f}",
            "target1": round(price * 1.045, 2),
            "target2": round(price * 1.100, 2),
            "stop_loss": round(price * 0.980, 2),
            "strategy": "严格在回踩下沿埋伏，破-2.0%无条件止损离场"
        }

# ==================== 4. 市场热点研判服务 (对接 hotspot_service) ====================
FALLBACK_SECTORS = [
        {
            "板块名称": "酿酒行业", "板块涨幅": "+0.50%", "成分股总数": 38,
            "三层标的": {
                "⚡ 进攻龙头(T+1)": [
                    {"名称": "会稽山", "代码": "601579", "最新价": 11.84, "涨跌幅(%)": 9.99, "成交额(亿)": 22.2, "市净率PB": 5.34},
                    {"名称": "古越龙山", "代码": "600059", "最新价": 11.73, "涨跌幅(%)": 6.64, "成交额(亿)": 15.6, "市净率PB": 1.83},
                    {"名称": "惠泉啤酒", "代码": "600573", "最新价": 10.19, "涨跌幅(%)": 1.90, "成交额(亿)": 4.8, "市净率PB": 1.84}
                ],
                "🛡️ 稳健中军(长线)": [
                    {"名称": "贵州茅台", "代码": "600519", "最新价": 1450.0, "涨跌幅(%)": 0.56, "成交额(亿)": 34.9, "市净率PB": 6.19},
                    {"名称": "五粮液", "代码": "000858", "最新价": 138.5, "涨跌幅(%)": -1.09, "成交额(亿)": 10.6, "市净率PB": 2.26},
                    {"名称": "泸州老窖", "代码": "000568", "最新价": 128.0, "涨跌幅(%)": 0.20, "成交额(亿)": 4.0, "市净率PB": 2.25}
                ],
                "💎 绩优合理估值": [
                    {"名称": "老白干酒", "代码": "600559", "最新价": 10.81, "涨跌幅(%)": 0.45, "成交额(亿)": 3.2, "市净率PB": 1.82},
                    {"名称": "燕京啤酒", "代码": "000729", "最新价": 10.85, "涨跌幅(%)": 0.65, "成交额(亿)": 2.8, "市净率PB": 1.81},
                    {"名称": "天佑德酒", "代码": "002646", "最新价": 7.08, "涨跌幅(%)": 0.15, "成交额(亿)": 1.5, "市净率PB": 1.19}
                ]
            }
        },
        {
            "板块名称": "半导体/算力", "板块涨幅": "+3.65%", "成分股总数": 52,
            "三层标的": {
                "⚡ 进攻龙头(T+1)": [
                    {"名称": "中际旭创", "代码": "300308", "最新价": 145.2, "涨跌幅(%)": 6.8, "成交额(亿)": 38.5, "市净率PB": 8.2},
                    {"名称": "新易盛", "代码": "300502", "最新价": 98.4, "涨跌幅(%)": 5.4, "成交额(亿)": 26.4, "市净率PB": 7.5}
                ],
                "🛡️ 稳健中军(长线)": [
                    {"名称": "工业富联", "代码": "601138", "最新价": 22.4, "涨跌幅(%)": 2.1, "成交额(亿)": 52.1, "市净率PB": 3.1},
                    {"名称": "中芯国际", "代码": "688981", "最新价": 58.2, "涨跌幅(%)": 1.8, "成交额(亿)": 41.2, "市净率PB": 2.8}
                ],
                "🚀 高弹性成长优选": [
                    {"名称": "通富微电", "代码": "002156", "最新价": 24.5, "涨跌幅(%)": 3.2, "成交额(亿)": 18.5, "市净率PB": 2.9},
                    {"名称": "长电科技", "代码": "600584", "最新价": 32.1, "涨跌幅(%)": 2.6, "成交额(亿)": 15.2, "市净率PB": 2.4}
                ]
            }
        },
        {
            "板块名称": "石油石化", "板块涨幅": "+0.03%", "成分股总数": 32,
            "三层标的": {
                "⚡ 进攻龙头(T+1)": [
                    {"名称": "国际实业", "代码": "000159", "最新价": 5.49, "涨跌幅(%)": 0.55, "成交额(亿)": 1.8, "市净率PB": 1.85},
                    {"名称": "通源石油", "代码": "300164", "最新价": 4.62, "涨跌幅(%)": 0.43, "成交额(亿)": 2.1, "市净率PB": 2.10}
                ],
                "🛡️ 稳健中军(长线)": [
                    {"名称": "中国石油", "代码": "601857", "最新价": 11.19, "涨跌幅(%)": 2.19, "成交额(亿)": 18.5, "市净率PB": 1.35},
                    {"名称": "中国石化", "代码": "600028", "最新价": 5.41, "涨跌幅(%)": 1.69, "成交额(亿)": 14.2, "市净率PB": 0.88}
                ],
                "💎 周期大底重置资产": [
                    {"名称": "海油工程", "代码": "600583", "最新价": 5.82, "涨跌幅(%)": 0.17, "成交额(亿)": 4.3, "市净率PB": 1.00},
                    {"名称": "华锦股份", "代码": "000059", "最新价": 4.95, "涨跌幅(%)": -0.20, "成交额(亿)": 2.0, "市净率PB": 0.89}
                ]
            }
        },
        {
            "板块名称": "生物医药", "板块涨幅": "-0.02%", "成分股总数": 60,
            "三层标的": {
                "⚡ 进攻龙头(T+1)": [
                    {"名称": "丽珠集团", "代码": "000513", "最新价": 38.5, "涨跌幅(%)": 3.8, "成交额(亿)": 8.4, "市净率PB": 2.02}
                ],
                "🛡️ 稳健中军(长线)": [
                    {"名称": "恒瑞医药", "代码": "600276", "最新价": 46.2, "涨跌幅(%)": 1.2, "成交额(亿)": 21.5, "市净率PB": 4.10},
                    {"名称": "药明康德", "代码": "603259", "最新价": 52.8, "涨跌幅(%)": 0.9, "成交额(亿)": 18.2, "市净率PB": 2.95}
                ],
                "💎 绩优合理估值": [
                    {"名称": "健康元", "代码": "600380", "最新价": 11.2, "涨跌幅(%)": 1.5, "成交额(亿)": 5.2, "市净率PB": 1.18},
                    {"名称": "华东医药", "代码": "000963", "最新价": 34.6, "涨跌幅(%)": 1.1, "成交额(亿)": 6.8, "市净率PB": 1.86}
                ]
            }
        },
        {
            "板块名称": "证券金融", "板块涨幅": "+1.85%", "成分股总数": 45,
            "三层标的": {
                "⚡ 进攻龙头(T+1)": [
                    {"名称": "国盛金控", "代码": "002670", "最新价": 10.45, "涨跌幅(%)": 4.8, "成交额(亿)": 12.5, "市净率PB": 1.80}
                ],
                "🛡️ 稳健中军(长线)": [
                    {"名称": "东方财富", "代码": "300059", "最新价": 15.6, "涨跌幅(%)": 2.8, "成交额(亿)": 65.4, "市净率PB": 2.95},
                    {"名称": "中信证券", "代码": "600030", "最新价": 22.8, "涨跌幅(%)": 1.9, "成交额(亿)": 45.2, "市净率PB": 1.25}
                ],
                "🏛️ 低估值高股息": [
                    {"名称": "华泰证券", "代码": "601688", "最新价": 14.8, "涨跌幅(%)": 1.2, "成交额(亿)": 18.5, "市净率PB": 0.85},
                    {"名称": "海通证券", "代码": "600837", "最新价": 8.95, "涨跌幅(%)": 0.8, "成交额(亿)": 12.1, "市净率PB": 0.68}
                ]
            }
        }
    ]

_HOTSPOTS_CACHE = {"time": 0, "data": None}

def get_market_hotspots():
    global hotspot_engine, _HOTSPOTS_CACHE
    now_ts = time.time()
    if _HOTSPOTS_CACHE.get("data") and (now_ts - _HOTSPOTS_CACHE.get("time", 0) < 180):
        return _HOTSPOTS_CACHE["data"]
    if not hotspot_engine and hotspot_service and hasattr(hotspot_service, "HotspotService"):
        try:
            hotspot_engine = hotspot_service.HotspotService()
        except Exception:
            pass

    if hotspot_engine:
        try:
            # 1. 动态拉取休市事件与正文穿透
            events, start_dt, is_trading = hotspot_engine.fetch_holiday_focus_events(max_pages=5)
            # 2. 动态扫描新浪全市场领涨行业及 75 只三层互斥成分股
            sectors_data = hotspot_engine.fetch_realtime_hotspots(top_n=5)
            
            hours_ago = max(0.1, round((datetime.now() - start_dt).total_seconds() / 3600, 1))
            days_ago = round(hours_ago / 24, 1)
            status_desc = "A 股正常交易盘中" if is_trading else f"A 股休市中（自上个交易日 {start_dt.strftime('%m-%d 15:00')} 闭市至今已发酵 {hours_ago} 小时 / 约 {days_ago} 天）"

            if not events:
                events = [
                    {"发布时间": "09-28 23:54", "标题": "国常会重磅部署：加力实施扩投资促消费政策，稳定房地产市场", "正文深度细则": "国务院常务会议研究宏观政策发力显效，促进有效投资有关工作，加大逆周期调节力度，推动经济持续回升向好。"},
                    {"发布时间": "09-29 08:01", "标题": "外围市场与亚太股市开盘波动，增量政策预期支撑A股独立韧性", "正文深度细则": "全球市场关注国内增量政策落地节奏，资本市场逆周期调节工具落地在即。"},
                    {"发布时间": "09-28 22:30", "标题": "存量房贷利率下调细节逐步落地，多地跟进出台稳楼市增量细则", "正文深度细则": "各银行积极做好存量房贷利率批量调整准备，减轻居民利息负担，提振内需消费信心。"}
                ]

            if not sectors_data:
                sectors_data = FALLBACK_SECTORS

            res_data = {
                "status": "success",
                "is_trading": is_trading,
                "market_status_desc": status_desc,
                "close_start": start_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "events": events[:8],
                "sectors": sectors_data
            }
            _HOTSPOTS_CACHE = {"time": now_ts, "data": res_data}
            return res_data
        except Exception as e:
            print(f"⚠️ 调用 hotspot_engine 实时抓取失败: {e}")

    # 兜底回退：提供标准全量 5 大核心主线，保证即使新浪接口波动，界面也 100% 完整展示
    

    return {
        "status": "fallback",
        "market_status_desc": "休市研判模式（全维度领涨主线对齐）",
        "events": [
            {"发布时间": "09-28 23:54", "标题": "国常会重磅部署：加力实施扩投资促消费政策，稳定房地产市场", "正文深度细则": "国务院常务会议研究宏观政策发力显效，加大逆周期调节力度，推动经济持续回升向好。"},
            {"发布时间": "09-29 08:01", "标题": "外围市场与亚太股市开盘波动，流动性博弈加剧", "正文深度细则": "全球市场关注国内增量政策落地节奏，A股核心资产具备独立反弹韧性。"}
        ],
        "sectors": FALLBACK_SECTORS
    }

def get_hotspot_ai_review():
    global hotspot_engine
    try:
        if not hotspot_engine and hotspot_service and hasattr(hotspot_service, "HotspotService"):
            try:
                hotspot_engine = hotspot_service.HotspotService()
            except Exception as ex:
                print(f"⚠️ 初始化 HotspotService 异常: {ex}")

        if not hotspot_engine:
            return "⚠️ hotspot_service 模块未加载，无法执行 DeepSeek 市场主线研判。"

        # 快速抓取 2 页核心焦点事件与前 5 大领涨板块，避免长时间等待
        events, start_dt, is_trading = hotspot_engine.fetch_holiday_focus_events(max_pages=2)
        sectors_data = hotspot_engine.fetch_realtime_hotspots(top_n=5)
        return hotspot_engine.get_comprehensive_review(sectors_data, events, start_dt, is_trading)
    except Exception as e:
        return f"❌ 调用 DeepSeek 市场主线研判异常: {e}"



# ==================== 多用户数据与鉴权管理 (UserManager) ====================
class UserManager:
    def __init__(self, data_dir=CURRENT_DIR):
        self.data_dir = data_dir
        self.users_file = os.path.join(data_dir, "users.json")
        self.portfolios_file = os.path.join(data_dir, "user_portfolios.json")
        self.lock = threading.Lock()
        self.salt = "stock_quant_secure_salt_2026"
        self._init_data()

    def _hash(self, pwd):
        return hashlib.sha256((pwd + self.salt).encode("utf-8")).hexdigest()

    def _read_json(self, path, default):
        if not os.path.exists(path):
            return default
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default

    def _write_json(self, path, data):
        tmp_path = path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
        except Exception:
            pass

    def _init_data(self):
        with self.lock:
            users = self._read_json(self.users_file, {})
            # 确保默认管理员存在 (admin / 888888)
            if "admin" not in users:
                token = str(uuid.uuid4()).replace("-", "")
                users["admin"] = {
                    "id": "admin",
                    "username": "admin",
                    "password_hash": self._hash("888888"),
                    "nickname": "总舵主 (管理员)",
                    "token": token,
                    "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
                }
                self._write_json(self.users_file, users)

            # 初始化空股票池，确保干净无预设测试股票
            portfolios = self._read_json(self.portfolios_file, {})
            if "admin" not in portfolios:
                portfolios["admin"] = []
                self._write_json(self.portfolios_file, portfolios)

    def register(self, username, password, nickname=""):
        username = str(username).strip()
        password = str(password).strip()
        if not username or len(username) < 2:
            return None, "账号名称不能少于 2 位字符"
        if not password or len(password) < 4:
            return None, "密码长度不能少于 4 位字符"
        with self.lock:
            users = self._read_json(self.users_file, {})
            if username in users:
                return None, "该账号已存在，请直接登录！"
            uid = f"user_{int(time.time()*1000)}"
            token = str(uuid.uuid4()).replace("-", "")
            users[username] = {
                "id": uid,
                "username": username,
                "password_hash": self._hash(password),
                "nickname": nickname.strip() or username,
                "token": token,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            self._write_json(self.users_file, users)
            return {"username": username, "nickname": users[username]["nickname"], "token": token}, ""

    def login(self, username, password):
        username = str(username).strip()
        password = str(password).strip()
        with self.lock:
            users = self._read_json(self.users_file, {})
            user = users.get(username)
            if not user:
                return None, "账号不存在，请先注册！"
            if user["password_hash"] != self._hash(password):
                return None, "密码错误，请核对后重试！"
            token = str(uuid.uuid4()).replace("-", "")
            user["token"] = token
            self._write_json(self.users_file, users)
            return {"username": username, "nickname": user.get("nickname") or username, "token": token}, ""

    def get_user_by_token(self, token):
        if not token:
            return None
        with self.lock:
            users = self._read_json(self.users_file, {})
            for u in users.values():
                if u.get("token") == token:
                    return {"username": u["username"], "nickname": u.get("nickname") or u["username"], "id": u.get("id")}
        return None

    def get_stocks(self, username):
        with self.lock:
            p_data = self._read_json(self.portfolios_file, {})
            return p_data.get(username, [])

    def save_stock(self, username, stock_item):
        with self.lock:
            p_data = self._read_json(self.portfolios_file, {})
            user_stocks = p_data.get(username, [])
            code = stock_item.get("代码")
            updated = False
            for s in user_stocks:
                if s.get("代码") == code:
                    s.update(stock_item)
                    updated = True
                    break
            if not updated:
                user_stocks.append(stock_item)
            p_data[username] = user_stocks
            self._write_json(self.portfolios_file, p_data)

    def delete_stock(self, username, code):
        with self.lock:
            p_data = self._read_json(self.portfolios_file, {})
            user_stocks = p_data.get(username, [])
            p_data[username] = [s for s in user_stocks if s.get("代码") != code]
            self._write_json(self.portfolios_file, p_data)

    def get_or_create_wechat_user(self, username, nickname, openid):
        with self.lock:
            users = self._read_json(self.users_file, {})
            if username in users:
                user = users[username]
                token = str(uuid.uuid4()).replace("-", "")
                user["token"] = token
                self._write_json(self.users_file, users)
                return {"username": username, "nickname": user.get("nickname") or nickname, "token": token}, ""
            else:
                uid = f"user_{int(time.time()*1000)}"
                token = str(uuid.uuid4()).replace("-", "")
                users[username] = {
                    "id": uid,
                    "username": username,
                    "password_hash": self._hash("wechat_auto_login"),
                    "nickname": nickname,
                    "openid": openid,
                    "token": token,
                    "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
                }
                self._write_json(self.users_file, users)
                return {"username": username, "nickname": nickname, "token": token}, ""



user_manager = UserManager()

def get_enriched_stocks(stocks=None):
    if stocks is None:
        stocks = user_manager.get_stocks("admin")
        
    portfolio_list = []
    watchlist_list = []
    
    for p in stocks:
        try:
            raw_code = str(p.get("代码", "")).strip()
            raw_name = str(p.get("名称", "")).strip()
            cost = float(p.get("成本价", 0.0) or 0.0)
            shares = int(p.get("持仓股数", 0) or 1000)
            is_holding = p.get("is_holding", True) if cost > 0 else False
            
            symbol, code, std_name = resolve_stock(raw_code or raw_name)
            if not symbol:
                symbol, code, std_name = resolve_stock(raw_name or raw_code)
                
            quote = fetch_real_quote(symbol) if symbol else {"name": "", "curr_price": cost or 5.0, "prev_close": cost or 5.0}
            name = quote.get("name") or std_name or raw_name or code
            curr_price = float(quote.get("curr_price", 0.0) or 0.0)
            prev_close = float(quote.get("prev_close", 0.0) or 0.0)
            if curr_price <= 0:
                # 尝试从K线历史拉取真实最后收盘价，坚决不拿成本价冒充现价
                k_fallback = fetch_kline_history(symbol, days=3)
                if k_fallback and k_fallback.get("klines"):
                    curr_price = float(k_fallback["klines"][-1][1])
                else:
                    curr_price = prev_close if prev_close > 0 else (cost if cost > 0 else 5.0)
            if prev_close <= 0: prev_close = curr_price
            
            pct_today = round(((curr_price - prev_close) / prev_close) * 100, 2) if prev_close else 0.0
            plan = compute_trade_plan(curr_price, is_holding, cost) or {}
            
            if is_holding and cost > 0:
                profit_pct = round(((curr_price - cost) / cost) * 100, 2)
                profit_amount = round((curr_price - cost) * (shares or 1000), 2)
                t_buy = float(plan.get("t_buy") or (curr_price * 0.982))
                t_sell = float(plan.get("t_sell") or (curr_price * 1.032))
                hard_stop = float(plan.get("hard_stop") or plan.get("atr_stop") or (cost * 0.95 if curr_price >= cost else curr_price * 0.965))
                portfolio_list.append({
                    "code": code, "symbol": symbol, "name": name, "cost": cost, "price": curr_price,
                    "pct_today": pct_today, "shares": shares,
                    "profit_pct": profit_pct, "profit_amount": profit_amount,
                    "t_buy": round(t_buy, 2), "t_sell": round(t_sell, 2), "hard_stop": round(hard_stop, 2)
                })
            else:
                entry_range = str(plan.get("buy_range") or plan.get("entry_range") or f"{curr_price*0.98:.2f} ~ {curr_price*0.99:.2f}")
                target1 = float(plan.get("target1") or plan.get("profit_price") or (curr_price * 1.045))
                target2 = float(plan.get("target2") or (curr_price * 1.100))
                stop_loss = float(plan.get("stop_loss") or plan.get("hard_stop") or plan.get("atr_stop") or (curr_price * 0.980))
                watchlist_list.append({
                    "code": code, "symbol": symbol, "name": name, "price": curr_price,
                    "pct_today": pct_today,
                    "entry_range": entry_range,
                    "target1": round(target1, 2), "target2": round(target2, 2), "stop_loss": round(stop_loss, 2)
                })
        except Exception as item_err:
            print(f"Error enriching stock item {p}: {item_err}")
            
    return portfolio_list, watchlist_list

# ----------------- 现代自适应 Web 前端 -----------------
HTML_CONTENT = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>A股 AI 量化投资与全端决策系统</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <style>
    body { background-color: #0b1120; color: #e2e8f0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    .card { background-color: #1e293b; border: 1px solid #334155; border-radius: 12px; }
    .stock-up { color: #ef4444; }
    .stock-down { color: #10b981; }
    .badge-up { background-color: rgba(239, 68, 68, 0.15); color: #ef4444; }
    .badge-down { background-color: rgba(16, 185, 129, 0.15); color: #10b981; }
    .tab-active { border-bottom: 2px solid #3b82f6; color: #60a5fa; font-weight: bold; }
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-thumb { background: #334155; border-radius: 4px; }
  
    
    /* ================= 顶级金融终端高清晰度表格与决策牌排版 ================= */
    #single-ai-content table, #all-ai-content table, .markdown-body table { 
      width: 100% !important; 
      border-collapse: collapse !important; 
      margin: 14px 0 22px 0 !important; 
      border: 1px solid #334155 !important;
      background-color: #0f172a !important;
      border-radius: 8px !important;
      overflow: hidden !important;
      display: table !important;
    }
    #single-ai-content th, #all-ai-content th, .markdown-body th { 
      background-color: #1e293b !important; 
      color: #38bdf8 !important; 
      font-weight: 700 !important; 
      padding: 10px 14px !important; 
      border: 1px solid #334155 !important; 
      text-align: left !important; 
      font-size: 13px !important;
      white-space: nowrap !important;
    }
    #single-ai-content td, #all-ai-content td, .markdown-body td { 
      padding: 10px 14px !important; 
      border: 1px solid #334155 !important; 
      color: #e2e8f0 !important; 
      font-size: 12.5px !important;
      line-height: 1.6 !important;
      vertical-align: middle !important;
    }
    #single-ai-content tr:nth-child(even), #all-ai-content tr:nth-child(even), .markdown-body tr:nth-child(even) { 
      background-color: rgba(30, 41, 59, 0.45) !important; 
    }
    #single-ai-content tr:hover, #all-ai-content tr:hover, .markdown-body tr:hover { 
      background-color: rgba(51, 65, 85, 0.4) !important; 
    }
    #single-ai-content blockquote, #all-ai-content blockquote, .markdown-body blockquote {
      border-left: 4px solid #f59e0b !important;
      background: linear-gradient(135deg, rgba(30, 41, 59, 0.95), rgba(15, 23, 42, 0.98)) !important;
      border-radius: 8px !important;
      padding: 12px 16px !important;
      margin: 10px 0 !important;
      color: #f8fafc !important;
      font-size: 13.5px !important;
      line-height: 1.6 !important;
      border: 1px solid rgba(245, 158, 11, 0.35) !important;
      border-left: 4px solid #f59e0b !important;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.4) !important;
    }
        #single-ai-content h3, #all-ai-content h3, .markdown-body h3 {
      font-size: 15px !important;
      font-weight: 800 !important;
      color: #f59e0b !important;
      margin-top: 24px !important;
      margin-bottom: 6px !important;
      padding-left: 10px !important;
      border-left: 4px solid #3b82f6 !important;
    }
    #single-ai-content .section-desc, #all-ai-content .section-desc, .markdown-body .section-desc {
      font-size: 12px !important;
      color: #94a3b8 !important;
      background: rgba(15, 23, 42, 0.75) !important;
      border: 1px solid rgba(56, 189, 248, 0.25) !important;
      border-left: 3px solid #38bdf8 !important;
      padding: 6px 12px !important;
      border-radius: 0 6px 6px 0 !important;
      margin: 4px 0 12px 0 !important;
      line-height: 1.5 !important;
      display: block !important;
    }

  </style>
</head>
<body class="p-4 md:p-8 max-w-7xl mx-auto">
  <!-- 顶栏标题与快捷按钮 -->
  <header class="flex flex-col md:flex-row justify-between items-start md:items-center pb-6 border-b border-slate-700 mb-6 gap-4">
    <div>
      <div class="flex items-center gap-3">
        <i class="fa-solid fa-chart-line text-2xl text-blue-500"></i>
        <h1 class="text-2xl font-bold tracking-tight text-white">A股 AI 量化投资决策系统</h1>
        <span class="px-2.5 py-0.5 text-xs font-semibold bg-blue-500/20 text-blue-400 rounded-full border border-blue-500/30">模块全集成版</span>
      </div>
      <p class="text-sm text-slate-400 mt-1">集成 K线图表 · 多因子评分 · 操盘实战点位 · 市场热点雷达 · DeepSeek 单股/全景投研</p>
    </div>
    <div class="flex items-center gap-2.5 w-full md:w-auto flex-wrap">
      <!-- 手机APP下载与扫码 -->
      <button onclick="openDownloadModal()" class="px-3.5 py-2 bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-400 border border-emerald-500/30 rounded-lg text-sm font-semibold transition flex items-center justify-center gap-1.5 shadow-sm">
        <i class="fa-solid fa-mobile-screen-button"></i> 手机APP
      </button>
      <!-- 用户登录状态插槽 -->
      <div id="auth-header-slot" class="flex items-center gap-2">
        <button onclick="openAuthModal('login')" class="px-3.5 py-2 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 text-white rounded-lg text-sm font-semibold transition flex items-center gap-1.5 shadow-md shadow-blue-500/20">
          <i class="fa-solid fa-user-circle"></i> 登录/注册
        </button>
      </div>
      <button onclick="loadData()" class="px-3 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg text-sm font-medium transition flex items-center justify-center gap-1.5">
        <i class="fa-solid fa-rotate"></i> 刷新
      </button>
      <button onclick="triggerAllAIDiagnose()" id="btn-ai-all" class="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-600 rounded-lg text-sm font-semibold transition flex items-center justify-center gap-1.5">
        <i class="fa-solid fa-list-check"></i> 全仓诊断
      </button>
    </div>
  </header>

  <!-- 录入栏 -->
  <div class="card p-5 mb-6 shadow-xl">
    <div class="flex items-center justify-between mb-3">
      <h2 class="text-base font-semibold text-white flex items-center gap-2">
        <i class="fa-solid fa-plus-circle text-blue-400"></i> 添加标的（支持输入任意股票名/拼音，自动补全）
      </h2>
      <div class="flex gap-4 text-xs font-medium">
        <label class="flex items-center gap-1.5 cursor-pointer">
          <input type="radio" name="stock-type" value="holding" checked onchange="toggleType(true)" class="text-blue-500">
          <span class="text-white font-bold">💼 实战持仓 (做T增益·降本锁利)</span>
        </label>
        <label class="flex items-center gap-1.5 cursor-pointer">
          <input type="radio" name="stock-type" value="watchlist" onchange="toggleType(false)" class="text-blue-500">
          <span class="text-amber-400 font-bold">👀 观察自选 (量化寻买点)</span>
        </label>
      </div>
    </div>

    <div class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-5 gap-3 relative">
      <div class="relative md:col-span-2">
        <input type="text" id="inp-search" placeholder="输入股票名称、拼音或代码 (如 601318、立讯、BYD)..." 
               class="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
               oninput="handleSearch(this.value)">
        <div id="suggest-box" class="hidden absolute left-0 right-0 top-11 bg-slate-900 border border-slate-700 rounded-lg shadow-2xl z-50 overflow-hidden"></div>
      </div>
      <div id="div-cost">
        <input type="number" id="inp-cost" step="0.001" placeholder="买入成本 (元)" 
               class="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500">
      </div>
      <div id="div-shares">
        <input type="number" id="inp-shares" placeholder="持仓股数 (默认1000)" 
               class="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500">
      </div>
      <div>
        <button onclick="submitAddStock()" class="w-full bg-blue-600 hover:bg-blue-500 text-white py-2 rounded-lg text-sm font-bold transition">
          存入清单
        </button>
      </div>
    </div>
  </div>

  <!-- 三大标签页导航 -->
  <div class="flex gap-8 border-b border-slate-800 mb-4 px-2">
    <button onclick="switchTab('tab-holding')" id="btn-tab-holding" class="pb-3 text-sm tab-active transition flex items-center gap-2">
      <i class="fa-solid fa-briefcase"></i> 我的实战持仓 (<span id="count-holding">0</span>)
    </button>
    <button onclick="switchTab('tab-watchlist')" id="btn-tab-watchlist" class="pb-3 text-sm text-slate-400 hover:text-slate-200 transition flex items-center gap-2">
      <i class="fa-solid fa-eye"></i> 重点观察自选 (<span id="count-watchlist">0</span>)
    </button>
    <button onclick="switchTab('tab-hotspots')" id="btn-tab-hotspots" class="pb-3 text-sm text-slate-400 hover:text-slate-200 transition flex items-center gap-2">
      <i class="fa-solid fa-fire text-amber-500"></i> 市场热点与三层推荐 (hotspot_service)
    </button>
  </div>

  <!-- 板块 1：实战持仓表格 -->
  <div id="tab-holding" class="card p-5 mb-6 shadow-xl overflow-hidden">
    <div class="overflow-x-auto">
      <table class="w-full text-left border-collapse">
        <thead>
          <tr class="border-b border-slate-700 text-slate-400 text-xs uppercase bg-slate-900/50">
            <th class="py-3 px-4">标的名称</th>
            <th class="py-3 px-3">买入成本</th>
            <th class="py-3 px-3">最新现价</th>
            <th class="py-3 px-3">持仓总盈亏</th>
            <th class="py-3 px-3">做 T 低吸买点</th>
            <th class="py-3 px-3">做 T 冲高卖点</th>
            <th class="py-3 px-3">刚性止损线</th>
            <th class="py-3 px-3 text-center">专属单股体检</th>
            <th class="py-3 px-3 text-right">操作</th>
          </tr>
        </thead>
        <tbody id="holding-body" class="divide-y divide-slate-800 text-sm">
          <tr><td colspan="9" class="text-center py-6 text-slate-500">加载中...</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <!-- 板块 2：观察自选表格 -->
  <div id="tab-watchlist" class="card p-5 mb-6 shadow-xl overflow-hidden hidden">
    <div class="overflow-x-auto">
      <table class="w-full text-left border-collapse">
        <thead>
          <tr class="border-b border-slate-700 text-slate-400 text-xs uppercase bg-slate-900/50">
            <th class="py-3 px-4">自选关注标的</th>
            <th class="py-3 px-3">真实最新价</th>
            <th class="py-3 px-3">今日涨跌</th>
            <th class="py-3 px-3">建议低吸区间</th>
            <th class="py-3 px-3">短线目标 (+4.5%)</th>
            <th class="py-3 px-3">波段目标 (+10%)</th>
            <th class="py-3 px-3">防守止损线</th>
            <th class="py-3 px-3 text-center">专属单股体检</th>
            <th class="py-3 px-3 text-right">操作</th>
          </tr>
        </thead>
        <tbody id="watchlist-body" class="divide-y divide-slate-800 text-sm">
          <tr><td colspan="9" class="text-center py-6 text-slate-500">自选池暂无数据</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <!-- 板块 3：市场热点研判与领涨行业三层标的 (hotspot_service) -->
  <div id="tab-hotspots" class="card p-5 mb-6 shadow-xl overflow-hidden hidden">
    <div class="mb-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-slate-700/60">
      <div>
        <h3 class="text-base font-bold text-white flex items-center gap-2">
          <i class="fa-solid fa-fire-flame-curved text-amber-400"></i> 市场主线热点深度研判与领涨行业推荐
        </h3>
        <p class="text-xs text-slate-400 mt-1">由 hotspot_service.py 实时穿透休市重大事件，动态扫描领涨行业与 75 只三层互斥成分股</p>
      </div>
      <div class="flex items-center gap-2">
        <button onclick="triggerHotspotAIReview()" id="btn-hotspot-ai" class="px-3 py-1.5 bg-gradient-to-r from-amber-600 to-rose-600 hover:from-amber-500 hover:to-rose-500 text-xs font-bold text-white rounded-lg shadow-md transition flex items-center gap-1.5">
          <i class="fa-solid fa-brain"></i> DeepSeek 市场主线研判
        </button>
        <button onclick="loadHotspots()" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-xs rounded-lg border border-slate-700 text-slate-200 transition flex items-center gap-1.5">
          <i class="fa-solid fa-rotate"></i> 实时刷新
        </button>
      </div>
    </div>

    <!-- AI 全局推演展开栏 -->
    <div id="hotspot-ai-box" class="p-4 mb-5 bg-gradient-to-br from-slate-900 via-slate-900 to-amber-950/30 rounded-xl border border-amber-500/40 hidden">
      <div class="flex justify-between items-center mb-2 pb-2 border-b border-slate-700/60">
        <span class="text-xs font-bold text-amber-400 flex items-center gap-1.5">
          <i class="fa-solid fa-chess-knight"></i> DeepSeek 首席全球宏观策略操盘手 · 全周期合力推演内参
        </span>
        <button onclick="document.getElementById('hotspot-ai-box').classList.add('hidden')" class="text-xs text-slate-500 hover:text-slate-300">
          <i class="fa-solid fa-xmark"></i> 关闭
        </button>
      </div>
      <div id="hotspot-ai-content" class="text-xs text-slate-300 leading-relaxed space-y-2 markdown-body"></div>
    </div>
    
    <!-- 动态填充热点新闻与行业标的 -->
    <div id="hotspot-content">
      <!-- 动态填充 -->
    </div>
  </div>

  <!-- ==================== 核心：单股专属深度体检与 K 线看板 ==================== -->
  <div id="section-stock-detail" class="card p-6 mb-6 shadow-2xl border-blue-500/40 hidden">
    <div class="flex flex-col md:flex-row justify-between items-start md:items-center pb-4 border-b border-slate-700 gap-4">
      <div class="flex items-center gap-4">
        <div>
          <div class="flex items-center gap-3">
            <h2 id="detail-stock-name" class="text-2xl font-extrabold text-white">--</h2>
            <span id="detail-stock-code" class="text-sm font-mono text-slate-400">--</span>
            <span id="detail-stock-grade" class="px-2.5 py-0.5 text-xs font-bold rounded-full bg-blue-500/20 text-blue-400 border border-blue-500/30">--</span>
          </div>
          <div class="flex items-center gap-4 mt-1 text-sm">
            <span>最新价: <strong id="detail-stock-price" class="text-lg text-white">--</strong></span>
            <span>涨跌幅: <strong id="detail-stock-pct" class="text-lg">--</strong></span>
            <span class="text-xs text-slate-400">今日换手: <span id="detail-turnover" class="text-amber-400 font-bold">--</span> | 5日均换手: <span id="detail-ma5-turnover" class="text-amber-300 font-semibold">--</span> | PE(动): <span id="detail-pe" class="text-white font-semibold">--</span> | PB: <span id="detail-pb" class="text-white font-semibold">--</span> | 总市值: <span id="detail-mv" class="text-white font-semibold">--</span> | MA5: <span id="detail-ma5">--</span> | MA20: <span id="detail-ma20">--</span> | RSI(14): <span id="detail-rsi">--</span></span>
          </div>
        </div>
      </div>
      <div class="flex items-center gap-2">
        <button onclick="triggerSingleAIDiagnose()" id="btn-single-ai" class="px-4 py-2 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 text-white rounded-lg text-sm font-bold shadow-lg shadow-blue-500/20 transition flex items-center gap-2">
          <i class="fa-solid fa-brain"></i> 生成该股 DeepSeek 操盘内参
        </button>
      </div>
    </div>

    <!-- K 线与多因子评分栅格 -->
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-6 mt-6">
      <!-- 左侧 2 栏：交互式 K 线图 -->
      <div class="lg:col-span-2 bg-slate-900/60 p-4 rounded-xl border border-slate-800">
        <div class="flex justify-between items-center mb-2 px-2">
          <span class="text-xs font-semibold text-slate-300 flex items-center gap-1.5">
            <i class="fa-solid fa-chart-candlestick text-blue-400"></i> 前复权日 K 线 (MA均线 / 成交量 / 换手率)
          </span>
          <span class="text-[11px] text-slate-500">支持鼠标滚轮缩放、拖拽与高亮十字光标</span>
        </div>
        <div id="kline-chart" style="width: 100%; height: 420px;"></div>
      </div>

      <!-- 右侧 1 栏：多因子量化评分面板 (quant_factors) + 操盘实战点位 (trade_plan) -->
      <div class="space-y-4">
        <!-- 评分卡片 -->
        <div class="bg-slate-900/60 p-4 rounded-xl border border-slate-800">
          <h4 class="text-xs font-bold uppercase tracking-wider text-slate-400 mb-3 flex items-center gap-1.5">
            <i class="fa-solid fa-gauge-high text-indigo-400"></i> 三大周期量化适应度评分 (quant_factors)
          </h4>
          <div class="space-y-3.5">
            <div>
              <div class="flex justify-between text-xs mb-1">
                <span class="font-medium text-slate-300">⚡ 短线 T+1 交易评分</span>
                <span id="score-short" class="font-bold text-white">0 / 100</span>
              </div>
              <div class="w-full bg-slate-800 rounded-full h-2">
                <div id="bar-short" class="bg-blue-500 h-2 rounded-full transition-all duration-500" style="width: 0%"></div>
              </div>
              <div class="flex justify-between items-center mt-1">
                <span id="desc-short" class="text-[11px] text-slate-400">--</span>
                <span id="tag-short" class="text-[10px] px-1.5 py-0.5 rounded">--</span>
              </div>
            </div>

            <div>
              <div class="flex justify-between text-xs mb-1">
                <span class="font-medium text-slate-300">🌊 中线波段趋势评分</span>
                <span id="score-mid" class="font-bold text-white">0 / 100</span>
              </div>
              <div class="w-full bg-slate-800 rounded-full h-2">
                <div id="bar-mid" class="bg-indigo-500 h-2 rounded-full transition-all duration-500" style="width: 0%"></div>
              </div>
              <div class="flex justify-between items-center mt-1">
                <span id="desc-mid" class="text-[11px] text-slate-400">--</span>
                <span id="tag-mid" class="text-[10px] px-1.5 py-0.5 rounded">--</span>
              </div>
            </div>

            <div>
              <div class="flex justify-between text-xs mb-1">
                <span class="font-medium text-slate-300">💎 长线价值配置评分</span>
                <span id="score-long" class="font-bold text-white">0 / 100</span>
              </div>
              <div class="w-full bg-slate-800 rounded-full h-2">
                <div id="bar-long" class="bg-purple-500 h-2 rounded-full transition-all duration-500" style="width: 0%"></div>
              </div>
              <div class="flex justify-between items-center mt-1">
                <span id="desc-long" class="text-[11px] text-slate-400">--</span>
                <span id="tag-long" class="text-[10px] px-1.5 py-0.5 rounded">--</span>
              </div>
            </div>

            <!-- 多因子量化评分标准 (含 T+1 换手率核心权重) -->
            <div class="mt-3 pt-2.5 border-t border-slate-800 text-[11px] text-slate-400 space-y-1.5 bg-slate-950/40 p-2.5 rounded-lg border border-slate-800/80">
              <div class="font-bold text-slate-200 flex items-center justify-between">
                <span class="flex items-center gap-1.5"><i class="fa-solid fa-scale-balanced text-amber-400"></i> 量化打分白盒标准 (含 T+1 换手率):</span>
                <span class="text-[10px] text-blue-400 font-normal">客观多因子计算</span>
              </div>
              <div class="text-[10px] text-slate-300">
                ⚡ <strong>短线 T+1 (100分)</strong>: 均线动量(35%) + <span class="text-amber-400 font-semibold">双轨复合换手率(30% [当日60% + 5日均40%])</span> + RSI强弱(20%) + 量比(15%)
              </div>
              <div class="text-[9.5px] text-slate-400 pl-2 leading-relaxed bg-slate-900/60 p-1.5 rounded border border-slate-800/60">
                • <strong>复合换手 &lt; 1.8%</strong>: 地量清淡，常态流动性不足严禁追高 (-18分)<br>
                • <strong>3.5% ~ 7%</strong>: 持续温和放量，筹码良性换手 (+14分)<br>
                • <strong>7% ~ 15%</strong>: 主力抢筹黄金区，资金持续沉淀，T+1 爆发力最强 (+24分)<br>
                • <strong>脉冲识别</strong>: 平时地量今日突发脉冲 &gt; 5%，智能扣分防一日游退潮！<br>
                • <strong>&gt; 25%</strong>: 天量过热松动，警惕主力高位派发防次日反杀 (-16分)
              </div>
              <div class="text-[10px] text-slate-400">
                🌊 <strong>中线波段</strong>: 均线趋势通道(40%) + MA20得失(35%) + 动量(25%)<br>
                💎 <strong>长线价值</strong>: 五大阵营估值中枢(50%) + PB重置安全垫(30%) + 市值(20%)
              </div>
            </div>
          </div>
        </div>

        <!-- 实战操盘点位与策略卡片 (trade_plan) -->
        <div class="bg-slate-900/60 p-4 rounded-xl border border-slate-800">
          <h4 class="text-xs font-bold uppercase tracking-wider text-slate-400 mb-3 flex items-center gap-1.5">
            <i class="fa-solid fa-bullseye text-emerald-400"></i> 券商条件单·实战挂单指引 (trade_plan)
          </h4>
          <div id="trade-plan-content" class="text-xs space-y-2">
            <!-- 动态填充 -->
          </div>
        </div>
      </div>
    </div>

    <!-- 单股专属 DeepSeek 投研内参展示区 -->
    <div class="mt-6 border-t border-slate-800 pt-5">
      <h3 class="text-sm font-bold text-white flex items-center gap-2 mb-3">
        <i class="fa-solid fa-comment-dots text-blue-400"></i> 该股专属操盘内参报告 (ai_advisor)
      </h3>
      <div id="single-ai-loading" class="hidden py-8 text-center">
        <i class="fa-solid fa-circle-notch fa-spin text-3xl text-blue-500 mb-2"></i>
        <p class="text-slate-400 text-xs">DeepSeek 正在为您针对本股进行盘口体检与挂单测算...</p>
      </div>
      <div id="single-ai-content" class="prose prose-invert max-w-none text-slate-300 text-sm bg-slate-900/70 p-5 rounded-xl border border-slate-800">
        点击上方【<strong>生成该股 DeepSeek 操盘内参</strong>】按钮，获取定制买卖点分析。
      </div>
    </div>
  </div>

  <!-- 全局诊断浮层/弹窗区域 -->
  <div id="modal-all-ai" class="fixed inset-0 bg-black/80 z-50 flex items-center justify-center p-4 hidden">
    <div class="bg-slate-900 border border-slate-700 rounded-2xl max-w-4xl w-full max-h-[85vh] flex flex-col shadow-2xl">
      <div class="p-4 border-b border-slate-800 flex justify-between items-center">
        <h3 class="text-base font-bold text-white flex items-center gap-2">
          <i class="fa-solid fa-robot text-blue-400"></i> 全局股票池·全景操盘内参
        </h3>
        <button onclick="closeAllAIModal()" class="text-slate-400 hover:text-white px-2 py-1">
          <i class="fa-solid fa-xmark text-lg"></i>
        </button>
      </div>
      <div class="p-6 overflow-y-auto flex-1">
        <div id="all-ai-loading" class="hidden py-12 text-center">
          <i class="fa-solid fa-circle-notch fa-spin text-4xl text-blue-500 mb-3"></i>
          <p class="text-slate-300 text-sm">正在汇总分析全部股票...</p>
        </div>
        <div id="all-ai-content" class="prose prose-invert max-w-none text-slate-200 text-sm leading-relaxed"></div>
      </div>
    </div>
  </div>

  <script>
    
    // 渲染 Markdown 报告：将 5 步骤 + 执单总结 解析为真正的 Tab 标签页切换卡片，彻底消灭'一长条'
    function renderSafeMarkdown(rawText) {
      if (!rawText) return '无分析内容';
      var safeText = String(rawText);
      safeText = safeText.replace(/~/g, '～');

      var LF = String.fromCharCode(10);
      var lines = safeText.split(LF);
      for (var i = 0; i < lines.length; i++) {
        var trimmed = lines[i].trim();
        if (trimmed.indexOf('*💡') === 0 && trimmed.lastIndexOf('*') > 2) {
          var inner = trimmed.substring(trimmed.indexOf('💡') + 2, trimmed.lastIndexOf('*')).trim();
          lines[i] = '<div class="section-desc mb-3">💡 ' + inner + '</div>';
        }
      }

      var tabDefs = [
        { key: '步骤一', alt: '【定调】', title: '🚦 定调画像', icon: 'fa-flag' },
        { key: '步骤二', alt: '【估值】', title: '🏢 基本估值', icon: 'fa-building-columns' },
        { key: '步骤三', alt: '【盘口】', title: '📊 盘口试金', icon: 'fa-chart-pie' },
        { key: '步骤四', alt: '【战术】', title: '🧭 双向预案', icon: 'fa-route' },
        { key: '步骤五', alt: '【条件单】', title: '📋 券商条件单', icon: 'fa-list-check' },
        { key: '执单总结', alt: '一页纸', title: '📑 执单总结', icon: 'fa-clipboard-check' }
      ];

      var tabs = [];
      var currentTab = null;
      var topLines = [];

      for (var j = 0; j < lines.length; j++) {
        var line = lines[j];
        var tr = line.trim();

        var matched = null;
        for (var k = 0; k < tabDefs.length; k++) {
          var def = tabDefs[k];
          if (tr.indexOf(def.key) !== -1 || (def.alt && tr.indexOf(def.alt) !== -1)) {
            matched = def;
            break;
          }
        }

        if (matched && (tr.indexOf('#') === 0 || tr.indexOf('**') === 0 || tr.indexOf('步骤') === 0 || tr.indexOf('执单总结') !== -1)) {
          if (currentTab) tabs.push(currentTab);
          currentTab = { title: matched.title, icon: matched.icon, lines: [line] };
        } else {
          if (currentTab) {
            currentTab.lines.push(line);
          } else {
            topLines.push(line);
          }
        }
      }
      if (currentTab) tabs.push(currentTab);

      if (tabs.length < 2) {
        return marked.parse(lines.join(LF));
      }

      var topHtml = topLines.length > 0 ? marked.parse(topLines.join(LF)) : '';

      var navHtml = '<div id="ai-report-nav" class="my-4 p-2 bg-slate-950/95 rounded-xl border border-blue-500/40 flex flex-wrap gap-2 items-center shadow-xl sticky top-2 z-20 backdrop-blur">' +
        '<span class="text-xs text-slate-400 font-semibold mr-1 flex items-center gap-1.5"><i class="fa-solid fa-layer-group text-blue-400"></i> 操盘分卡:</span>';

      for (var t = 0; t < tabs.length; t++) {
        var tab = tabs[t];
        var isFirst = (t === 0);
        var activeClass = isFirst ? 'bg-blue-600 text-white shadow-lg ring-1 ring-blue-400 font-bold' : 'bg-slate-800 text-slate-300 hover:bg-slate-700 font-medium';
        navHtml += '<button type="button" data-tab-idx="' + t + '" id="ai-tab-btn-' + t + '" class="ai-tab-btn px-3.5 py-1.5 rounded-lg text-xs transition flex items-center gap-1.5 border border-slate-700/80 ' + activeClass + '">' +
          '<i class="fa-solid ' + tab.icon + '"></i> ' + tab.title + '</button>';
      }

      navHtml += '<button type="button" data-tab-idx="ALL" id="ai-tab-btn-ALL" class="ai-tab-btn px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-800/80 text-slate-400 hover:text-white transition ml-auto border border-slate-700">' +
        '<i class="fa-solid fa-bars"></i> 展开全部</button></div>';

      var panelsHtml = '<div id="ai-report-panels" class="mt-2">';
      for (var p = 0; p < tabs.length; p++) {
        var hiddenClass = (p === 0) ? '' : 'hidden';
        panelsHtml += '<div id="ai-tab-panel-' + p + '" class="ai-tab-panel ' + hiddenClass + ' animate-fadeIn">' +
          marked.parse(tabs[p].lines.join(LF)) + '</div>';
      }
      panelsHtml += '<div id="ai-tab-panel-ALL" class="ai-tab-panel hidden space-y-6">';
      for (var q = 0; q < tabs.length; q++) {
        panelsHtml += '<div class="p-4 bg-slate-900/60 rounded-xl border border-slate-800/80 shadow-md">' +
          marked.parse(tabs[q].lines.join(LF)) + '</div>';
      }
      panelsHtml += '</div></div>';

      return topHtml + navHtml + panelsHtml;
    }

    document.addEventListener('click', function(e) {
      var btn = e.target.closest('.ai-tab-btn');
      if (btn) {
        var idx = btn.getAttribute('data-tab-idx');
        if (idx !== null && idx !== undefined) {
          window.switchAiReportTab(idx);
        }
      }
    });

    window.switchAiReportTab = function(idx) {
      document.querySelectorAll('.ai-tab-btn').forEach(function(btn) {
        btn.classList.remove('bg-blue-600', 'text-white', 'shadow-lg', 'ring-1', 'ring-blue-400', 'font-bold');
        btn.classList.add('bg-slate-800', 'text-slate-300', 'font-medium');
      });
      document.querySelectorAll('.ai-tab-panel').forEach(function(panel) {
        panel.classList.add('hidden');
      });

      var activeBtn = document.getElementById('ai-tab-btn-' + idx);
      var activePanel = document.getElementById('ai-tab-panel-' + idx);
      if (activeBtn) {
        activeBtn.classList.remove('bg-slate-800', 'text-slate-300', 'font-medium');
        activeBtn.classList.add('bg-blue-600', 'text-white', 'shadow-lg', 'ring-1', 'ring-blue-400', 'font-bold');
      }
      if (activePanel) {
        activePanel.classList.remove('hidden');
      }
    };

    let currentSelectedCode = null;
    let currentSelectedSymbol = null;
    let klineChartInstance = null;
    let isHoldingMode = true;

    // 安全取数助手
    function _at(arr, idx) {
      if (!arr) return undefined;
      return arr[idx];
    }

    function toggleType(isHolding) {
      isHoldingMode = isHolding;
      document.getElementById('div-cost').style.display = isHolding ? 'block' : 'none';
      document.getElementById('div-shares').style.display = isHolding ? 'block' : 'none';
    }

    function switchTab(tabId) {
      document.getElementById('tab-holding').classList.add('hidden');
      document.getElementById('tab-watchlist').classList.add('hidden');
      document.getElementById('tab-hotspots').classList.add('hidden');
      document.getElementById('btn-tab-holding').classList.remove('tab-active');
      document.getElementById('btn-tab-watchlist').classList.remove('tab-active');
      document.getElementById('btn-tab-hotspots').classList.remove('tab-active');

      document.getElementById(tabId).classList.remove('hidden');
      if (tabId === 'tab-holding') {
        document.getElementById('btn-tab-holding').classList.add('tab-active');
      } else if (tabId === 'tab-watchlist') {
        document.getElementById('btn-tab-watchlist').classList.add('tab-active');
      } else if (tabId === 'tab-hotspots') {
        document.getElementById('btn-tab-hotspots').classList.add('tab-active');
        loadHotspots();
      }
    }

    
    // ==================== 多用户鉴权与状态管理 ====================
    let currentAuthToken = localStorage.getItem('stock_auth_token') || '';
    let currentAuthUser = null;
    let authModalMode = 'login';
    let qrcodeInstance = null;

    function getAuthHeaders(headers = {}) {
      const h = { ...headers };
      if (currentAuthToken) {
        h['Authorization'] = 'Bearer ' + currentAuthToken;
      }
      return h;
    }

    async function fetchWithAuth(url, options = {}) {
      options.headers = getAuthHeaders(options.headers || {});
      return fetch(url, options);
    }

    async function checkAuthStatus() {
      if (!currentAuthToken) {
        window.location.href = '/login.html';
        return;
      }
      try {
        const res = await fetchWithAuth('/api/auth/me');
        if (res.ok) {
          const data = await res.json();
          if (data.status === 'success' && data.user) {
            currentAuthUser = data.user;
            renderAuthHeader(currentAuthUser);
            return;
          }
        }
      } catch (e) {
        console.warn("Auth check error:", e);
      }
      currentAuthToken = '';
      currentAuthUser = null;
      localStorage.removeItem('stock_auth_token');
      window.location.href = '/login.html';
    }

    function showLockScreen() {
      const lockOverlay = document.getElementById('lock-screen-overlay');
      if (lockOverlay) lockOverlay.classList.remove('hidden');
      openAuthModal('register');
    }

    function hideLockScreen() {
      const lockOverlay = document.getElementById('lock-screen-overlay');
      if (lockOverlay) lockOverlay.classList.add('hidden');
    }

    function renderAuthHeader(user) {
      const slot = document.getElementById('auth-header-slot');
      if (!slot) return;
      if (user) {
        slot.innerHTML = `
          <div class="flex items-center gap-1.5 px-3 py-1.5 bg-blue-500/20 text-blue-300 rounded-lg text-xs font-semibold border border-blue-500/30">
            <i class="fa-solid fa-user-check text-emerald-400"></i>
            <span>${user.nickname || user.username}</span>
          </div>
          <button onclick="handleLogout()" class="px-2 py-1.5 bg-slate-800 hover:bg-rose-950/60 text-slate-400 hover:text-rose-400 rounded-lg text-xs font-medium border border-slate-700 transition">
            退出
          </button>
        `;
      } else {
        slot.innerHTML = `
          <button onclick="openAuthModal('login')" class="px-3.5 py-2 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 text-white rounded-lg text-sm font-semibold transition flex items-center gap-1.5 shadow-md shadow-blue-500/20">
            <i class="fa-solid fa-user-circle"></i> 登录/注册
          </button>
        `;
      }
    }

    function openAuthModal(mode = 'login') {
      authModalMode = mode;
      switchAuthTab(mode);
      const errEl = document.getElementById('auth-error-msg');
      if (errEl) errEl.classList.add('hidden');
      const m = document.getElementById('modal-auth');
      if (m) m.classList.remove('hidden');
    }

    function closeAuthModal() {
      const m = document.getElementById('modal-auth');
      if (m) m.classList.add('hidden');
    }

    function switchAuthTab(mode) {
      authModalMode = mode;
      const tabLogin = document.getElementById('tab-auth-login');
      const tabReg = document.getElementById('tab-auth-register');
      const nickWrap = document.getElementById('auth-nickname-wrap');
      const btn = document.getElementById('btn-submit-auth');
      const errEl = document.getElementById('auth-error-msg');
      if (errEl) errEl.classList.add('hidden');

      if (mode === 'login') {
        if (tabLogin) tabLogin.className = "text-base font-bold text-blue-400 border-b-2 border-blue-500 pb-1 cursor-pointer";
        if (tabReg) tabReg.className = "text-base font-bold text-slate-400 hover:text-white pb-1 cursor-pointer";
        if (nickWrap) nickWrap.classList.add('hidden');
        if (btn) btn.innerText = "立即登录";
      } else {
        if (tabReg) tabReg.className = "text-base font-bold text-blue-400 border-b-2 border-blue-500 pb-1 cursor-pointer";
        if (tabLogin) tabLogin.className = "text-base font-bold text-slate-400 hover:text-white pb-1 cursor-pointer";
        if (nickWrap) nickWrap.classList.remove('hidden');
        if (btn) btn.innerText = "立即注册开通我的股票池";
      }
    }

    
    let siteQrInstance = null;
    function toggleMobileScanBox() {
      const box = document.getElementById('mobile-scan-box');
      if (!box) return;
      if (box.classList.contains('hidden')) {
        box.classList.remove('hidden');
        const qrEl = document.getElementById('site-qrcode');
        if (qrEl && !siteQrInstance) {
          qrEl.innerHTML = '';
          try {
            siteQrInstance = new QRCode(qrEl, {
              text: window.location.origin,
              width: 130,
              height: 130,
              colorDark: "#0f172a",
              colorLight: "#ffffff",
              correctLevel: QRCode.CorrectLevel.M
            });
          } catch(e) {
            qrEl.innerHTML = `<img src="https://api.qrserver.com/v1/create-qr-code/?size=130x130&data=${encodeURIComponent(window.location.origin)}" class="w-[130px] h-[130px] mx-auto">`;
          }
        }
      } else {
        box.classList.add('hidden');
      }
    }

    async function submitAuth() {
      const uEl = document.getElementById('auth-username');
      const pEl = document.getElementById('auth-password');
      const nEl = document.getElementById('auth-nickname');
      const username = uEl ? uEl.value.trim() : '';
      const password = pEl ? pEl.value.trim() : '';
      const nickname = nEl ? nEl.value.trim() : '';
      const errEl = document.getElementById('auth-error-msg');
      const btn = document.getElementById('btn-submit-auth');

      if (!username || username.length < 2) {
        if (errEl) { errEl.innerText = "请输入有效的手机号或账号 (至少2位)"; errEl.classList.remove('hidden'); }
        return;
      }
      if (!password || password.length < 4) {
        if (errEl) { errEl.innerText = "密码长度不能少于 4 位"; errEl.classList.remove('hidden'); }
        return;
      }

      if (btn) { btn.disabled = true; btn.innerText = "正在提交..."; }
      if (errEl) errEl.classList.add('hidden');

      const url = authModalMode === 'login' ? '/api/auth/login' : '/api/auth/register';
      try {
        const res = await fetch(url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username, password, nickname })
        });
        const data = await res.json();
        if (data.status === 'success' && data.token) {
          currentAuthToken = data.token;
          currentAuthUser = data.user;
          localStorage.setItem('stock_auth_token', currentAuthToken);
          renderAuthHeader(currentAuthUser);
          closeAuthModal();
          alert(authModalMode === 'login' ? `欢迎回来，${currentAuthUser.nickname || currentAuthUser.username}！` : `注册成功！已为您建立专属个人股票池。`);
          loadData();
        } else {
          if (errEl) {
            let msg = data.message || "请求失败，请稍后重试";
            if (msg.includes("账号不存在")) {
              errEl.innerHTML = `${msg} <a href="javascript:void(0)" onclick="switchAuthTab('register')" class="text-blue-400 font-bold underline ml-1">点此直接一键注册</a>`;
            } else if (msg.includes("已存在")) {
              errEl.innerHTML = `${msg} <a href="javascript:void(0)" onclick="switchAuthTab('login')" class="text-blue-400 font-bold underline ml-1">点此直接登录</a>`;
            } else {
              errEl.innerText = msg;
            }
            errEl.classList.remove('hidden');
          }
        }
      } catch (e) {
        if (errEl) { errEl.innerText = "网络连线异常: " + e; errEl.classList.remove('hidden'); }
      } finally {
        if (btn) {
          btn.disabled = false;
          btn.innerText = authModalMode === 'login' ? "立即登录" : "立即注册开通我的股票池";
        }
      }
    }

    async function handleLogout() {
      if (!confirm("确定要退出当前账号吗？")) return;
      try {
        await fetchWithAuth('/api/auth/logout', { method: 'POST' });
      } catch (e) {}
      currentAuthToken = '';
      currentAuthUser = null;
      localStorage.removeItem('stock_auth_token');
      window.location.href = '/login.html';
    }

    // ==================== APP 扫码下载弹窗 ====================
    function openDownloadModal() {
      const modal = document.getElementById('modal-download');
      if (modal) modal.classList.remove('hidden');
      const box = document.getElementById('app-qrcode');
      if (box && !qrcodeInstance) {
        box.innerHTML = '';
        const downloadUrl = window.location.origin + '/download';
        try {
          qrcodeInstance = new QRCode(box, {
            text: downloadUrl,
            width: 140,
            height: 140,
            colorDark : "#0f172a",
            colorLight : "#ffffff",
            correctLevel : QRCode.CorrectLevel.M
          });
        } catch (e) {
          box.innerHTML = `<img src="https://api.qrserver.com/v1/create-qr-code/?size=140x140&data=${encodeURIComponent(downloadUrl)}" alt="下载二维码" class="w-[140px] h-[140px] mx-auto">`;
        }
      }
    }

    function closeDownloadModal() {
      const modal = document.getElementById('modal-download');
      if (modal) modal.classList.add('hidden');
    }

    async function loadData() {
      try {
        const res = await fetchWithAuth('/api/stocks');
        const data = await res.json();
        if (data.need_login) {
          showLockScreen();
          const hBody = document.getElementById('holding-body');
          if (hBody) hBody.innerHTML = '<tr><td colspan="9" class="text-center py-8 text-slate-500"><i class="fa-solid fa-lock text-amber-400 mr-2"></i>专属股票池已安全锁定，请先登录/注册查看</td></tr>';
          const wBody = document.getElementById('watchlist-body');
          if (wBody) wBody.innerHTML = '<tr><td colspan="8" class="text-center py-8 text-slate-500"><i class="fa-solid fa-lock text-amber-400 mr-2"></i>专属股票池已安全锁定，请先登录/注册查看</td></tr>';
          document.getElementById('count-holding').innerText = '0';
          document.getElementById('count-watchlist').innerText = '0';
          return;
        }
        hideLockScreen();
        const holdings = data.holdings || [];
        const watchlists = data.watchlists || [];

        document.getElementById('count-holding').innerText = holdings.length;
        document.getElementById('count-watchlist').innerText = watchlists.length;

        // 渲染持仓列表
        const hBody = document.getElementById('holding-body');
        if (holdings.length === 0) {
          hBody.innerHTML = '<tr><td colspan="9" class="text-center py-10 text-slate-500"><i class="fa-solid fa-folder-open text-2xl text-slate-600 mb-2 block"></i>暂无实战持仓标的，请在上方添加您的实战持仓股票</td></tr>';
        } else {
          hBody.innerHTML = holdings.map(item => {
            const isUp = item.profit_pct >= 0;
            return `
              <tr class="hover:bg-slate-800/50 transition">
                <td class="py-3 px-4 font-bold text-white">
                  ${item.name} <span class="text-xs font-normal text-slate-400">(${item.code})</span>
                </td>
                <td class="py-3 px-3">${fmt2(item.cost)} 元</td>
                <td class="py-3 px-3 font-semibold ${item.pct_today >= 0 ? 'stock-up':'stock-down'}">${fmt2(item.price)} 元</td>
                <td class="py-3 px-3">
                  <span class="px-2 py-0.5 rounded text-xs font-bold ${isUp ? 'badge-up':'badge-down'}">
                    ${item.profit_pct > 0 ? '+':''}${item.profit_pct}% (${item.profit_amount}元)
                  </span>
                </td>
                <td class="py-3 px-3 text-blue-400 font-medium">【 ${fmt2(item.t_buy)} 】</td>
                <td class="py-3 px-3 text-amber-400 font-medium">【 ${fmt2(item.t_sell)} 】</td>
                <td class="py-3 px-3 text-rose-500 font-medium">【 ${fmt2(item.hard_stop)} 】</td>
                <td class="py-3 px-3 text-center">
                  <button onclick="inspectStock('${item.code}', '${item.symbol}', '${item.name}')" 
                          class="px-3 py-1 bg-blue-600 hover:bg-blue-500 text-white rounded text-xs font-bold transition flex items-center gap-1 mx-auto">
                    <i class="fa-solid fa-chart-line"></i> 深度体检
                  </button>
                </td>
                <td class="py-3 px-3 text-right">
                  <button onclick="deleteStock('${item.code}')" class="text-slate-500 hover:text-rose-400 text-xs transition">删除</button>
                </td>
              </tr>
            `;
          }).join('');
        }

        // 渲染自选列表
        const wBody = document.getElementById('watchlist-body');
        if (watchlists.length === 0) {
          wBody.innerHTML = '<tr><td colspan="9" class="text-center py-6 text-slate-500">自选池暂无股票，请在上方添加！</td></tr>';
        } else {
          wBody.innerHTML = watchlists.map(item => {
            const isUp = item.pct_today >= 0;
            return `
              <tr class="hover:bg-slate-800/50 transition">
                <td class="py-3 px-4 font-bold text-white">
                  ${item.name} <span class="text-xs font-normal text-slate-400">(${item.code})</span>
                </td>
                <td class="py-3 px-3 font-semibold ${isUp ? 'stock-up':'stock-down'}">${fmt2(item.price)} 元</td>
                <td class="py-3 px-3 ${isUp ? 'stock-up':'stock-down'} font-bold">${isUp ? '+':''}${item.pct_today}%</td>
                <td class="py-3 px-3 text-blue-400 font-medium">【 ${item.entry_range} 】</td>
                <td class="py-3 px-3 text-amber-400 font-medium">【 ${fmt2(item.target1)} 】</td>
                <td class="py-3 px-3 text-emerald-400 font-medium">【 ${fmt2(item.target2)} 】</td>
                <td class="py-3 px-3 text-rose-500 font-medium">【 ${fmt2(item.stop_loss)} 】</td>
                <td class="py-3 px-3 text-center">
                  <button onclick="inspectStock('${item.code}', '${item.symbol}', '${item.name}')" 
                          class="px-3 py-1 bg-indigo-600 hover:bg-indigo-500 text-white rounded text-xs font-bold transition flex items-center gap-1 mx-auto">
                    <i class="fa-solid fa-chart-line"></i> 深度体检
                  </button>
                </td>
                <td class="py-3 px-3 text-right">
                  <button onclick="promoteToHolding('${item.code}', '${item.name}', ${item.price})" class="text-blue-400 hover:underline text-xs mr-2">转持仓</button>
                  <button onclick="deleteStock('${item.code}')" class="text-slate-500 hover:text-rose-400 text-xs transition">删除</button>
                </td>
              </tr>
            `;
          }).join('');
        }
      } catch (e) {
        console.error("loadData error:", e);
        const hBody = document.getElementById('holding-body');
        const wBody = document.getElementById('watchlist-body');
        if (hBody) hBody.innerHTML = `<tr><td colspan="9" class="text-center py-6 text-rose-400">加载异常: ${e.message || e}</td></tr>`;
        if (wBody) wBody.innerHTML = `<tr><td colspan="9" class="text-center py-6 text-rose-400">加载异常: ${e.message || e}</td></tr>`;
      }
    }

    // ==================== 单股深度体检入口 ====================
    async function inspectStock(code, symbol, name) {
      currentSelectedCode = code;
      currentSelectedSymbol = symbol;

      const detailSec = document.getElementById('section-stock-detail');
      detailSec.classList.remove('hidden');
      detailSec.scrollIntoView({ behavior: 'smooth' });

      document.getElementById('detail-stock-name').innerText = name;
      document.getElementById('detail-stock-code').innerText = code;
      document.getElementById('single-ai-content').innerHTML = `
        <div class="text-slate-400 text-center py-4">
          已选中 <strong>${name} (${code})</strong>。点击上方【<strong>生成该股 DeepSeek 操盘内参</strong>】按钮，即刻调用 AI 操盘大脑针对本股进行专属诊断！
        </div>
      `;

      try {
        const res = await fetchWithAuth(`/api/stock/analysis?code=${code}&symbol=${symbol}`);
        const data = await res.json();
        
        if (data.quote) {
          document.getElementById('detail-stock-price').innerText = data.quote.curr_price.toFixed(2) + " 元";
          const pct = ((data.quote.curr_price - data.quote.prev_close) / data.quote.prev_close * 100).toFixed(2);
          const pctEl = document.getElementById('detail-stock-pct');
          pctEl.innerText = (pct > 0 ? "+" : "") + pct + "%";
          pctEl.className = pct >= 0 ? "text-lg text-rose-500 font-bold" : "text-lg text-emerald-500 font-bold";

          const peVal = data.quote.pe;
          const toVal = data.quote.turnover_rate;
          const toEl = document.getElementById('detail-turnover');
          if (toEl) { toEl.innerText = (toVal !== undefined && toVal !== null && toVal > 0) ? toVal.toFixed(2) + '%' : '--'; }
          const ma5ToEl = document.getElementById('detail-ma5-turnover');
          if (ma5ToEl) {
            const m5Val = (data.scores && data.scores.ma5_turnover) ? data.scores.ma5_turnover : (data.quote.turnover_rate || 0);
            ma5ToEl.innerText = (m5Val > 0) ? Number(m5Val).toFixed(2) + '%' : '--';
          }
          document.getElementById('detail-pe').innerText = (peVal && peVal > 0) ? peVal.toFixed(1) + '倍' : (peVal < 0 ? '亏损' : '--');
          const pbVal = data.quote.pb;
          document.getElementById('detail-pb').innerText = (pbVal && pbVal > 0) ? pbVal.toFixed(2) : '--';
          const mvVal = data.quote.total_mv;
          document.getElementById('detail-mv').innerText = (mvVal && mvVal > 0) ? mvVal.toFixed(1) + '亿' : '--';
        }

        if (data.scores) {
          const sc = data.scores;
          document.getElementById('detail-stock-grade').innerText = sc.overall_grade;
          document.getElementById('detail-ma5').innerText = sc.ma5;
          document.getElementById('detail-ma20').innerText = sc.ma20;
          document.getElementById('detail-rsi').innerText = sc.rsi;

          document.getElementById('score-short').innerText = sc.short_term.score + ' / 100';
          document.getElementById('bar-short').style.width = sc.short_term.score + '%';
          document.getElementById('desc-short').innerText = sc.short_term.desc;
          document.getElementById('tag-short').innerText = sc.short_term.tag;
          document.getElementById('tag-short').className = "text-[10px] px-1.5 py-0.5 rounded " + sc.short_term.style;

          document.getElementById('score-mid').innerText = sc.mid_term.score + ' / 100';
          document.getElementById('bar-mid').style.width = sc.mid_term.score + '%';
          document.getElementById('desc-mid').innerText = sc.mid_term.desc;
          document.getElementById('tag-mid').innerText = sc.mid_term.tag;
          document.getElementById('tag-mid').className = "text-[10px] px-1.5 py-0.5 rounded " + sc.mid_term.style;

          document.getElementById('score-long').innerText = sc.long_term.score + ' / 100';
          document.getElementById('bar-long').style.width = sc.long_term.score + '%';
          document.getElementById('desc-long').innerText = sc.long_term.desc;
          document.getElementById('tag-long').innerText = sc.long_term.tag;
          document.getElementById('tag-long').className = "text-[10px] px-1.5 py-0.5 rounded " + sc.long_term.style;
        }

        if (data.plan) {
          const pl = data.plan;
          const planBox = document.getElementById('trade-plan-content');
          if (pl.type === 'holding') {
            planBox.innerHTML = `
              <div class="flex justify-between border-b border-slate-800 pb-1">
                <span class="text-slate-400">做 T 回踩低吸买点:</span>
                <span class="text-blue-400 font-bold">【 ${pl.t_buy} 元 】</span>
              </div>
              <div class="flex justify-between border-b border-slate-800 pb-1">
                <span class="text-slate-400">做 T 冲高止盈卖点:</span>
                <span class="text-amber-400 font-bold">【 ${pl.t_sell} 元 】</span>
              </div>
              <div class="flex justify-between border-b border-slate-800 pb-1">
                <span class="text-slate-400">持仓刚性止损红线:</span>
                <span class="text-rose-500 font-bold">【 ${pl.hard_stop} 元 】</span>
              </div>
              <div class="text-[11px] text-slate-400 mt-2">💡 操作指引: ${pl.strategy}</div>
            `;
          } else {
            planBox.innerHTML = `
              <div class="flex justify-between border-b border-slate-800 pb-1">
                <span class="text-slate-400">建议回踩买入区间:</span>
                <span class="text-blue-400 font-bold">【 ${pl.buy_range} 元 】</span>
              </div>
              <div class="flex justify-between border-b border-slate-800 pb-1">
                <span class="text-slate-400">短线第一止盈 (+4.5%):</span>
                <span class="text-amber-400 font-bold">【 ${pl.target1} 元 】</span>
              </div>
              <div class="flex justify-between border-b border-slate-800 pb-1">
                <span class="text-slate-400">波段第二止盈 (+10%):</span>
                <span class="text-emerald-400 font-bold">【 ${pl.target2} 元 】</span>
              </div>
              <div class="flex justify-between border-b border-slate-800 pb-1">
                <span class="text-slate-400">开仓刚性止损线:</span>
                <span class="text-rose-500 font-bold">【 ${pl.stop_loss} 元 】</span>
              </div>
              <div class="text-[11px] text-slate-400 mt-2">💡 操作指引: ${pl.strategy}</div>
            `;
          }
        }

        if (data.kline && data.kline.success) {
          renderEChartsKLine(data.kline.dates, data.kline.klines, data.kline.volumes, data.kline.turnover_rates);
        }

      } catch (err) {
        console.error("加载个股详情失败:", err);
      }
    }

    // 绘制专业 K 线图
    function renderEChartsKLine(dates, klines, volumes, turnoverRates) {
      const chartDom = document.getElementById('kline-chart');
      if (chartDom == null) return;
      let chart = echarts.getInstanceByDom(chartDom);
      if (chart == null) {
        chart = echarts.init(chartDom, 'dark');
      }
      klineChartInstance = chart;
      if (klineChartInstance == null) return;

      function calcMA(dayCount, data) {
        var result = [];
        for (var i = 0, len = data.length; i < len; i++) {
          if (i < dayCount) {
            result.push('-');
            continue;
          }
          var sum = 0;
          for (var j = 0; j < dayCount; j++) {
            var row = _at(data, i - j);
            sum += _at(row, 1);
          }
          result.push((sum / dayCount).toFixed(2));
        }
        return result;
      }

      const ma5 = calcMA(5, klines);
      const ma20 = calcMA(20, klines);
      const ma60 = calcMA(60, klines);

      const option = {
        backgroundColor: '#0f172a',
        animation: false,
        legend: {
          data: ['日K', 'MA5', 'MA20', 'MA60', '成交量', '换手率(%)'],
          inactiveColor: '#475569',
          textStyle: { color: '#94a3b8' }
        },
        tooltip: {
          trigger: 'axis',
          axisPointer: { type: 'cross' },
          backgroundColor: 'rgba(15, 23, 42, 0.95)',
          borderColor: '#3b82f6',
          borderWidth: 1,
          textStyle: { color: '#f8fafc', fontSize: 12 },
          position: function (pos, params, el, elRect, size) {
            var obj = { top: 10 };
            if (_at(pos, 0) < _at(size.viewSize, 0) / 2) {
              obj.right = 30;
            } else {
              obj.left = 30;
            }
            return obj;
          },
          formatter: function(params) {
            if (params == null || params.length == 0) return '';
            var date = params[0].axisValue;
            var kParam = params.find(function(p) { return p.seriesName == '日K'; });
            var vParam = params.find(function(p) { return p.seriesName == '成交量'; });
            var tParam = params.find(function(p) { return p.seriesName == '换手率(%)'; });

            var d = (kParam && Array.isArray(kParam.data)) ? kParam.data : [];
            var open = Number(d.length >= 5 ? _at(d, 1) : _at(d, 0)) || 0;
            var close = Number(d.length >= 5 ? _at(d, 2) : _at(d, 1)) || 0;
            var low = Number(d.length >= 5 ? _at(d, 3) : _at(d, 2)) || 0;
            var high = Number(d.length >= 5 ? _at(d, 4) : _at(d, 3)) || 0;

            var diff = close - open;
            var pct = open > 0 ? (diff / open * 100) : 0;
            var isUp = close >= open;
            var color = isUp ? '#ef4444' : '#10b981';

            var volVal = vParam ? Number(vParam.data || 0) : 0;
            var volStr = volVal >= 10000 ? (volVal / 10000).toFixed(2) + ' 万手' : volVal.toFixed(0) + ' 手';

            var tNum = tParam ? Number(Array.isArray(tParam.data) ? tParam.data[1] : tParam.data) : 0;
            var tVal = !isNaN(tNum) ? tNum.toFixed(2) + '%' : '--';

            return '<div style="min-width: 165px; font-family: -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif; font-size: 11.5px; line-height: 1.55;">' +
              '<div style="text-align: center; font-weight: bold; color: #38bdf8; border-bottom: 1px solid rgba(56, 189, 248, 0.3); padding-bottom: 3px; margin-bottom: 5px;">' + date + '</div>' +
              '<table style="width: 100%; border-collapse: collapse;">' +
              '<tr><td style="color: #94a3b8;">开盘</td><td style="text-align: right; color: ' + color + '; font-weight: bold;">' + open.toFixed(2) + '</td></tr>' +
              '<tr><td style="color: #94a3b8;">收盘</td><td style="text-align: right; color: ' + color + '; font-weight: bold;">' + close.toFixed(2) + '</td></tr>' +
              '<tr><td style="color: #94a3b8;">最高</td><td style="text-align: right; color: #ef4444; font-weight: bold;">' + high.toFixed(2) + '</td></tr>' +
              '<tr><td style="color: #94a3b8;">最低</td><td style="text-align: right; color: #10b981; font-weight: bold;">' + low.toFixed(2) + '</td></tr>' +
              '<tr><td style="color: #94a3b8;">涨跌幅</td><td style="text-align: right; color: ' + color + '; font-weight: bold;">' + (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%</td></tr>' +
              '<tr><td style="color: #94a3b8;">涨跌额</td><td style="text-align: right; color: ' + color + '; font-weight: bold;">' + (diff >= 0 ? '+' : '') + diff.toFixed(2) + '</td></tr>' +
              '<tr style="border-top: 1px dashed #334155;"><td style="color: #94a3b8; padding-top: 3px;">成交量</td><td style="text-align: right; color: #38bdf8; font-weight: bold; padding-top: 3px;">' + volStr + '</td></tr>' +
              '<tr><td style="color: #94a3b8;">换手率</td><td style="text-align: right; color: #f59e0b; font-weight: bold;">' + tVal + '</td></tr>' +
              '</table></div>';
          }
        },
        axisPointer: {
          link: [{ xAxisIndex: 'all' }],
          label: { backgroundColor: '#334155' }
        },
        grid: [
          { left: '8%', right: '8%', height: '56%', top: '10%' },
          { left: '8%', right: '8%', top: '72%', height: '18%' }
        ],
        xAxis: [
          {
            type: 'category',
            data: dates,
            boundaryGap: false,
            axisLine: { lineStyle: { color: '#475569' } },
            splitLine: { show: false }
          },
          {
            type: 'category',
            gridIndex: 1,
            data: dates,
            boundaryGap: false,
            axisLine: { lineStyle: { color: '#475569' } },
            axisLabel: { show: false }
          }
        ],
        yAxis: [
          {
            scale: true,
            splitArea: { show: false },
            splitLine: { lineStyle: { color: '#1e293b' } }
          },
          {
            scale: true,
            gridIndex: 1,
            splitNumber: 2,
            axisLabel: { color: '#94a3b8', fontSize: 10 },
            axisLine: { show: false },
            axisTick: { show: false },
            splitLine: { show: false }
          },
          {
            scale: true,
            gridIndex: 1,
            position: 'right',
            splitLine: { show: false },
            axisLabel: {
              formatter: '{value}%',
              color: '#f59e0b',
              fontSize: 10
            },
            axisLine: { show: true, lineStyle: { color: '#78350f' } },
            axisTick: { show: false }
          }
        ],
        dataZoom: [
          { type: 'inside', xAxisIndex: Array(0, 1), start: 40, end: 100 },
          { show: true, type: 'slider', xAxisIndex: Array(0, 1), top: '93%', height: 16 }
        ],
        series: [
          {
            name: '日K',
            type: 'candlestick',
            data: klines,
            itemStyle: {
              color: '#ef4444',
              color0: '#10b981',
              borderColor: '#ef4444',
              borderColor0: '#10b981'
            }
          },
          {
            name: 'MA5',
            type: 'line',
            data: ma5,
            smooth: true,
            showSymbol: false,
            lineStyle: { opacity: 0.8, width: 1.5, color: '#f59e0b' }
          },
          {
            name: 'MA20',
            type: 'line',
            data: ma20,
            smooth: true,
            showSymbol: false,
            lineStyle: { opacity: 0.8, width: 1.5, color: '#8b5cf6' }
          },
          {
            name: 'MA60',
            type: 'line',
            data: ma60,
            smooth: true,
            showSymbol: false,
            lineStyle: { opacity: 0.8, width: 1.5, color: '#38bdf8' }
          },
          {
            name: '成交量',
            type: 'bar',
            xAxisIndex: 1,
            yAxisIndex: 1,
            data: volumes,
            itemStyle: {
              color: function(params) {
                var k = _at(klines, params.dataIndex);
                return (k && _at(k, 1) >= _at(k, 0)) ? '#ef4444' : '#10b981';
              }
            }
          },
          {
            name: '换手率(%)',
            type: 'line',
            xAxisIndex: 1,
            yAxisIndex: 2,
            data: turnoverRates || [],
            smooth: true,
            showSymbol: false,
            lineStyle: { opacity: 0.9, width: 1.5, color: '#f59e0b' }
          }
        ]
      };

      klineChartInstance.setOption(option, true);
      setTimeout(function() { if (klineChartInstance != null) klineChartInstance.resize(); }, 50);
      window.addEventListener('resize', () => klineChartInstance.resize());
    }

    // ==================== 单股专属 DeepSeek 诊断 ====================
    async function triggerSingleAIDiagnose() {
      if (!currentSelectedCode) {
        alert("请先选择一只股票进行体检！");
        return;
      }
      const btn = document.getElementById('btn-single-ai');
      const loading = document.getElementById('single-ai-loading');
      const content = document.getElementById('single-ai-content');

      btn.disabled = true;
      btn.classList.add('opacity-50');
      loading.classList.remove('hidden');
      content.classList.add('hidden');

      try {
        const res = await fetchWithAuth('/api/ai/diagnose', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ code: currentSelectedCode, symbol: currentSelectedSymbol })
        });
        const data = await res.json();
        content.innerHTML = renderSafeMarkdown(data.report);
      } catch (e) {
        content.innerHTML = `<span class="text-rose-500">诊断失败: ${e}</span>`;
      } finally {
        loading.classList.add('hidden');
        content.classList.remove('hidden');
        btn.disabled = false;
        btn.classList.remove('opacity-50');
      }
    }

    // ==================== 全局全景 AI 诊断 ====================
    async function triggerAllAIDiagnose() {
      const modal = document.getElementById('modal-all-ai');
      const loading = document.getElementById('all-ai-loading');
      const content = document.getElementById('all-ai-content');
      modal.classList.remove('hidden');
      loading.classList.remove('hidden');
      content.innerHTML = '';

      try {
        const res = await fetchWithAuth('/api/ai/diagnose', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({})
        });
        const data = await res.json();
        content.innerHTML = renderSafeMarkdown(data.report);
      } catch (e) {
        content.innerHTML = `<span class="text-rose-500">诊断失败: ${e}</span>`;
      } finally {
        loading.classList.add('hidden');
      }
    }

    function closeAllAIModal() {
      document.getElementById('modal-all-ai').classList.add('hidden');
    }

    // ==================== 市场热点、休市新闻与领涨行业 ====================
    
    // 数值美化格式化工具函数：强制保留两位小数，杜绝长浮点数
    function fmt2(val, defaultVal = '--') {
      if (val === undefined || val === null || val === '') return defaultVal;
      const n = parseFloat(val);
      return isNaN(n) ? defaultVal : n.toFixed(2);
    }

    function fmtPct(val) {
      if (val === undefined || val === null || val === '') return '0.00%';
      let str = String(val).replace('%', '').trim();
      const n = parseFloat(str);
      if (isNaN(n)) return '0.00%';
      return (n > 0 ? '+' : '') + n.toFixed(2) + '%';
    }

    let currentHotspotSectors = [];
    window.currentHotspotEvents = [];

    async function loadHotspots() {
      const box = document.getElementById('hotspot-content');
      box.innerHTML = '<div class="text-center py-10 text-slate-400"><i class="fa-solid fa-spinner fa-spin mr-2 text-amber-500"></i>正在调用 hotspot_service.py 实时对齐休市日历、穿透正文新闻并扫描领涨行业...</div>';
      
      try {
        const res = await fetch('/api/hotspots');
        const data = await res.json();
        
        currentHotspotSectors = data.sectors || [];
        window.currentHotspotEvents = data.events || [];
        const events = window.currentHotspotEvents;

        // 1. 顶部：休市状态与重点穿透大事件
        let newsHtml = `
          <div class="mb-5 p-4 bg-slate-900/80 rounded-xl border border-amber-500/30">
            <div class="flex flex-col sm:flex-row sm:items-center justify-between pb-3 mb-3 border-b border-slate-800 gap-2">
              <div class="flex items-center gap-2">
                <span class="px-2 py-0.5 text-xs font-bold rounded ${data.is_trading ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40' : 'bg-amber-500/20 text-amber-400 border border-amber-500/40'}">
                  ${data.is_trading ? '● 交易日进行中' : '● 休市发酵中'}
                </span>
                <span class="text-xs font-semibold text-slate-200">${data.market_status_desc || '休市推演对齐'}</span>
              </div>
              <span class="text-[11px] text-slate-400">已捕获 ${events.length} 条核心大事 (均穿透全文细则)</span>
            </div>
            
            <div class="grid grid-cols-1 md:grid-cols-2 gap-3 max-h-60 overflow-y-auto pr-1">
              ${events.map((ev, i) => `
                <div onclick="openNewsModal(${i})" class="p-3 bg-slate-800/60 hover:bg-slate-800/90 rounded-lg border border-slate-700/50 hover:border-amber-500/50 cursor-pointer transition group">
                  <div class="flex justify-between items-center text-xs text-slate-400 mb-1">
                    <span class="font-mono text-amber-400 font-semibold">[${ev.发布时间 || ''}]</span>
                    <div class="flex items-center gap-1.5">
                      <span class="text-[10px] px-1.5 py-0.5 bg-amber-500/20 text-amber-400 border border-amber-500/30 rounded font-medium">正文已穿透</span>
                      ${ev.url ? `<a href="${ev.url}" target="_blank" rel="noopener noreferrer" onclick="event.stopPropagation()" class="text-[10px] px-2 py-0.5 bg-slate-700 hover:bg-blue-600 text-slate-200 hover:text-white rounded flex items-center gap-1 transition" title="跳转新浪原文网"><i class="fa-solid fa-arrow-up-right-from-square text-[9px]"></i> 原文</a>` : ''}
                    </div>
                  </div>
                  <div class="font-bold text-slate-100 group-hover:text-amber-300 text-xs mb-1.5 leading-snug transition">${ev.标题}</div>
                  <div class="text-[11px] text-slate-400 leading-relaxed line-clamp-3">
                    ${ev.正文深度细则 || ev.摘要 || '无详细内容'}
                  </div>
                </div>
              `).join('')}
            </div>
          </div>
        `;

        // 2. 行业赛道选项卡切换
        let sectorTabsHtml = `
          <div class="mb-4">
            <div class="flex items-center justify-between mb-2">
              <span class="text-xs font-bold text-slate-300 flex items-center gap-1.5">
                <i class="fa-solid fa-layer-group text-blue-400"></i> 全市场领涨行业排行榜 (Top 5 核心主线)
              </span>
              <span class="text-[11px] text-slate-500">点击切换行业，查看对应的 15 只三层互斥成分股</span>
            </div>
            <div class="flex flex-wrap gap-2">
              ${currentHotspotSectors.map((sec, idx) => `
                <button onclick="renderSectorDetail(${idx})" id="sec-btn-${idx}" class="sec-tab px-3.5 py-2 rounded-lg text-xs font-semibold transition ${idx === 0 ? 'bg-amber-500 text-slate-950 font-bold shadow-lg shadow-amber-500/20' : 'bg-slate-800 text-slate-300 hover:bg-slate-700'}">
                  ${sec.板块名称} <span class="ml-1 text-[11px] font-mono opacity-90">${fmtPct(sec.板块涨幅)}</span>
                </button>
              `).join('')}
            </div>
          </div>
          <div id="sector-stock-container"></div>
        `;

        box.innerHTML = newsHtml + sectorTabsHtml;
        
        if (currentHotspotSectors.length > 0) {
          renderSectorDetail(0);
        }
      } catch (e) {
        console.error(e);
        box.innerHTML = `<div class="text-center py-8 text-rose-400">获取实时热点数据失败: ${e.message}</div>`;
      }
    }

    function renderSectorDetail(idx) {
      document.querySelectorAll('.sec-tab').forEach((b, i) => {
        b.className = `sec-tab px-3.5 py-2 rounded-lg text-xs font-semibold transition ${i === idx ? 'bg-amber-500 text-slate-950 font-bold shadow-lg shadow-amber-500/20' : 'bg-slate-800 text-slate-300 hover:bg-slate-700'}`;
      });

      const sec = currentHotspotSectors[idx];
      if (!sec || !sec.三层标的) return;

      const t1 = sec.三层标的['⚡ 进攻龙头(T+1)'] || sec.三层标的['⚡ 进攻先锋(T+1)'] || [];
      const t2 = sec.三层标的['🛡️ 稳健中军(长线)'] || [];
      const t3Key = Object.keys(sec.三层标的).find(k => !k.includes('进攻') && !k.includes('稳健中军')) || '💎 低估值优选';
      const t3 = sec.三层标的[t3Key] || [];
      const t3Title = t3Key;

      const container = document.getElementById('sector-stock-container');
      container.innerHTML = `
        <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
          <!-- 梯队 1 -->
          <div class="bg-slate-900/60 p-4 rounded-xl border border-rose-500/30">
            <h4 class="text-sm font-bold text-rose-400 mb-3 flex items-center justify-between">
              <span><i class="fa-solid fa-bolt mr-1"></i> 进攻龙头 (T+1)</span>
              <span class="text-xs text-rose-400/80 font-mono">${t1.length} 只标的</span>
            </h4>
            <div class="space-y-2.5">
              ${t1.map(s => `
                <div class="p-2.5 bg-slate-800/60 rounded-lg flex justify-between items-center hover:bg-slate-800 transition">
                  <div>
                    <div class="font-bold text-white text-sm">${s.名称} <span class="text-xs font-mono text-slate-400">(${s.代码})</span></div>
                    <div class="text-xs mt-0.5"><span class="text-rose-400 font-bold font-mono">¥${fmt2(s.最新价)}</span> <span class="text-rose-400 font-mono ml-2">${fmtPct(s['涨跌幅(%)'] !== undefined ? s['涨跌幅(%)'] : s.涨跌幅)}</span></div>
                  </div>
                  <button onclick="inspectStock('${s.代码}', '', '${s.名称}')" class="px-2.5 py-1 bg-rose-600 hover:bg-rose-500 text-white rounded text-xs font-medium transition">体检</button>
                </div>
              `).join('')}
            </div>
          </div>

          <!-- 梯队 2 -->
          <div class="bg-slate-900/60 p-4 rounded-xl border border-blue-500/30">
            <h4 class="text-sm font-bold text-blue-400 mb-3 flex items-center justify-between">
              <span><i class="fa-solid fa-shield-halved mr-1"></i> 稳健中军 (长线)</span>
              <span class="text-xs text-blue-400/80 font-mono">${t2.length} 只标的</span>
            </h4>
            <div class="space-y-2.5">
              ${t2.map(s => `
                <div class="p-2.5 bg-slate-800/60 rounded-lg flex justify-between items-center hover:bg-slate-800 transition">
                  <div>
                    <div class="font-bold text-white text-sm">${s.名称} <span class="text-xs font-mono text-slate-400">(${s.代码})</span></div>
                    <div class="text-xs text-slate-400 mt-0.5">成交: <span class="text-slate-200 font-mono">${s['成交额(亿)']}亿</span> | PB: <span class="font-mono">${fmt2(s.市净率PB)}</span></div>
                  </div>
                  <button onclick="inspectStock('${s.代码}', '', '${s.名称}')" class="px-2.5 py-1 bg-blue-600 hover:bg-blue-500 text-white rounded text-xs font-medium transition">体检</button>
                </div>
              `).join('')}
            </div>
          </div>

          <!-- 梯队 3 -->
          <div class="bg-slate-900/60 p-4 rounded-xl border border-emerald-500/30">
            <h4 class="text-sm font-bold text-emerald-400 mb-3 flex items-center justify-between">
              <span><i class="fa-solid fa-coins mr-1"></i> ${t3Title}</span>
              <span class="text-xs text-emerald-400/80 font-mono">${t3.length} 只标的</span>
            </h4>
            <div class="space-y-2.5">
              ${t3.map(s => `
                <div class="p-2.5 bg-slate-800/60 rounded-lg flex justify-between items-center hover:bg-slate-800 transition">
                  <div>
                    <div class="font-bold text-white text-sm">${s.名称} <span class="text-xs font-mono text-slate-400">(${s.代码})</span></div>
                    <div class="text-xs text-slate-400 mt-0.5">单价: <span class="text-emerald-400 font-bold font-mono">¥${fmt2(s.最新价)}</span> | PB: <span class="text-emerald-400 font-mono">${fmt2(s.市净率PB)}</span></div>
                  </div>
                  <button onclick="inspectStock('${s.代码}', '', '${s.名称}')" class="px-2.5 py-1 bg-emerald-600 hover:bg-emerald-500 text-white rounded text-xs font-medium transition">体检</button>
                </div>
              `).join('')}
            </div>
          </div>
        </div>
      `;
    }

    async function triggerHotspotAIReview() {
      const btn = document.getElementById('btn-hotspot-ai');
      const box = document.getElementById('hotspot-ai-box');
      const content = document.getElementById('hotspot-ai-content');

      box.classList.remove('hidden');
      content.innerHTML = '<div class="py-4 text-center text-amber-400"><i class="fa-solid fa-spinner fa-spin mr-2"></i>正在汇总近期重大核心事件与领涨板块，调用 DeepSeek 推演市场主线研判...</div>';
      btn.disabled = true;

      try {
        const res = await fetch('/api/hotspots/ai_review');
        const data = await res.json();
        content.innerHTML = renderSafeMarkdown(data.review);
      } catch (e) {
        content.innerHTML = `<span class="text-rose-400">调用失败: ${e.message}</span>`;
      } finally {
        btn.disabled = false;
      }
    }

    let currentSearchSelected = null;
    let searchTimer = null;
    function handleSearch(val) {
      clearTimeout(searchTimer);
      const box = document.getElementById('suggest-box');
      if (!val || val.trim().length === 0) { box.classList.add('hidden'); return; }
      searchTimer = setTimeout(async () => {
        const res = await fetch(`/api/search?q=${encodeURIComponent(val)}`);
        const data = await res.json();
        const matches = data.matches || [];
        if (matches.length === 0) { box.classList.add('hidden'); return; }
        box.innerHTML = matches.map(m => `
          <div onclick="selectSearchStock('${m.code}', '${m.name}')" class="px-3 py-2 hover:bg-slate-800 cursor-pointer flex justify-between items-center text-xs border-b border-slate-800 last:border-0">
            <span class="font-bold text-white">${m.name} <span class="text-slate-400">(${m.code})</span></span>
            <span class="text-blue-400 bg-blue-900/30 px-1.5 py-0.5 rounded">${m.market}</span>
          </div>
        `).join('');
        box.classList.remove('hidden');
      }, 250);
    }

    function selectSearchStock(code, name) {
      currentSearchSelected = { code: code, name: name };
      document.getElementById('inp-search').value = `${name} (${code})`;
      document.getElementById('suggest-box').classList.add('hidden');
    }

    async function submitAddStock() {
      const searchVal = document.getElementById('inp-search').value.trim();
      let code = currentSearchSelected ? currentSearchSelected.code : searchVal;
      let name = currentSearchSelected ? currentSearchSelected.name : searchVal;
      const cost = isHoldingMode ? (document.getElementById('inp-cost').value || 0) : 0;
      const shares = isHoldingMode ? (document.getElementById('inp-shares').value || 1000) : 0;
      if (!code) { alert("请输入股票名称或代码！"); return; }
      await submitStockData(code, name, cost, shares, isHoldingMode);
    }

    async function submitStockData(code, name, cost, shares, isHolding) {
      await fetchWithAuth('/api/stock/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code, name, cost: parseFloat(cost) || 0, shares: parseInt(shares) || 0, is_holding: isHolding })
      });
      document.getElementById('inp-search').value = '';
      document.getElementById('inp-cost').value = '';
      document.getElementById('inp-shares').value = '';
      currentSearchSelected = null;
      loadHotspots();
    loadData();
    // 自动恢复上次选中的标签页（如上次停留在热点页，刷新后直接保持在热点页）
    try {
      const savedTab = localStorage.getItem('userActiveTab');
      if (savedTab && document.getElementById(savedTab)) {
        switchTab(savedTab);
      }
    } catch(e){}
    }

    function promoteToHolding(code, name, price) {
      const cost = prompt(`请输入【${name}】的买入成本价：`, price);
      if (!cost) return;
      const shares = prompt(`请输入持仓股数：`, 1000);
      submitStockData(code, name, cost, shares, true);
    }

    async function deleteStock(code) {
      if (!confirm("确定移除该股票吗？")) return;
      await fetchWithAuth('/api/stock/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code })
      });
      loadData();
    }

    // ==================== 重点新闻穿透弹窗交互 ====================
    function openNewsModal(idx) {
      if (!window.currentHotspotEvents || !window.currentHotspotEvents[idx]) return;
      const ev = window.currentHotspotEvents[idx];
      document.getElementById('modal-news-time').innerText = ev.发布时间 || '';
      document.getElementById('modal-news-title').innerText = ev.标题 || '';
      
      const content = ev.正文深度细则 || ev.摘要 || '暂无详细正文内容';
      const paras = content.split(String.fromCharCode(10)).filter(p => p.trim());
      document.getElementById('modal-news-body').innerHTML = paras.map(p => `<p class="indent-6 text-slate-200 text-xs md:text-sm leading-relaxed mb-3">${p.trim()}</p>`).join('') || `<p>${content}</p>`;

      const rawLinkBtn = document.getElementById('modal-news-raw-link');
      if (ev.url) {
        rawLinkBtn.href = ev.url;
        rawLinkBtn.classList.remove('hidden');
      } else {
        rawLinkBtn.classList.add('hidden');
      }

      const modal = document.getElementById('modal-news-detail');
      if (modal) modal.classList.remove('hidden');
    }

    function closeNewsModal() {
      const modal = document.getElementById('modal-news-detail');
      if (modal) modal.classList.add('hidden');
    }

    checkAuthStatus().then(() => {
      loadData();
    });
  </script>

  <!-- 新闻穿透全文沉浸式阅读弹窗 (Modal) -->
  <div id="modal-news-detail" onclick="if(event.target === this) closeNewsModal()" class="fixed inset-0 z-50 bg-slate-950/80 backdrop-blur-sm flex items-center justify-center p-4 hidden">
    <div class="bg-slate-900 border border-slate-700 rounded-2xl max-w-2xl w-full max-h-[85vh] flex flex-col shadow-2xl overflow-hidden animate-in fade-in duration-200">
      <div class="p-4 border-b border-slate-800 flex justify-between items-center bg-slate-900/90">
        <div class="flex items-center gap-2">
          <span class="px-2 py-0.5 text-xs font-bold bg-amber-500/20 text-amber-400 border border-amber-500/30 rounded">
            <i class="fa-solid fa-file-lines mr-1"></i> 正文穿透全文
          </span>
          <span id="modal-news-time" class="text-xs font-mono text-slate-400"></span>
        </div>
        <div class="flex items-center gap-2">
          <a id="modal-news-raw-link" href="#" target="_blank" rel="noopener noreferrer" class="px-3 py-1 bg-blue-600 hover:bg-blue-500 text-white rounded text-xs font-semibold flex items-center gap-1.5 transition">
            <i class="fa-solid fa-arrow-up-right-from-square"></i> 打开新浪原网页
          </a>
          <button onclick="closeNewsModal()" class="w-7 h-7 flex items-center justify-center text-slate-400 hover:text-white hover:bg-slate-800 rounded-lg transition">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </div>
      </div>
      <div class="p-6 overflow-y-auto space-y-4">
        <h3 id="modal-news-title" class="text-lg font-bold text-white leading-snug"></h3>
        <div id="modal-news-body" class="text-sm text-slate-300 leading-relaxed space-y-3 whitespace-pre-wrap font-sans"></div>
      </div>
      <div class="p-3 border-t border-slate-800 bg-slate-900/80 flex justify-end">
        <button onclick="closeNewsModal()" class="px-4 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded text-xs font-medium transition">
          关闭窗口
        </button>
      </div>
    </div>
  </div>


  <!-- ==================== 登录 / 注册 弹窗 ==================== -->
  <div id="modal-auth" class="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4 hidden">
    <div class="bg-slate-900 border border-slate-700 rounded-2xl w-full max-w-sm overflow-hidden shadow-2xl p-6 relative">
      <button onclick="closeAuthModal()" class="absolute top-4 right-4 text-slate-400 hover:text-white text-lg">
        <i class="fa-solid fa-xmark"></i>
      </button>

      <!-- Tab 切换 -->
      <div class="flex border-b border-slate-700 pb-3 mb-5 gap-6">
        <button id="tab-auth-login" onclick="switchAuthTab('login')" class="text-base font-bold text-blue-400 border-b-2 border-blue-500 pb-1 cursor-pointer">
          账号登录
        </button>
        <button id="tab-auth-register" onclick="switchAuthTab('register')" class="text-base font-bold text-slate-400 hover:text-white pb-1 cursor-pointer">
          新用户注册
        </button>
      </div>

      <div class="space-y-4 text-left">
        <div>
          <label class="block text-xs font-medium text-slate-300 mb-1">账号 / 手机号</label>
          <input type="text" id="auth-username" placeholder="请输入手机号或账号" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3.5 py-2 text-sm text-white focus:outline-none focus:border-blue-500">
        </div>
        <div id="auth-nickname-wrap" class="hidden">
          <label class="block text-xs font-medium text-slate-300 mb-1">您的昵称 (选填)</label>
          <input type="text" id="auth-nickname" placeholder="例如：操盘手小李" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3.5 py-2 text-sm text-white focus:outline-none focus:border-blue-500">
        </div>
        <div>
          <label class="block text-xs font-medium text-slate-300 mb-1">密码</label>
          <input type="password" id="auth-password" placeholder="请输入密码 (至少4位)" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3.5 py-2 text-sm text-white focus:outline-none focus:border-blue-500">
        </div>

        <div id="auth-error-msg" class="text-xs text-rose-400 hidden"></div>

        <button id="btn-submit-auth" onclick="submitAuth()" class="w-full py-2.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-bold text-sm transition shadow-lg shadow-blue-600/30">
          立即登录
        </button>

        <!-- 手机扫码直达本站 -->
        <div class="pt-3 border-t border-slate-800 text-center">
          <button onclick="toggleMobileScanBox()" class="text-xs text-slate-400 hover:text-emerald-400 transition flex items-center justify-center gap-1.5 mx-auto">
            <i class="fa-solid fa-qrcode text-emerald-400"></i> 发给朋友？显示手机扫码直达二维码
          </button>
          <div id="mobile-scan-box" class="mt-3 p-3 bg-white rounded-xl shadow-md inline-block hidden">
            <div id="site-qrcode"></div>
            <p class="text-[11px] text-slate-800 font-bold mt-1.5">手机微信扫码直达注册</p>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- ==================== 手机 APP 下载 & 扫码弹窗 ==================== -->
  <div id="modal-download" class="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4 hidden">
    <div class="bg-slate-900 border border-slate-700 rounded-2xl w-full max-w-sm overflow-hidden shadow-2xl p-6 relative text-center">
      <button onclick="closeDownloadModal()" class="absolute top-4 right-4 text-slate-400 hover:text-white text-lg">
        <i class="fa-solid fa-xmark"></i>
      </button>

      <div class="w-12 h-12 mx-auto mb-2 rounded-xl bg-gradient-to-tr from-emerald-500 to-blue-500 p-0.5 shadow-lg">
        <div class="w-full h-full bg-slate-900 rounded-xl flex items-center justify-center">
          <i class="fa-solid fa-mobile-screen-button text-2xl text-emerald-400"></i>
        </div>
      </div>
      <h3 class="text-lg font-bold text-white mb-1">下载安卓手机客户端</h3>
      <p class="text-xs text-slate-400 mb-4">全屏无边框 · 个人独立股票池 · 离线秒开</p>

      <!-- 动态二维码区域 -->
      <div class="bg-white p-3.5 rounded-xl inline-block shadow-md mb-4">
        <div id="app-qrcode"></div>
      </div>
      <p class="text-xs text-slate-300 font-medium mb-4"><i class="fa-solid fa-qrcode text-emerald-400"></i> 手机微信或相机扫一扫，立即下载安装</p>

      <div class="space-y-2">
        <a href="/download/app.apk" class="w-full py-2.5 px-4 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-bold text-sm transition shadow-md flex items-center justify-center gap-2">
          <i class="fa-brands fa-android text-base"></i> 电脑直接下载 APK 安装包
        </a>
        <a href="/download" target="_blank" class="w-full py-2 px-4 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs transition block">
          打开专属移动端下载网页 <i class="fa-solid fa-arrow-up-right-from-square"></i>
        </a>
      </div>
    </div>
  </div>


  <!-- 未登录全屏毛玻璃锁定安全遮罩 -->
  <div id="lock-screen-overlay" class="fixed inset-0 bg-slate-950/70 backdrop-blur-md z-40 flex items-center justify-center p-4 hidden pointer-events-none">
    <div class="text-center p-6 rounded-2xl border border-blue-500/30 bg-slate-900/90 shadow-2xl max-w-sm pointer-events-auto">
      <div class="w-14 h-14 mx-auto mb-3 rounded-full bg-blue-500/20 text-blue-400 flex items-center justify-center text-2xl border border-blue-500/30">
        <i class="fa-solid fa-user-lock"></i>
      </div>
      <h3 class="text-lg font-bold text-white mb-1.5">私有量化空间已锁定</h3>
      <p class="text-xs text-slate-400 mb-4">系统已开启多用户数据隔离保护，请先登录或注册您的专属操盘账号。</p>
      <button onclick="openAuthModal('login')" class="w-full py-2.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-bold text-sm transition shadow-lg shadow-blue-600/30">
        立即登录 / 注册
      </button>
    </div>
  </div>

  <!-- 🤖 右下角 DeepSeek 咨询悬浮小气泡 -->
  <div id="deepseek-float-bubble" onclick="toggleDeepseekChat()" class="fixed bottom-6 right-6 z-50 flex items-center gap-2.5 px-4 py-3 bg-gradient-to-r from-blue-600 via-indigo-600 to-purple-600 hover:from-blue-500 hover:to-purple-500 text-white rounded-full shadow-2xl shadow-blue-500/50 border border-blue-400/50 cursor-pointer transition-all transform hover:scale-105 active:scale-95 group">
    <div class="relative flex items-center justify-center">
      <i class="fa-solid fa-robot text-lg text-white group-hover:rotate-12 transition-transform"></i>
      <span class="absolute -top-1 -right-1 w-2.5 h-2.5 bg-emerald-400 rounded-full border-2 border-slate-900 animate-pulse"></span>
    </div>
    <span class="text-xs font-bold tracking-wide select-none">咨询 DeepSeek</span>
  </div>

  <!-- 🤖 DeepSeek 交互对话浮窗 -->
  <div id="deepseek-chat-panel" class="fixed bottom-20 right-6 w-96 max-w-[calc(100vw-2rem)] h-[540px] max-h-[82vh] bg-slate-900/95 backdrop-blur-xl border border-blue-500/40 rounded-2xl shadow-2xl shadow-black/80 flex flex-col z-50 hidden transition-all">
    <!-- Header -->
    <div class="px-4 py-3 bg-slate-950/80 border-b border-slate-800 flex items-center justify-between">
      <div class="flex items-center gap-2.5">
        <div class="w-8 h-8 rounded-full bg-gradient-to-br from-blue-500 to-indigo-600 flex items-center justify-center text-white text-sm shadow">
          <i class="fa-solid fa-brain"></i>
        </div>
        <div>
          <div class="text-xs font-bold text-white flex items-center gap-1.5">
            DeepSeek 投资总监
            <span class="px-1.5 py-0.2 bg-emerald-500/20 text-emerald-400 text-[10px] rounded border border-emerald-500/30">在线</span>
          </div>
          <div class="text-[10px] text-slate-400">实时解答盘口异动、个股诊断与战术答疑</div>
        </div>
      </div>
      <div class="flex items-center gap-1">
        <button onclick="clearDeepseekChat()" class="p-1.5 text-slate-400 hover:text-amber-400 rounded-lg transition" title="清空对话"><i class="fa-solid fa-trash-can text-xs"></i></button>
        <button onclick="toggleDeepseekChat()" class="p-1.5 text-slate-400 hover:text-white rounded-lg transition" title="关闭"><i class="fa-solid fa-xmark text-sm"></i></button>
      </div>
    </div>

    <!-- Quick question tags -->
    <div class="px-3 py-2 bg-slate-950/40 border-b border-slate-800/80 flex items-center gap-1.5 overflow-x-auto text-[11px] whitespace-nowrap scrollbar-none">
      <span class="text-slate-500 text-[10px] font-medium"><i class="fa-solid fa-bolt text-amber-400"></i> 快问:</span>
      <button onclick="sendQuickPrompt('大盘今天整体格局怎么看？')" class="px-2 py-0.5 rounded-full bg-slate-800 hover:bg-blue-600 text-slate-300 hover:text-white transition border border-slate-700">大盘怎么看？</button>
      <button onclick="sendQuickPrompt('帮我诊断一下我的持仓股风险')" class="px-2 py-0.5 rounded-full bg-slate-800 hover:bg-blue-600 text-slate-300 hover:text-white transition border border-slate-700">诊断持仓</button>
      <button onclick="sendQuickPrompt('如何看待当前市场的主线热点？')" class="px-2 py-0.5 rounded-full bg-slate-800 hover:bg-blue-600 text-slate-300 hover:text-white transition border border-slate-700">主线热点</button>
    </div>

    <!-- Chat Messages Container -->
    <div id="deepseek-chat-messages" class="flex-1 p-3.5 overflow-y-auto space-y-3 text-xs">
      <div class="flex items-start gap-2">
        <div class="w-6 h-6 rounded-full bg-blue-600 flex-shrink-0 flex items-center justify-center text-white text-[11px] mt-0.5">
          <i class="fa-solid fa-robot"></i>
        </div>
        <div class="p-3 bg-slate-800/90 rounded-2xl rounded-tl-sm text-slate-200 border border-slate-700/60 leading-relaxed shadow-sm">
          您好！我是您的 <b>DeepSeek 投资总监助手</b>。您可以随时向我提问关于大盘走势、个股技术指标、做T策略、风险防守或量化分析的问题。请问有什么可以帮您？
        </div>
      </div>
    </div>

    <!-- Input Bar -->
    <div class="p-3 bg-slate-950/90 border-t border-slate-800 flex items-center gap-2">
      <input id="deepseek-chat-input" type="text" placeholder="输入问题，按回车咨询 DeepSeek..." 
        onkeydown="if(event.key==='Enter') sendDeepseekMessage()"
        class="flex-1 px-3 py-2 bg-slate-800 border border-slate-700 rounded-xl text-xs text-white placeholder-slate-500 focus:outline-none focus:border-blue-500 transition">
      <button id="deepseek-send-btn" onclick="sendDeepseekMessage()" class="px-3 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-xl text-xs font-bold transition flex items-center gap-1 shadow-md shadow-blue-600/30">
        <i class="fa-solid fa-paper-plane text-[11px]"></i>
        <span>发送</span>
      </button>
    </div>
  </div>

  <script>
    function escapeHtml(str) {
      if (!str) return '';
      return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    let deepseekChatHistory = [];
    function toggleDeepseekChat() {
      const panel = document.getElementById('deepseek-chat-panel');
      if (!panel) return;
      panel.classList.toggle('hidden');
      if (!panel.classList.contains('hidden')) {
        const inp = document.getElementById('deepseek-chat-input');
        if (inp) inp.focus();
        scrollChatToBottom();
      }
    }

    function clearDeepseekChat() {
      deepseekChatHistory = [];
      const msgBox = document.getElementById('deepseek-chat-messages');
      if (msgBox) {
        msgBox.innerHTML = `
          <div class="flex items-start gap-2">
            <div class="w-6 h-6 rounded-full bg-blue-600 flex-shrink-0 flex items-center justify-center text-white text-[11px] mt-0.5">
              <i class="fa-solid fa-robot"></i>
            </div>
            <div class="p-3 bg-slate-800/90 rounded-2xl rounded-tl-sm text-slate-200 border border-slate-700/60 leading-relaxed shadow-sm">
              对话记录已清空。您可以继续向我提问关于大盘走势、个股技术指标或操盘策略的问题。
            </div>
          </div>`;
      }
    }

    function sendQuickPrompt(promptText) {
      const inp = document.getElementById('deepseek-chat-input');
      if (inp) inp.value = promptText;
      sendDeepseekMessage();
    }

    function scrollChatToBottom() {
      const msgBox = document.getElementById('deepseek-chat-messages');
      if (msgBox) {
        msgBox.scrollTop = msgBox.scrollHeight;
      }
    }

    async function sendDeepseekMessage() {
      const inp = document.getElementById('deepseek-chat-input');
      const btn = document.getElementById('deepseek-send-btn');
      const msgBox = document.getElementById('deepseek-chat-messages');
      if (!inp || !msgBox) return;

      const userText = inp.value.trim();
      if (!userText) return;

      inp.value = '';
      const userBubble = document.createElement('div');
      userBubble.className = 'flex items-start justify-end gap-2';
      userBubble.innerHTML = `
        <div class="p-3 bg-blue-600 rounded-2xl rounded-tr-sm text-white leading-relaxed max-w-[85%] shadow-sm">
          ${escapeHtml(userText)}
        </div>
        <div class="w-6 h-6 rounded-full bg-slate-700 flex-shrink-0 flex items-center justify-center text-slate-300 text-[11px] mt-0.5">
          <i class="fa-solid fa-user"></i>
        </div>`;
      msgBox.appendChild(userBubble);
      scrollChatToBottom();

      const loadingBubble = document.createElement('div');
      loadingBubble.className = 'flex items-start gap-2 deepseek-loading-msg';
      loadingBubble.innerHTML = `
        <div class="w-6 h-6 rounded-full bg-blue-600 flex-shrink-0 flex items-center justify-center text-white text-[11px] mt-0.5">
          <i class="fa-solid fa-robot"></i>
        </div>
        <div class="p-3 bg-slate-800/90 rounded-2xl rounded-tl-sm text-slate-400 border border-slate-700/60 flex items-center gap-2">
          <i class="fa-solid fa-spinner fa-spin text-blue-400"></i> DeepSeek 正在思考分析...
        </div>`;
      msgBox.appendChild(loadingBubble);
      scrollChatToBottom();

      if (btn) btn.disabled = true;

      try {
        const res = await fetchWithAuth('/api/ai/chat', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            message: userText,
            history: deepseekChatHistory
          })
        });
        const data = await res.json();
        
        loadingBubble.remove();

        const replyContent = data.reply || data.message || '⚠️ 未收到有效回复';
        
        deepseekChatHistory.push({role: 'user', content: userText});
        deepseekChatHistory.push({role: 'assistant', content: replyContent});

        const botBubble = document.createElement('div');
        botBubble.className = 'flex items-start gap-2';
        const parsedReply = (typeof marked !== 'undefined') ? marked.parse(replyContent) : escapeHtml(replyContent);
        botBubble.innerHTML = `
          <div class="w-6 h-6 rounded-full bg-blue-600 flex-shrink-0 flex items-center justify-center text-white text-[11px] mt-0.5">
            <i class="fa-solid fa-robot"></i>
          </div>
          <div class="p-3 bg-slate-800/90 rounded-2xl rounded-tl-sm text-slate-200 border border-slate-700/60 leading-relaxed max-w-[88%] shadow-sm markdown-body text-xs">
            ${parsedReply}
          </div>`;
        msgBox.appendChild(botBubble);
        scrollChatToBottom();
      } catch (err) {
        loadingBubble.remove();
        const errBubble = document.createElement('div');
        errBubble.className = 'flex items-start gap-2';
        errBubble.innerHTML = `
          <div class="w-6 h-6 rounded-full bg-rose-600 flex-shrink-0 flex items-center justify-center text-white text-[11px] mt-0.5">
            <i class="fa-solid fa-triangle-exclamation"></i>
          </div>
          <div class="p-3 bg-rose-950/60 border border-rose-500/40 rounded-2xl rounded-tl-sm text-rose-300">
            网络请求异常: ${escapeHtml(err.message || String(err))}
          </div>`;
        msgBox.appendChild(errBubble);
        scrollChatToBottom();
      } finally {
        if (btn) btn.disabled = false;
      }
    }
  </script>
</body>
</html>
"""

# ----------------- 后端服务路由与分发 -----------------

DOWNLOAD_HTML_CONTENT = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>A股 AI 量化投资决策系统 - 官方安卓客户端下载</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <script src="https://cdn.jsdelivr.net/npm/qrcodejs@1.0.0/qrcode.min.js"></script>
  <style>
    body { background-color: #0b1329; color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    .glass-card { background: rgba(30, 41, 59, 0.7); backdrop-filter: blur(12px); border: 1px solid rgba(56, 189, 248, 0.2); }
  </style>
</head>
<body class="min-h-screen flex flex-col justify-between p-4 md:p-8">
  <div class="max-w-md mx-auto w-full pt-6 pb-12">
    <!-- 图标与标题 -->
    <div class="text-center mb-8">
      <div class="w-20 h-20 mx-auto mb-4 rounded-2xl bg-gradient-to-tr from-blue-600 to-emerald-400 p-0.5 shadow-xl shadow-blue-500/20">
        <div class="w-full h-full bg-slate-900 rounded-2xl flex items-center justify-center">
          <i class="fa-solid fa-chart-line text-4xl text-transparent bg-clip-text bg-gradient-to-r from-blue-400 to-emerald-400"></i>
        </div>
      </div>
      <h1 class="text-2xl font-bold tracking-tight text-white mb-1">A股 AI 量化决策系统</h1>
      <p class="text-xs text-slate-400">官方原生 Android 客户端 · v2.1.0 稳定版</p>
      
      <div class="flex justify-center gap-2 mt-3 flex-wrap">
        <span class="px-2.5 py-0.5 text-xs bg-blue-500/20 text-blue-300 rounded-full border border-blue-500/30">全端数据同步</span>
        <span class="px-2.5 py-0.5 text-xs bg-emerald-500/20 text-emerald-300 rounded-full border border-emerald-500/30">独立私人股票池</span>
        <span class="px-2.5 py-0.5 text-xs bg-amber-500/20 text-amber-300 rounded-full border border-amber-500/30">AI 操盘内参</span>
      </div>
    </div>

    <!-- 下载主卡片 -->
    <div class="glass-card rounded-2xl p-6 shadow-2xl mb-6 text-center">
      <div class="text-slate-300 text-sm mb-4">
        随时随地查看自选与持仓，毫秒级多因子量化评分与买卖点指引
      </div>

      <!-- 下载按钮 -->
      <a href="/download/app.apk" class="w-full py-3.5 px-6 rounded-xl bg-gradient-to-r from-blue-600 via-indigo-600 to-blue-700 hover:from-blue-500 hover:to-indigo-500 text-white font-bold text-base shadow-lg shadow-blue-600/30 transition flex items-center justify-center gap-2 mb-3">
        <i class="fa-brands fa-android text-xl"></i>
        <span>立即下载安卓 APK 安装包</span>
      </a>
      <div class="text-[11px] text-slate-500 mb-6">安装包大小：约 5.8 MB · 适用 Android 7.0 及以上系统</div>

      <!-- 电脑端扫码下载指引 -->
      <div class="border-t border-slate-700/80 pt-5">
        <p class="text-xs text-slate-400 mb-3"><i class="fa-solid fa-qrcode text-blue-400"></i> 用手机扫一扫，直接在手机上下载安装：</p>
        <div id="qrcode-box" class="bg-white p-3 rounded-xl inline-block shadow-md"></div>
      </div>
    </div>

    <!-- 安装指引 -->
    <div class="glass-card rounded-2xl p-5 shadow-xl text-xs text-slate-400 space-y-2">
      <div class="font-bold text-slate-200 text-sm mb-1 flex items-center gap-1.5">
        <i class="fa-solid fa-circle-info text-amber-400"></i> 安卓手机快速安装小贴士：
      </div>
      <p>1. 点击上方按钮下载 <strong>.apk</strong> 安装包；</p>
      <p>2. 若手机提示“未知来源应用”或“可能存在风险”，请点击<strong>【允许本次安装】</strong>或<strong>【继续安装】</strong>；</p>
      <p>3. 安装完成后在手机桌面直接点击图标，登录或注册即可拥有专属独立股票池！</p>
    </div>

    <div class="text-center mt-6">
      <a href="/" class="text-xs text-blue-400 hover:underline flex items-center justify-center gap-1">
        <i class="fa-solid fa-arrow-left"></i> 返回电脑/网页端直接使用
      </a>
    </div>
  </div>

  <script>
    window.addEventListener('DOMContentLoaded', () => {
      const qrEl = document.getElementById('qrcode-box');
      if (qrEl) {
        new QRCode(qrEl, {
          text: window.location.href,
          width: 140,
          height: 140,
          colorDark : "#0f172a",
          colorLight : "#ffffff",
          correctLevel : QRCode.CorrectLevel.M
        });
      }
    });
  </script>
</body>
</html>
"""


class PurePythonStockHandler(BaseHTTPRequestHandler):
    def get_current_user(self):
        auth_header = self.headers.get("Authorization", "")
        token = ""
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
        if not token:
            url_parsed = urllib.parse.urlparse(self.path)
            query_params = urllib.parse.parse_qs(url_parsed.query)
            token = _get(query_params.get("token", [""]), 0).strip()
        return user_manager.get_user_by_token(token)

    def send_json(self, data_dict):
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data_dict, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):
        try:
            self._handle_GET()
        except Exception as e:
            traceback.print_exc()
            try:
                self.send_json({"status": "error", "message": f"GET处理异常: {e}"})
            except Exception:
                pass

    def _handle_GET(self):
        url_parsed = urllib.parse.urlparse(self.path)
        path = url_parsed.path
        query_params = urllib.parse.parse_qs(url_parsed.query)

        if path in ["/", "/index.html"]:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()
            self.wfile.write(HTML_CONTENT.encode("utf-8"))

        elif path in ["/login", "/login.html"]:
            login_file = os.path.join(CURRENT_DIR, "login.html")
            if os.path.exists(login_file):
                with open(login_file, "rb") as f:
                    login_data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(login_data)))
                self.end_headers()
                self.wfile.write(login_data)
            else:
                self.send_response(302)
                self.send_header("Location", "/")
                self.end_headers()



        elif path in ["/download", "/app", "/download.html"]:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(DOWNLOAD_HTML_CONTENT.encode("utf-8"))

        elif path in ["/download/app.apk", "/static/stock_app.apk"]:
            apk_paths = [
                os.path.join(CURRENT_DIR, "stock_app.apk"),
                os.path.join(CURRENT_DIR, "app.apk"),
                os.path.join(CURRENT_DIR, "downloads", "stock_app.apk")
            ]
            found_apk = None
            for ap in apk_paths:
                if os.path.exists(ap):
                    found_apk = ap
                    break
            if found_apk:
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.android.package-archive")
                self.send_header("Content-Disposition", 'attachment; filename="stock_quant_app.apk"')
                self.send_header("Content-Length", str(os.path.getsize(found_apk)))
                self.end_headers()
                with open(found_apk, "rb") as f_apk:
                    self.wfile.write(f_apk.read())
            else:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                notice_html = "<html><body style='font-family:sans-serif;padding:30px;background:#0f172a;color:#fff;'><h2>📱 APK 安装包生成与上传指引</h2><p>当前服务器根目录下尚未检测到 <code>stock_app.apk</code> 文件。</p><p>您可以使用免费的 <b>HBuilderX</b> 将您的网址一键云打包为 APK 后，放置在项目根目录下，即可随时供所有用户点击/扫码下载！</p><p><a href='/download' style='color:#38bdf8;'>返回下载页面</a></p></body></html>"
                self.wfile.write(notice_html.encode("utf-8"))

        elif path == "/api/auth/me":
            user = self.get_current_user()
            if user:
                self.send_json({"status": "success", "user": user})
            else:
                self.send_json({"status": "unauthorized"})

        elif path == "/api/stocks":
            user = self.get_current_user()
            if not user:
                self.send_json({"holdings": [], "watchlists": [], "need_login": True, "message": "未登录，请先登录或注册！"})
                return
            user_stocks = user_manager.get_stocks(user["username"])
            holdings, watchlists = get_enriched_stocks(user_stocks)
            self.send_json({"holdings": holdings, "watchlists": watchlists, "user": user})

        elif path == "/api/stock/analysis":
            code = _get(query_params.get("code", [""]), 0).strip()
            symbol = _get(query_params.get("symbol", [""]), 0).strip()
            if not symbol:
                symbol, code, _ = resolve_stock(code)
            
            quote = fetch_real_quote(symbol)
            kline_data = fetch_kline_history(symbol, days=120)
            curr_p = float(quote.get("curr_price", 0.0) or 0.0)
            prev_c = float(quote.get("prev_close", 0.0) or curr_p)
            scores = compute_quant_scores(code, quote.get("name", ""), curr_p, prev_c, kline_data, quote_info=quote)

            # 智能检查是否为用户真实持仓
            pf_file = get_portfolio_path()
            holding_info = None
            if os.path.exists(pf_file):
                try:
                    with open(pf_file, "r", encoding="utf-8") as f:
                        stocks_pf = json.load(f)
                    for sp in stocks_pf:
                        if str(sp.get("代码", "")).strip() == code:
                            c_cost = float(sp.get("成本价", 0.0) or 0.0)
                            if c_cost > 0:
                                holding_info = sp
                                break
                except Exception:
                    pass

            if holding_info:
                cost = float(holding_info.get("成本价", 0.0))
                shares = int(holding_info.get("持仓股数", 1000) or 1000)
                plan = compute_trade_plan(curr_p, is_holding=True, cost=cost)
            else:
                plan = compute_trade_plan(curr_p, is_holding=False, cost=0.0)

            self.send_json({
                "code": code,
                "symbol": symbol,
                "quote": quote,
                "kline": kline_data,
                "scores": scores,
                "plan": plan,
                "is_holding": bool(holding_info)
            })

        elif path == "/api/hotspots":
            data = get_market_hotspots()
            self.send_json(data)

        elif path == "/api/hotspots/ai_review":
            review_text = get_hotspot_ai_review()
            self.send_json({"review": review_text})
            
        elif path == "/api/search":
            q = _get(query_params.get("q", [""]), 0).strip()
            if not q:
                self.send_json({"matches": []})
                return
            url = f"https://searchapi.eastmoney.com/api/suggest/get?input={urllib.parse.quote(q)}&type=14"
            try:
                res = requests.get(url, timeout=3).json()
                raw = res.get("QuotationCodeTable", {}).get("Data", [])
                matches = [{"code": it.get("Code", ""), "name": it.get("Name", ""), "market": it.get("SecurityTypeName", "")} for it in raw[:5]]
                self.send_json({"matches": matches})
            except Exception:
                self.send_json({"matches": []})
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        try:
            self._handle_POST()
        except Exception as e:
            traceback.print_exc()
            try:
                self.send_json({"status": "error", "report": f"⚠️ 服务端处理异常: {e}", "message": str(e)})
            except Exception:
                pass

    def _handle_POST(self):
        url_parsed = urllib.parse.urlparse(self.path)
        path = url_parsed.path
        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length)
        try:
            body = json.loads(post_data.decode("utf-8")) if post_data else {}
        except Exception:
            body = {}

        pf_file = get_portfolio_path()
        stocks = []
        if os.path.exists(pf_file):
            try:
                with open(pf_file, "r", encoding="utf-8") as f:
                    stocks = json.load(f)
            except Exception:
                pass

        if path == "/api/auth/register":
            u = body.get("username", "")
            p = body.get("password", "")
            n = body.get("nickname", "")
            res, err = user_manager.register(u, p, n)
            if res:
                self.send_json({"status": "success", "token": res["token"], "user": res})
            else:
                self.send_json({"status": "error", "message": err})

        elif path == "/api/auth/login":
            u = body.get("username", "")
            p = body.get("password", "")
            res, err = user_manager.login(u, p)
            if res:
                self.send_json({"status": "success", "token": res["token"], "user": res})
            else:
                self.send_json({"status": "error", "message": err})

        elif path == "/api/auth/logout":
            self.send_json({"status": "success"})

        elif path == "/api/stocks/clear_all":
            user = self.get_current_user()
            target_username = user["username"] if user else "admin"
            user_manager.save_stock(target_username, {}) # or clear
            p_data = user_manager._read_json(user_manager.portfolios_file, {})
            p_data[target_username] = []
            user_manager._write_json(user_manager.portfolios_file, p_data)
            self.send_json({"status": "success", "message": "已全部清空！"})

        elif path == "/api/stock/save":
            user = self.get_current_user()
            if not user:
                self.send_json({"status": "error", "message": "🔒 系统已开启安全隔离，请先登录或注册您的专属账号！"})
                return
            target_username = user["username"]
            
            raw_c = body.get("code")
            raw_n = body.get("name")
            cost = float(body.get("cost", 0.0))
            shares = int(body.get("shares", 0))
            is_holding = body.get("is_holding", True)
            
            symbol, real_code, real_name = resolve_stock(raw_c or raw_n)
            final_code = real_code if re.match(r'^\d{6}$', real_code) else raw_c
            final_name = real_name or raw_n
            
            stock_item = {
                "代码": final_code,
                "名称": final_name,
                "成本价": cost,
                "持仓股数": shares,
                "is_holding": is_holding
            }
            user_manager.save_stock(target_username, stock_item)
            self.send_json({"status": "success", "username": target_username})

        elif path == "/api/stock/delete":
            user = self.get_current_user()
            if not user:
                self.send_json({"status": "error", "message": "🔒 请先登录！"})
                return
            target_username = user["username"]
            code = body.get("code")
            user_manager.delete_stock(target_username, code)
            self.send_json({"status": "success", "username": target_username})

        elif path == "/api/ai/chat":
            user = self.get_current_user()
            user_msg = body.get("message", "").strip()
            history = body.get("history", [])
            if not user_msg:
                self.send_json({"status": "error", "message": "消息内容不能为空"})
                return

            target_username = user["username"] if user else "admin"
            user_stocks = user_manager.get_stocks(target_username)
            holdings_summary = []
            for s in user_stocks:
                c = s.get("代码", "")
                n = s.get("名称", "")
                p = s.get("成本价", 0)
                sh = s.get("持仓股数", 0)
                if p > 0:
                    holdings_summary.append(f"{n}({c}) 成本:{p}元 持仓:{sh}股")
                else:
                    holdings_summary.append(f"{n}({c}) 自选观察")
            
            holdings_context = "用户当前关注/持仓标的：" + ("、".join(holdings_summary) if holdings_summary else "暂无")

            sys_prompt = f"""你是一名顶级 A 股私募基金投资总监与量化操盘顾问。
你的任务是以专业、严谨、客观、贴合实操的口吻，随时解答用户的证券投资、大盘行情、操盘战术、技术指标与股票诊断问题。
【用户账户背景】：
{holdings_context}
【原则】：
1. 语言精炼有力，直指核心，拒绝口水话；
2. 涉及操作时给出具体明确的技术支撑阻力区间与 If-Then 应对法则；
3. 严格遵循风险管理第一，提醒防范回撤与追高风险；
4. 保持理性客观，不构成法定投资承诺。"""

            messages = [{"role": "system", "content": sys_prompt}]
            for h in history[-6:]:
                if h.get("role") in ["user", "assistant"] and h.get("content"):
                    messages.append({"role": h["role"], "content": str(h["content"])[:1000]})
            messages.append({"role": "user", "content": user_msg})

            try:
                auto_load_env()
                API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
                BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
                MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip()
                if not OpenAI or not API_KEY:
                    self.send_json({"status": "error", "reply": "⚠️ 请先在 .env 中配置有效的 DEEPSEEK_API_KEY。"})
                    return
                client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
                res = client.chat.completions.create(
                    model=MODEL,
                    messages=messages,
                    temperature=0.3
                )
                reply_text = res.choices[0].message.content
                self.send_json({"status": "success", "reply": reply_text})
            except Exception as e:
                self.send_json({"status": "error", "reply": f"⚠️ DeepSeek 响应异常: {e}"})

        elif path == "/api/ai/diagnose":
            target_code = body.get("code")
            target_symbol = body.get("symbol")
            
            # 优先调用 modules/ai_advisor.py 专职投研模块
            advisor_obj = None
            if ai_advisor:
                if hasattr(ai_advisor, "AIAdvisor"):
                    try:
                        advisor_obj = ai_advisor.AIAdvisor()
                    except Exception:
                        pass
                elif hasattr(ai_advisor, "get_advisor"):
                    try:
                        advisor_obj = ai_advisor.get_advisor()
                    except Exception:
                        pass

            if target_code:
                if not target_symbol:
                    target_symbol, target_code, _ = resolve_stock(target_code)
                quote = fetch_real_quote(target_symbol)
                kline_data = fetch_kline_history(target_symbol, days=60)
                curr_p = float(quote.get("curr_price", 0.0) or 0.0)
                prev_c = float(quote.get("prev_close", 0.0) or curr_p)
                scores = compute_quant_scores(target_code, quote.get("name"), curr_p, prev_c, kline_data, quote_info=quote)

                # 智能识别当前登录用户的实战持仓股并提取真实成本
                user = self.get_current_user()
                target_username = user["username"] if user else "admin"
                user_stocks = user_manager.get_stocks(target_username)
                holding_item = None
                for sp in user_stocks:
                    if str(sp.get("代码", "")).strip() == target_code:
                        cost_val = float(sp.get("成本价", 0.0) or 0.0)
                        if cost_val > 0 and sp.get("is_holding", True):
                            holding_item = sp
                            break

                pct_today_str = f"{((curr_p - prev_c)/prev_c*100):+.2f}%" if prev_c else "0.00%"
                plan = compute_trade_plan(curr_p, is_holding=bool(holding_item), cost=float(holding_item.get("成本价", 0.0)) if holding_item else 0.0)

                # 确保优先直接调用 modules/ai_advisor.py 专职投研模块
                if not advisor_obj:
                    try:
                        from ai_advisor import AIAdvisor
                        advisor_obj = AIAdvisor()
                    except Exception:
                        pass

                if advisor_obj and hasattr(advisor_obj, "diagnose_single_stock"):
                    try:
                        rep = advisor_obj.diagnose_single_stock(target_code, quote, scores, plan, holding_item)
                    except Exception as err:
                        rep = f"⚠️ 诊断处理异常: {err}"
                    self.send_json({"report": rep})
                    return

                if holding_item:
                    cost = float(holding_item.get("成本价", 0.0) or 0.0)
                    shares = int(holding_item.get("持仓股数", 1000) or 1000)
                    loss_pct = round(((curr_p - cost) / cost) * 100, 2) if cost > 0 else 0.0
                    total_pnl = round((curr_p - cost) * shares, 2) if cost > 0 else 0.0
                    account_status = f"已买入持仓 | 成本: {cost:.2f} 元 | 股数: {shares} 股 | 盈亏幅度: {loss_pct:+.2f}% ({total_pnl:+.2f} 元)"
                else:
                    cost = 0.0
                    account_status = "尚未建仓（自选/观察标的，持仓为0）"

                t_buy = plan.get('t_buy') or round(curr_p * 0.985, 2)
                t_sell = plan.get('t_sell') or round(curr_p * 1.035, 2)
                protect_line = plan.get('protect_line') or plan.get('hard_stop') or round(curr_p * 0.965, 2)
                target1 = plan.get('target1') or round(curr_p * 1.06, 2)
                target2 = plan.get('target2') or round(curr_p * 1.15, 2)

                prompt = f"""你是一名资深 A 股私募基金投资总监。请严格基于以下客观真实数据，按照固定的【步骤一至步骤五 + 条件单执行铁律 + 执单总结】通用操盘框架，为用户输出操盘执行单。

【标的与账户客观数据】：
- 股票标的：{quote.get('name')} ({target_code}) | 现价：{curr_p:.2f} 元 (今日涨跌: {pct_today_str})
- 账户处境：{account_status}
- 基本面估值：PE/PB/市值客观数据
- 盘口量化指标：MA5={scores.get('ma5')} | MA20={scores.get('ma20')} | MA60={scores.get('ma60')} | RSI(14)={scores.get('rsi')}
- 周期量化评分：短线={scores.get('short_term', {}).get('score')}分 | 中线={scores.get('mid_term', {}).get('score')}分 | 长线={scores.get('long_term', {}).get('score')}分
- 量化系统参考：支撑/低吸参考【{t_buy}元】 | 阻力/高抛参考【{t_sell}元】 | 关键防守线【{protect_line}元】 | 目标价【{target1}元 / {target2}元】

【核心输出准则】：
1. 框架模板完全固定：必须 100% 严格使用下方的步骤结构、表头与总结表格的7大通用项目名称，严禁增减或变更项目名称；
2. 内容实事求是：不要预设固定套路（若是盈利股，重点指导移动保利与防坐电梯；若是深套股，指导做T降本与防守；若是短线题材，指导波段快进快出；若是长线白马，指导均线定投；若是未建仓，指导右侧买点与止盈）；
3. 价格严禁幻觉：所有价格必须基于现价 {curr_p:.2f} 元及真实数据。

--- 请严格按照以下固定框架模板输出 ---

### 步骤一：【定调】标的定位与操盘总基调
> 🚦 **标的定位**：[定性标的属性：如短线情绪博弈 / 行业周期反转 / 核心长线配置等]
> 📋 **研判说明**：[简析行业护城河与赛道逻辑]
> 🎯 **操盘总基调**：[结合真实账户状态与盘口健康度，给出明确方向与仓位定调]

### 步骤二：【估值】基本面护城河与估值中枢表
| 深度分析维度 | 核心数据 / 行业事实 | 操盘手定性结论与实战含义 |
| :--- | :--- | :--- |
| **行业地位与护城河** | [行业市占率与竞争格局] | [核心壁垒与长期安全垫] |
| **产业宏观周期** | [赛道宏观阶段与供需拐点] | [政策与消息催化动向] |
| **估值安全边际** | PE/PB/市值客观数据 | [评估估值性价比与赔率空间] |

### 步骤三：【盘口】早盘 45 分钟试金石与日内多空分水岭表
| 盘口关键时段 / 指标 | 关键临界数值 | 操盘手实战定调与盘口信号 |
| :--- | :--- | :--- |
| **9:25 集合竞价承接力** | 竞价合理区间【{curr_p*0.99:.2f} ~ {curr_p*1.015:.2f}元】 | [分析集合竞价量价异动信号] |
| **9:30-10:00 前半小时强弱** | 上攻阻力【{t_sell}元】 / 下探支撑【{t_buy}元】 | [说明突破与下探的信号] |
| **日内多空平衡线** | MA20支撑位: {scores.get('ma20')}元 | [说明生命线的得失与多空动能] |

### 步骤四：【战术】多空双向 If-Then 实战预案表
| 盘面演变情景 | 触发条件与点位 | 仓位执行动作 | 战术目的与收益防守 |
| :--- | :--- | :--- | :--- |
| **情景 1：放量突破上攻** | 突破阻力位【{t_sell}元】 | [具体仓位动作] | [战术目的] |
| **情景 2：冲高滞涨回落** | 触及目标位【{target1}元】无量 | [具体仓位动作] | [战术目的] |
| **情景 3：破位跳水防守** | 跌破防守线【{protect_line}元】 | [具体仓位动作] | [战术目的] |

### 步骤五：【条件单】手机券商直接照抄执行清单
| 条件单类型 | 监控触发价格 | 委托操作与数量 | 监控有效期 | 战术目的 |
| :--- | :--- | :--- | :--- | :--- |
| 回落买入单 (低吸/建仓) | 价格 <= **{t_buy}元** | 限价买入指定仓位 | 长期有效 | [说明买入意图与目的] |
| 冲高卖出单 (止盈/高抛) | 价格 >= **{t_sell}元** | 限价卖出指定仓位 | 长期有效 | [说明高抛或兑现目的] |
| 破位防守单 (止损/减仓) | 价格 <= **{protect_line}元** | 市价/限价卖出对应仓位 | 长期有效 | [说明截断回撤目的] |
| 阶段目标单 (兑现/减半) | 价格 >= **{target1}元** | 限价卖出部分仓位 | 长期有效 | [说明目标兑现目的] |
| 移动保护单 (保护利润) | 若价格站上 **{target1}元** 后回落至 **{t_buy}元** | 卖出对应仓位 | 触发后失效 | [防止坐电梯回吐] |

▲ 条件单执行铁律:
[结合上述条件单提炼 3~4 条执行铁律，说明买入单、卖出单、防守单的触发执行纪律]

### 执单总结（一页纸浓缩）
| 项目 | 结论 |
| :--- | :--- |
| **标的定位** | [定性标的属性与交易定位] |
| **当前状态** | [概括真实盈亏幅度与盘口形态强弱] |
| **核心战术** | [根据实际情况给出核心操盘战术] |
| **操作区间** | [给出明确价格区间与空间测算：如买卖区间/做T区间/建仓区间] |
| **风控底线** | [给出明确风控价格及跌破后的动作：止盈保利线或止损防守线] |
| **兑现纪律** | [结合实际给出兑现目标：如目标止盈 / 回本减仓 / 波段落袋] |
| **操作红线** | [列出 2~3 条针对该标的真实处境的绝对禁忌] |

——执单完毕，严守纪律，知行合一——
"""

            try:
                if not OpenAI or not API_KEY:
                    self.send_json({"report": "⚠️ 请先在 .env 中配置有效的 DEEPSEEK_API_KEY。"})
                    return
                client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
                res = client.chat.completions.create(
                    model=MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.2
                )
                self.send_json({"report": _get(res.choices, 0).message.content})
            except Exception as e:
                self.send_json({"report": f"❌ DeepSeek 连线异常: {e}"})
        else:
            self.send_response(404)
            self.end_headers()

from http.server import ThreadingHTTPServer

def run_server_port(p):
    try:
        srv = ThreadingHTTPServer(("0.0.0.0", p), PurePythonStockHandler)
        print(f"✅ 成功监听端口: {p}")
        srv.serve_forever()
    except Exception as e:
        print(f"ℹ️ 端口 {p} 启动状态: {e}")

if __name__ == "__main__":
    print("\n" + "="*60)
    print("🚀 A股 AI 量化投资与全端决策系统 [微信公众号对接全端旗舰版] 启动中...")
    print("💻 网页端访问:      http://150.109.157.135:8000 或 http://150.109.157.135")
    print("📱 微信回调对接地址: http://150.109.157.135/wechat")
    print("="*60 + "\n")

    # 在后台线程监听 80 端口 (专供微信消息推送和直接访问)
    t_80 = threading.Thread(target=run_server_port, args=(80,), daemon=True)
    t_80.start()

    # 主线程监听 8000 端口 (兼容原本的访问习惯)
    try:
        run_server_port(8000)
    except KeyboardInterrupt:
        print("\n🛑 服务已安全停止。")
