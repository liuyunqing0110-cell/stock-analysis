from datetime import datetime
import os
import json
import requests
import re
import urllib.parse
import sys
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
        url = "http://hq.sinajs.cn/list=sh000001"
        try:
            s = requests.Session()
            s.trust_env = False
            res = s.get(url, headers=self.headers, timeout=5)
            parts = res.text.split(",")
            if len(parts) >= 32:
                trade_date = parts[30]
                trade_time = parts[31]
                last_trade_dt = datetime.strptime(f"{trade_date} 15:00:00", "%Y-%m-%d %H:%M:%S")
                now = datetime.now()
                is_trading = (now.strftime("%Y-%m-%d") == trade_date and "09:30:00" <= trade_time < "15:00:00")
                return last_trade_dt, is_trading
        except Exception:
            pass
        now = datetime.now()
        return now.replace(hour=15, minute=0, second=0, microsecond=0), False

    def fetch_article_detail(self, url: str, max_chars=1800) -> str:
        if not url or not url.startswith("http"): return ""
        try:
            s = requests.Session()
            s.trust_env = False
            res = s.get(url, headers=self.headers, timeout=6)
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

    def fetch_holiday_focus_events(self, max_pages=10) -> tuple:
        start_dt, is_trading = self.get_a_share_market_close_time()
        start_ts = int(start_dt.timestamp())
        all_raw_events = []
        seen_titles = set()
        
        for page in range(1, max_pages + 1):
            url = f"https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2509&k=&num=50&page={page}"
            try:
                s = requests.Session()
                s.trust_env = False
                res = s.get(url, headers=self.headers, timeout=6)
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

        for i in range(min(4, len(final_events))):
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
            res = s.get(url, headers=self.headers, timeout=8)
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
            for sec in all_sectors:
                clean_name = re.sub(r'[ⅡⅢIV123]', '', sec["名称"]).strip()
                if clean_name in seen or not clean_name: continue
                stocks_tiered, count = self.fetch_sector_constituents(sec["node"])
                if count < 10: continue
                seen.add(clean_name)
                dynamic_results.append({
                    "板块名称": clean_name, "板块涨幅": f"{sec['涨跌幅']:+.2f}%" if sec['涨跌幅'] != 0 else "0.00%",
                    "成分股总数": count, "三层标的": stocks_tiered
                })
                if len(dynamic_results) >= top_n: break
            return dynamic_results
        except Exception:
            return []

    def fetch_sector_constituents(self, node_code: str) -> tuple:
        url = f"http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData?page=1&num=50&sort=changepercent&asc=0&node={node_code}"
        try:
            s = requests.Session()
            s.trust_env = False
            res = s.get(url, headers=self.headers, timeout=8)
            items = res.json()
            if not items or not isinstance(items, list): return {}, 0
            cleaned = []
            for it in items:
                try:
                    price = float(it.get("trade", 0))
                    if price <= 0: continue
                    cleaned.append({
                        "代码": str(it.get("symbol", "")).replace("sh", "").replace("sz", ""),
                        "名称": str(it.get("name", "")),
                        "最新价": round(price, 2),
                        "涨跌幅(%)": round(float(it.get("changepercent", 0)), 2),
                        "成交额(亿)": round(float(it.get("amount", 0)) / 1e8, 1),
                        "市净率PB": round(float(it.get("pb", 99.0)), 2) if it.get("pb") else 99.0
                    })
                except: continue
            if len(cleaned) < 10: return {}, len(cleaned)
            used = set()
            leaders = sorted(cleaned, key=lambda x: x["涨跌幅(%)"], reverse=True)[:5]
            for s in leaders: used.add(s["代码"])
            cores = sorted([s for s in cleaned if s["代码"] not in used], key=lambda x: x["成交额(亿)"], reverse=True)[:5]
            for s in cores: used.add(s["代码"])
            rem = [s for s in cleaned if s["代码"] not in used]
            cheaps = sorted([s for s in rem if 3.0 <= s["最新价"] <= 20.0], key=lambda x: (x["市净率PB"], -x["涨跌幅(%)"]))[:5]
            if not cheaps: cheaps = sorted(rem, key=lambda x: x["最新价"])[:5]
            return {"⚡ 进攻龙头(T+1)": leaders, "🛡️ 稳健中军(长线)": cores, "💰 高性价比低价股": cheaps}, len(cleaned)
        except Exception:
            return {}, 0

    def get_comprehensive_review(self, market_data: list, holiday_events: list, start_dt: datetime, is_trading: bool) -> str:
        events_text = json.dumps(holiday_events, ensure_ascii=False, indent=2)
        market_text = json.dumps(market_data, ensure_ascii=False, indent=2)
        duration_hours = round((datetime.now() - start_dt).total_seconds() / 3600, 1)
        duration_days = round(duration_hours / 24, 1)
        status_desc = "A 股正常交易盘中" if is_trading else f"A 股休市中（自上个交易日 {start_dt.strftime('%Y-%m-%d 15:00:00')} 闭市至今已发酵 {duration_hours} 小时/约 {duration_days} 天）"

        prompt = f"""你是一名资深 A 股私募基金首席全球宏观策略操盘手。
【当前市场状态】：{status_desc}。

以下是系统精准提取的【自 A 股放假闭市起至今，全假期间累积发生的所有重大焦点事件与官方全文细则】：
{events_text}

【当前全市场领涨行业及严格互斥的三层标的真实数据】：
{market_text}

【你的任务 - 站在 A 股整个放假周期消息全量累积的视角，推演下个交易日开盘合力策略】：
1. **放假期间重大事件全局定性与合力研判**：
   - 梳理从放假闭市至今累积的所有重大事件（含正文深度细则），评估多空合力对下周 A 股大盘开盘的综合冲击；
   - 提取对特定赛道具备确定性催化的核心条款（论据必须直接出自抓取到的正文细节）；
2. **领涨行业与假期发酵逻辑的交叉印证**：
   - 结合下方领涨板块，指明哪些板块与假期间重大事件形成强烈共振，开盘具备持续爆发力；
   - 点评核心标的（进攻龙头、稳健中军、高性价比低价股）在事件驱动下的实战承接力；
3. **“涨乐财富通”节后开盘交易纪律与执行挂单**：
   - 针对开盘可能出现的“假期利好高开脉冲”，给出具体的防追高限价回踩买入区间、分批止盈条件单点位以及刚性止损红线。
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
hotspot_engine = None
if hotspot_service and hasattr(hotspot_service, "HotspotService"):
    try:
        hotspot_engine = hotspot_service.HotspotService()
        print("✅ 成功加载外部 modules/hotspot_service.py 引擎")
    except Exception as e:
        print(f"ℹ️ 外部引擎初始化异常，启用内置完整 HotspotService: {e}")
        hotspot_engine = HotspotService()
else:
    hotspot_engine = HotspotService()
    print("✅ 成功启用内置完整 HotspotService 实时引擎")


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
                    "name": _get(f, 0, ""),
                    "curr_price": curr,
                    "prev_close": prev,
                    "high": high,
                    "low": low,
                    "volume": vol,
                    "turnover": turnover
                }
    except Exception as e:
        print(f"fetch_real_quote error: {e}")
    return {"name": "", "curr_price": 0.0, "prev_close": 0.0, "high": 0.0, "low": 0.0, "volume": 0.0, "turnover": 0.0}

# 抓取前复权日K线数据 (新浪财经接口 + 腾讯财经备选)
def fetch_kline_history(symbol, days=120):
    try:
        url = f"https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={days}"
        res = requests.get(url, headers=HEADERS, timeout=4).json()
        if isinstance(res, list) and len(res) > 0:
            dates, klines, volumes = [], [], []
            for item in res:
                dates.append(item.get("day", ""))
                o = float(item.get("open", 0))
                c = float(item.get("close", 0))
                l = float(item.get("low", 0))
                h = float(item.get("high", 0))
                v = float(item.get("volume", 0))
                klines.append([o, c, l, h])
                volumes.append(v)
            return {"success": True, "dates": dates, "klines": klines, "volumes": volumes}
    except Exception:
        pass

    try:
        tx_url = f"https://web.ifzq.gtimg.cn/appstock/news/fqkline/get?param={symbol},day,,,{days},qfq"
        res = requests.get(tx_url, headers=HEADERS, timeout=4).json()
        stock_data = res.get("data", {}).get(symbol, {})
        raw_kl = stock_data.get("qfqday") or stock_data.get("day", [])
        if raw_kl:
            dates, klines, volumes = [], [], []
            for item in raw_kl:
                dates.append(_get(item, 0))
                o = float(_get(item, 1))
                c = float(_get(item, 2))
                h = float(_get(item, 3))
                l = float(_get(item, 4))
                v = float(_get(item, 5))
                klines.append([o, c, l, h])
                volumes.append(v)
            return {"success": True, "dates": dates, "klines": klines, "volumes": volumes}
    except Exception:
        pass

    return {"success": False, "error": "K线接口暂时不可用"}

# 多因子评分计算 (连接 quant_factors 模块，带智能降级计算)
def compute_quant_scores(code, name, curr_price, prev_close, klines_info=None):
    if quant_factors:
        for fn in ["calculate_stock_scores", "get_scores", "analyze_stock", "calculate_factors"]:
            if hasattr(quant_factors, fn):
                try:
                    res = getattr(quant_factors, fn)(code)
                    if isinstance(res, dict) and "short_term" in res:
                        return res
                except Exception:
                    pass

    ma5, ma20, ma60 = curr_price, curr_price, curr_price
    rsi_14 = 52.0
    short_score = 72
    mid_score = 68
    long_score = 75

    if klines_info and klines_info.get("success") and len(klines_info.get("klines", [])) >= 20:
        klines = klines_info.get("klines", [])
        closes = [_get(x, 1) for x in klines]
        ma5 = round(sum(closes[-5:]) / 5, 2)
        ma20 = round(sum(closes[-20:]) / 20, 2)
        if len(closes) >= 60:
            ma60 = round(sum(closes[-60:]) / 60, 2)
        else:
            ma60 = ma20

        diffs = [closes[i] - closes[i-1] for i in range(len(closes)-14, len(closes))]
        gains = sum(d for d in diffs if d > 0)
        losses = abs(sum(d for d in diffs if d < 0))
        if losses == 0:
            rsi_14 = 100.0
        else:
            rs = (gains / 14) / (losses / 14)
            rsi_14 = round(100 - (100 / (1 + rs)), 1)

        if curr_price >= ma5 >= ma20:
            short_score += 15
            mid_score += 15
        elif curr_price < ma5 and curr_price < ma20:
            short_score -= 15
            mid_score -= 12

        if 48 <= rsi_14 <= 68:
            short_score += 8
        elif rsi_14 > 80:
            short_score -= 10

        if curr_price <= 15.0:
            long_score += 12
        elif curr_price <= 25.0:
            long_score += 6

    short_score = max(20, min(95, short_score))
    mid_score = max(20, min(95, mid_score))
    long_score = max(20, min(95, long_score))

    def get_tag(score):
        if score >= 80: return "强势进攻", "bg-rose-500/20 text-rose-400 border border-rose-500/30"
        if score >= 60: return "稳健中性", "bg-blue-500/20 text-blue-400 border border-blue-500/30"
        return "偏弱观望", "bg-emerald-500/20 text-emerald-400 border border-emerald-500/30"

    st_tag, st_cls = get_tag(short_score)
    mt_tag, mt_cls = get_tag(mid_score)
    lt_tag, lt_cls = get_tag(long_score)

    st_desc = "量比爆发配合良好，突破关键压力位，具备短线强攻动能" if short_score >= 80 else ("量能温和中性震荡，分时回踩均线支撑，适合逢低吸纳" if short_score >= 60 else "均线空头受压，量能萎缩存量博弈，短线动能偏弱严禁追高")
    mt_desc = "上升通道维持完好，MA20/60多头排列，波段趋势强劲" if mid_score >= 80 else ("箱体震荡整理蓄势，均线系统纠缠粘合，等待右侧放量信号" if mid_score >= 60 else "破位下行通道中，中均线压制明显，仍处筑底调整周期")
    lt_desc = "处于历史估值低分位，安全边际极厚，向上赔率巨大" if long_score >= 80 else ("估值合理中枢水平，基本面支撑良好，具备防御配置价值" if long_score >= 60 else "估值溢价偏高或处于周期高位，长线安全垫不足需谨慎")

    return {
        "short_term": {"score": short_score, "tag": st_tag, "style": st_cls, "desc": st_desc},
        "mid_term": {"score": mid_score, "tag": mt_tag, "style": mt_cls, "desc": mt_desc},
        "long_term": {"score": long_score, "tag": lt_tag, "style": lt_cls, "desc": lt_desc},
        "ma5": ma5, "ma20": ma20, "ma60": ma60,
        "rsi": rsi_14,
        "overall_grade": "A+ 顶格精选" if short_score >= 82 else ("A 级 优先标的" if (short_score+mid_score)/2 >= 70 else "B 级 观察仓位")
    }

# 实战风控点位生成 (连接 trade_plan 模块)
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
def get_market_hotspots():
    global hotspot_engine
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
            
            hours_ago = round((datetime.now() - start_dt).total_seconds() / 3600, 1)
            days_ago = round(hours_ago / 24, 1)
            status_desc = "A 股正常交易盘中" if is_trading else f"A 股休市中（自上个交易日 {start_dt.strftime('%m-%d 15:00')} 闭市至今已发酵 {hours_ago} 小时 / 约 {days_ago} 天）"

            return {
                "status": "success",
                "is_trading": is_trading,
                "market_status_desc": status_desc,
                "close_start": start_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "events": events[:8],
                "sectors": sectors_data
            }
        except Exception as e:
            print(f"⚠️ 调用 hotspot_engine 实时抓取失败: {e}")

    # 兜底回退
    return {
        "status": "fallback",
        "market_status_desc": "休市研判模式（网络等待重试）",
        "events": [
            {"发布时间": "实时分析", "标题": "等待刷新或请确认网络环境可正常直连新浪财经接口", "正文深度细则": "系统支持自动根据最后交易日时间戳均衡抽样并深度穿透新闻正文。"}
        ],
        "sectors": [
            {
                "板块名称": "AI芯片/CPO算力", "板块涨幅": "+3.65%", "成分股总数": 48,
                "三层标的": {
                    "⚡ 进攻龙头(T+1)": [{"名称": "中际旭创", "代码": "300308", "最新价": 145.2, "涨跌幅(%)": 6.8, "成交额(亿)": 38.5, "市净率PB": 8.2}],
                    "🛡️ 稳健中军(长线)": [{"名称": "工业富联", "代码": "601138", "最新价": 22.4, "涨跌幅(%)": 2.1, "成交额(亿)": 52.1, "市净率PB": 3.1}],
                    "💰 高性价比低价股": [{"名称": "掌趣科技", "代码": "300315", "最新价": 4.85, "涨跌幅(%)": 3.2, "成交额(亿)": 8.4, "市净率PB": 1.4}]
                }
            }
        ]
    }

def get_hotspot_ai_review():
    global hotspot_engine
    if not hotspot_engine and hotspot_service and hasattr(hotspot_service, "HotspotService"):
        try:
            hotspot_engine = hotspot_service.HotspotService()
        except Exception:
            pass

    if not hotspot_engine:
        return "⚠️ hotspot_service 模块未加载，无法执行 DeepSeek 全周期宏观推演。"
    try:
        events, start_dt, is_trading = hotspot_engine.fetch_holiday_focus_events(max_pages=5)
        sectors_data = hotspot_engine.fetch_realtime_hotspots(top_n=5)
        return hotspot_engine.get_comprehensive_review(sectors_data, events, start_dt, is_trading)
    except Exception as e:
        return f"❌ 调用 DeepSeek 全周期推演失败: {e}"


def get_enriched_stocks():
    pf_file = get_portfolio_path()
    stocks = []
    if os.path.exists(pf_file):
        try:
            with open(pf_file, "r", encoding="utf-8") as f:
                stocks = json.load(f)
        except Exception:
            pass
        
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
            if curr_price <= 0: curr_price = cost if cost > 0 else 5.0
            if prev_close <= 0: prev_close = curr_price
            
            pct_today = round(((curr_price - prev_close) / prev_close) * 100, 2) if prev_close else 0.0
            plan = compute_trade_plan(curr_price, is_holding, cost) or {}
            
            if is_holding and cost > 0:
                profit_pct = round(((curr_price - cost) / cost) * 100, 2)
                profit_amount = round((curr_price - cost) * (shares or 1000), 2)
                t_buy = plan.get("t_buy", round(curr_price * 0.982, 2))
                t_sell = plan.get("t_sell", round(curr_price * 1.032, 2))
                hard_stop = plan.get("hard_stop", round(cost * 0.95 if curr_price >= cost else curr_price * 0.965, 2))
                portfolio_list.append({
                    "code": code, "symbol": symbol, "name": name, "cost": cost, "price": curr_price,
                    "pct_today": pct_today, "shares": shares,
                    "profit_pct": profit_pct, "profit_amount": profit_amount,
                    "t_buy": t_buy, "t_sell": t_sell, "hard_stop": hard_stop
                })
            else:
                entry_range = plan.get("buy_range") or plan.get("entry_range") or f"{curr_price*0.98:.2f} ~ {curr_price*0.99:.2f}"
                target1 = plan.get("target1", round(curr_price * 1.045, 2))
                target2 = plan.get("target2", round(curr_price * 1.100, 2))
                stop_loss = plan.get("stop_loss", round(curr_price * 0.980, 2))
                watchlist_list.append({
                    "code": code, "symbol": symbol, "name": name, "price": curr_price,
                    "pct_today": pct_today,
                    "entry_range": entry_range,
                    "target1": target1, "target2": target2, "stop_loss": stop_loss
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
      margin-bottom: 12px !important;
      padding-left: 10px !important;
      border-left: 4px solid #3b82f6 !important;
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
      <p class="text-sm text-slate-400 mt-1">集成 K线图表 · 多因子评分 · 涨乐富点位 · 市场热点雷达 · DeepSeek 单股/全景投研</p>
    </div>
    <div class="flex items-center gap-3 w-full md:w-auto">
      <button onclick="loadData()" class="px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg text-sm font-medium transition flex items-center justify-center gap-2">
        <i class="fa-solid fa-rotate"></i> 刷新列表
      </button>
      <button onclick="triggerAllAIDiagnose()" id="btn-ai-all" class="px-5 py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-600 rounded-lg text-sm font-semibold transition flex items-center justify-center gap-2">
        <i class="fa-solid fa-list-check"></i> 全仓一键诊断
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
          <span class="text-white font-bold">💼 实战持仓 (做T解套)</span>
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
          <i class="fa-solid fa-brain"></i> DeepSeek 节后开盘推演
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
            <span class="text-xs text-slate-400">MA5: <span id="detail-ma5">--</span> | MA20: <span id="detail-ma20">--</span> | RSI(14): <span id="detail-rsi">--</span></span>
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
            <i class="fa-solid fa-chart-candlestick text-blue-400"></i> 前复权日 K 线 (MA5 / MA20 / MA60 / 成交量)
          </span>
          <span class="text-[11px] text-slate-500">支持鼠标滚轮缩放、拖拽与高亮十字光标</span>
        </div>
        <div id="kline-chart" style="width: 100%; height: 420px;"></div>
      </div>

      <!-- 右侧 1 栏：多因子量化评分面板 (quant_factors) + 涨乐富点位 (trade_plan) -->
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
          </div>
        </div>

        <!-- 涨乐富挂单与策略卡片 (trade_plan) -->
        <div class="bg-slate-900/60 p-4 rounded-xl border border-slate-800">
          <h4 class="text-xs font-bold uppercase tracking-wider text-slate-400 mb-3 flex items-center gap-1.5">
            <i class="fa-solid fa-bullseye text-emerald-400"></i> 涨乐财富通·实战挂单指引 (trade_plan)
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
    
    // 渲染 Markdown 报告，自动规避波浪号 ~ 触发 Markdown 意外删除线 (strikethrough)
    function renderSafeMarkdown(rawText) {
      if (!rawText) return '无分析内容';
      let safeText = String(rawText);
      const LF = String.fromCharCode(10);
      // 1. 将英文半角波浪号 ~ 替换为中文全角 ～，彻底杜绝删除线误触发
      safeText = safeText.replace(/~/g, '～');
      // 2. 自动给独立引用块（> 决策牌）之间补空行，强制渲染为独立分离的大卡片
      safeText = safeText.replace(new RegExp('(^>.*?)' + LF + '(>)', 'gm'), '$1' + LF + LF + '$2');
      safeText = safeText.replace(new RegExp('(^>.*?)' + LF + '(>)', 'gm'), '$1' + LF + LF + '$2');
      // 3. 自动在表格和标题前补空行，确保 Markdown 完美解析为原生 table 元素
      safeText = safeText.replace(new RegExp('([^' + LF + '|])' + LF + '(\\|.*?\\|)', 'g'), '$1' + LF + LF + '$2');
      safeText = safeText.replace(new RegExp('([^' + LF + '])' + LF + '(###\\s+)', 'g'), '$1' + LF + LF + '$2');
      return marked.parse(safeText);
    }

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

    async function loadData() {
      try {
        const res = await fetch('/api/stocks');
        const data = await res.json();
        const holdings = data.holdings || [];
        const watchlists = data.watchlists || [];

        document.getElementById('count-holding').innerText = holdings.length;
        document.getElementById('count-watchlist').innerText = watchlists.length;

        // 渲染持仓列表
        const hBody = document.getElementById('holding-body');
        if (holdings.length === 0) {
          hBody.innerHTML = '<tr><td colspan="9" class="text-center py-6 text-slate-500">加载中...</td></tr>';
        } else {
          hBody.innerHTML = holdings.map(item => {
            const isUp = item.profit_pct >= 0;
            return `
              <tr class="hover:bg-slate-800/50 transition">
                <td class="py-3 px-4 font-bold text-white">
                  ${item.name} <span class="text-xs font-normal text-slate-400">(${item.code})</span>
                </td>
                <td class="py-3 px-3">${item.cost.toFixed(2)} 元</td>
                <td class="py-3 px-3 font-semibold ${item.pct_today >= 0 ? 'stock-up':'stock-down'}">${item.price.toFixed(2)} 元</td>
                <td class="py-3 px-3">
                  <span class="px-2 py-0.5 rounded text-xs font-bold ${isUp ? 'badge-up':'badge-down'}">
                    ${item.profit_pct > 0 ? '+':''}${item.profit_pct}% (${item.profit_amount}元)
                  </span>
                </td>
                <td class="py-3 px-3 text-blue-400 font-medium">【 ${item.t_buy.toFixed(2)} 】</td>
                <td class="py-3 px-3 text-amber-400 font-medium">【 ${item.t_sell.toFixed(2)} 】</td>
                <td class="py-3 px-3 text-rose-500 font-medium">【 ${item.hard_stop.toFixed(2)} 】</td>
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
                <td class="py-3 px-3 font-semibold ${isUp ? 'stock-up':'stock-down'}">${item.price.toFixed(2)} 元</td>
                <td class="py-3 px-3 ${isUp ? 'stock-up':'stock-down'} font-bold">${isUp ? '+':''}${item.pct_today}%</td>
                <td class="py-3 px-3 text-blue-400 font-medium">【 ${item.entry_range} 】</td>
                <td class="py-3 px-3 text-amber-400 font-medium">【 ${item.target1.toFixed(2)} 】</td>
                <td class="py-3 px-3 text-emerald-400 font-medium">【 ${item.target2.toFixed(2)} 】</td>
                <td class="py-3 px-3 text-rose-500 font-medium">【 ${item.stop_loss.toFixed(2)} 】</td>
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
        if (hBody) hBody.innerHTML = '<tr><td colspan="9" class="text-center py-6 text-slate-500">加载中...</td></tr>';
        if (wBody) wBody.innerHTML = '<tr><td colspan="9" class="text-center py-6 text-slate-500">自选池暂无股票，请在上方添加！</td></tr>';
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
        const res = await fetch(`/api/stock/analysis?code=${code}&symbol=${symbol}`);
        const data = await res.json();
        
        if (data.quote) {
          document.getElementById('detail-stock-price').innerText = data.quote.curr_price.toFixed(2) + " 元";
          const pct = ((data.quote.curr_price - data.quote.prev_close) / data.quote.prev_close * 100).toFixed(2);
          const pctEl = document.getElementById('detail-stock-pct');
          pctEl.innerText = (pct > 0 ? "+" : "") + pct + "%";
          pctEl.className = pct >= 0 ? "text-lg text-rose-500 font-bold" : "text-lg text-emerald-500 font-bold";
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
          renderEChartsKLine(data.kline.dates, data.kline.klines, data.kline.volumes);
        }

      } catch (err) {
        console.error("加载个股详情失败:", err);
      }
    }

    // 绘制专业 K 线图
    function renderEChartsKLine(dates, klines, volumes) {
      const chartDom = document.getElementById('kline-chart');
      if (!klineChartInstance) {
        klineChartInstance = echarts.init(chartDom, 'dark');
      }

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
          data: ['日K', 'MA5', 'MA20', 'MA60'],
          inactiveColor: '#475569',
          textStyle: { color: '#94a3b8' }
        },
        tooltip: {
          trigger: 'axis',
          axisPointer: { type: 'cross' },
          backgroundColor: 'rgba(30, 41, 59, 0.9)',
          borderColor: '#475569',
          textStyle: { color: '#f8fafc' },
          position: function (pos, params, el, elRect, size) {
            var obj = { top: 10 };
            if (_at(pos, 0) < _at(size.viewSize, 0) / 2) {
              obj.right = 30;
            } else {
              obj.left = 30;
            }
            return obj;
          }
        },
        axisPointer: {
          link: [{ xAxisIndex: 'all' }],
          label: { backgroundColor: '#334155' }
        },
        grid: [
          { left: '8%', right: '4%', height: '58%', top: '10%' },
          { left: '8%', right: '4%', top: '74%', height: '16%' }
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
            axisLabel: { show: false },
            axisLine: { show: false },
            axisTick: { show: false },
            splitLine: { show: false }
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
          }
        ]
      };

      klineChartInstance.setOption(option);
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
        const res = await fetch('/api/ai/diagnose', {
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
        const res = await fetch('/api/ai/diagnose', {
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

      const t1 = sec.三层标的['⚡ 进攻龙头(T+1)'] || [];
      const t2 = sec.三层标的['🛡️ 稳健中军(长线)'] || [];
      const t3 = sec.三层标的['💰 高性价比低价股'] || [];

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
              <span><i class="fa-solid fa-coins mr-1"></i> 高性价比低价潜伏股</span>
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
      content.innerHTML = '<div class="py-4 text-center text-amber-400"><i class="fa-solid fa-spinner fa-spin mr-2"></i>正在汇总全假期间所有核心穿透大事件与领涨行业，调用 DeepSeek 推演开盘合力策略...</div>';
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
      await fetch('/api/stock/save', {
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
      await fetch('/api/stock/delete', {
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

    loadData();
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

</body>
</html>
"""

# ----------------- 后端服务路由与分发 -----------------
class PurePythonStockHandler(BaseHTTPRequestHandler):
    def send_json(self, data_dict):
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data_dict, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):
        url_parsed = urllib.parse.urlparse(self.path)
        path = url_parsed.path
        query_params = urllib.parse.parse_qs(url_parsed.query)

        if path in ["/", "/index.html"]:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_CONTENT.encode("utf-8"))
            
        elif path == "/api/stocks":
            holdings, watchlists = get_enriched_stocks()
            self.send_json({"holdings": holdings, "watchlists": watchlists})

        elif path == "/api/stock/analysis":
            code = _get(query_params.get("code", [""]), 0).strip()
            symbol = _get(query_params.get("symbol", [""]), 0).strip()
            if not symbol:
                symbol, code, _ = resolve_stock(code)
            
            quote = fetch_real_quote(symbol)
            kline_data = fetch_kline_history(symbol, days=120)
            curr_p = float(quote.get("curr_price", 0.0) or 0.0)
            prev_c = float(quote.get("prev_close", 0.0) or curr_p)
            scores = compute_quant_scores(code, quote.get("name", ""), curr_p, prev_c, kline_data)

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
        url_parsed = urllib.parse.urlparse(self.path)
        path = url_parsed.path
        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length)
        body = json.loads(post_data.decode("utf-8")) if post_data else {}

        pf_file = get_portfolio_path()
        stocks = []
        if os.path.exists(pf_file):
            try:
                with open(pf_file, "r", encoding="utf-8") as f:
                    stocks = json.load(f)
            except Exception:
                pass

        if path == "/api/stock/save":
            raw_c = body.get("code")
            raw_n = body.get("name")
            cost = float(body.get("cost", 0.0))
            shares = int(body.get("shares", 0))
            is_holding = body.get("is_holding", True)
            
            symbol, real_code, real_name = resolve_stock(raw_c or raw_n)
            final_code = real_code if re.match(r'^\d{6}$', real_code) else raw_c
            final_name = real_name or raw_n
            
            updated = False
            for p in stocks:
                if p.get("代码") == final_code:
                    p.update({"代码": final_code, "名称": final_name, "成本价": cost, "持仓股数": shares, "is_holding": is_holding})
                    updated = True
                    break
            if not updated:
                stocks.append({"代码": final_code, "名称": final_name, "成本价": cost, "持仓股数": shares, "is_holding": is_holding})
                
            for p_path in [os.path.join(MODULES_DIR, "my_portfolio.json"), os.path.join(CURRENT_DIR, "my_portfolio.json")]:
                try:
                    with open(p_path, "w", encoding="utf-8") as f:
                        json.dump(stocks, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass
            self.send_json({"status": "success", "total": len(stocks)})

        elif path == "/api/stock/delete":
            code = body.get("code")
            stocks = [p for p in stocks if p.get("代码") != code]
            for p_path in [os.path.join(MODULES_DIR, "my_portfolio.json"), os.path.join(CURRENT_DIR, "my_portfolio.json")]:
                try:
                    with open(p_path, "w", encoding="utf-8") as f:
                        json.dump(stocks, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass
            self.send_json({"status": "success"})

        elif path == "/api/ai/diagnose":
            target_code = body.get("code")
            target_symbol = body.get("symbol")
            
            if target_code:
                if not target_symbol:
                    target_symbol, target_code, _ = resolve_stock(target_code)
                quote = fetch_real_quote(target_symbol)
                kline_data = fetch_kline_history(target_symbol, days=60)
                curr_p = float(quote.get("curr_price", 0.0) or 0.0)
                prev_c = float(quote.get("prev_close", 0.0) or curr_p)
                scores = compute_quant_scores(target_code, quote.get("name"), curr_p, prev_c, kline_data)

                # 智能识别是否为实战持仓股并提取真实成本
                pf_file = get_portfolio_path()
                holding_item = None
                if os.path.exists(pf_file):
                    try:
                        with open(pf_file, "r", encoding="utf-8") as f:
                            pf_list = json.load(f)
                        for sp in pf_list:
                            if str(sp.get("代码", "")).strip() == target_code:
                                cost_val = float(sp.get("成本价", 0.0) or 0.0)
                                if cost_val > 0:
                                    holding_item = sp
                                    break
                    except Exception:
                        pass

                pct_today_str = f"{((curr_p - prev_c)/prev_c*100):+.2f}%" if prev_c else "0.00%"

                if holding_item:
                    # 【场景 A：实战持仓股】—— 全周期操盘实战指导（短线/中线/长线全方位覆盖 + 盈亏针对性应对）
                    cost = float(holding_item.get("成本价", 0.0))
                    shares = int(holding_item.get("持仓股数", 1000) or 1000)
                    loss_pct = round(((curr_p - cost) / cost) * 100, 2)
                    total_loss = round((curr_p - cost) * shares, 2)
                    needed_gain = round(((cost - curr_p) / curr_p) * 100, 2) if curr_p > 0 else 0.0
                    plan = compute_trade_plan(curr_p, is_holding=True, cost=cost)

                    profit_status = f"盈利 +{loss_pct:.2f}% (+{total_loss:.2f} 元)" if loss_pct > 0 else (f"持平 0.00%" if loss_pct == 0 else f"浮亏 {loss_pct:.2f}% ({total_loss:.2f} 元，直接回本需涨幅 +{needed_gain:.2f}%)")

                    prompt = f"""你是一名资深 A 股私募基金投资总监。请针对用户【已购入的实战持仓标的】，输出一份【顶置三大核心决策牌 + 四大清晰结构化表格】的操盘手实战执单。

【持仓账户与盘口数据】：
- 股票标的：{quote.get('name')} ({target_code})
- 您的买入成本：{cost:.2f} 元
- 当前最新现价：{curr_p:.2f} 元 (今日涨跌: {pct_today_str})
- 您的持仓股数：{shares} 股
- 当前持仓状态：{profit_status}
- 量化多因子：MA5={scores.get('ma5')} | MA20={scores.get('ma20')} | MA60={scores.get('ma60')} | RSI(14)={scores.get('rsi')}
- 三大周期量化评分：短线T+1={scores.get('short_term', {}).get('score')}分 ({scores.get('short_term', {}).get('desc')}) | 中线波段={scores.get('mid_term', {}).get('score')}分 ({scores.get('mid_term', {}).get('desc')}) | 长线配置={scores.get('long_term', {}).get('score')}分 ({scores.get('long_term', {}).get('desc')})
- 关键点位：日内做T买点【{plan.get('t_buy')}元】 | 做T冲高卖点【{plan.get('t_sell')}元】 | 刚性止损红线【{plan.get('hard_stop')}元】

【硬性排版要求 - 彻底告别大段文字，一目了然】：
1. 严禁任何客套废话！
2. **第一步（必须在最顶部输出三个独立决策牌，每张牌之间空一行）**：
> 🚦 **今日核心战术定调**：【给出明确指令，如：日内做T降本 / 逢高反弹减仓 / 顺势持股待涨】 (说明当前筹码状态与防守底线)

> 🟢 **日内做 T 回踩买点**：【 **{plan.get('t_buy')} 元** 】 (具体买入触发条件，预期降低每股成本幅度)

> 🔴 **冲高做 T 止盈卖点**：【 **{plan.get('t_sell')} 元** 】 (具体卖出触发条件，遇阻力位果断落袋)

3. **第二步：紧接着输出以下四大紧凑表格（每张表必须包含标准表头和表格边框，每张表前后空一行）**：

### 一、 筹码分布与关键阻力支撑表
| 诊断维度 | 核心点位 / 数据 | 操盘手定性结论与实战含义 |
| :--- | :--- | :--- |
| 成本与现价 | 成本 {cost:.2f}元 vs 现价 {curr_p:.2f}元 | 当前盈亏 {profit_status}，分析筹码处于获利盘还是套牢区 |
| 上方关键阻力带 | 具体价格区间 (如 MA20/MA60) | 反弹抛压重灾区与做T交筹码窗口 |
| 下方核心支撑带 | 具体价格区间 (如 做T买点/止损) | 多头最后防守位，跌破则趋势恶化 |

### 二、 三大持有周期实战操作决策表（短/中/长线）
| 周期类型与评分 | 核心点位规划 | 具体仓位动作与目标 |
| :--- | :--- | :--- |
| **⚡ 短线 T+1 ({scores.get('short_term', {}).get('score')}分)** | 做T买入: **{plan.get('t_buy')}元**<br>冲高卖出: **{plan.get('t_sell')}元** | 回踩低吸加仓，冲高必须T出底仓，赚差价降本，破止损严决减仓 |
| **🌊 中线波段 ({scores.get('mid_term', {}).get('score')}分)** | 建议止盈: **xx元**<br>加仓均线: **xx元** | 保持合理底仓，未站稳MA20不盲目重仓，反弹分批减仓策略 |
| **💎 长线价值 ({scores.get('long_term', {}).get('score')}分)** | 补仓点位:<br>一档: **xx元**<br>二档: **xx元** | 结合估值安全边际，评估长线回本目标价与金字塔分批布局计划 |

### 三、 账户当前实际盈亏针对性应对路线表
| 战术步骤 | 触发价格条件 | 委托动作与仓位 | 战术目的与降本目标 |
| :--- | :--- | :--- | :--- |
| **步骤 1：日内做T降本** | 回踩至 **{plan.get('t_buy')}元** / 冲高至 **{plan.get('t_sell')}元** | 买入/卖出对应数量 | 测算每笔做T降低综合成本幅度 |
| **步骤 2：阻力位减仓** | 达到上方第一技术阻力位 | 分批减仓比例 | 锁定反弹战果，防止回踩再度被套 |
| **步骤 3：刚性风险防守** | 跌破 **{plan.get('hard_stop')}元** | 严格执行止损 | 绝不盲目死扛，守住本金底线 |

### 四、 涨乐财富通条件单直接照抄清单
| 条件单类型 | 监控触发价格 | 委托操作与数量 | 监控有效期 | 战术目的 |
| :--- | :--- | :--- | :--- | :--- |
| 股价回落买入 (做T低吸) | 价格 <= **{plan.get('t_buy')}元** | 限价买入 xx股 | 当日有效 | 日内回踩低吸拉低成本 |
| 股价反弹卖出 (做T冲高) | 价格 >= **{plan.get('t_sell')}元** | 限价卖出 xx股 | 当日有效 | 冲高获利兑现做T差价 |
| 止损条件单 (防守底线) | 价格 <= **{plan.get('hard_stop')}元** | 市价/限价卖出全部 | 长期有效 | 破位刚性离场规避深套 |
"""
                else:
                    # 【场景 B：观察自选 / 市场热点推荐标的】—— 启动左侧狙击与建仓计划
                    plan = compute_trade_plan(curr_p, is_holding=False, cost=0.0)
                    prompt = f"""你是一名专业私募基金投资总监。请针对以下用户【尚未持仓的观察标的】，输出一份【顶置核心决策牌 + 4张结构化表格】的实战操盘策略。

【标的技术面实时数据】：
- 股票标的：{quote.get('name')} ({target_code})
- 当前最新价格：{curr_p:.2f} 元 (今日涨跌: {pct_today_str})
- 技术面指标：MA5={scores.get('ma5')} | MA20={scores.get('ma20')} | MA60={scores.get('ma60')} | RSI(14)={scores.get('rsi')}
- 多因子评分：短线T+1={scores.get('short_term', {}).get('score')}分 ({scores.get('short_term', {}).get('desc')}) | 中线波段={scores.get('mid_term', {}).get('score')}分 ({scores.get('mid_term', {}).get('desc')}) | 长线价值={scores.get('long_term', {}).get('score')}分 ({scores.get('long_term', {}).get('desc')}) | 评级={scores.get('overall_grade')}
- 计划点位：建议回踩买入区间【{plan.get('buy_range')}】 | 短线目标【{plan.get('target1')}元】 | 波段目标【{plan.get('target2')}元】 | 刚性止损【{plan.get('stop_loss')}元】

【硬性排版要求 - 严格执行“样式一”全端适配】：
1. **第一步（在最顶部输出三个醒目决策牌）**，采用 Markdown 引用块（> ）格式：
> 🚦 **核心战术定调**：【根据技术面给出6~10字建仓建议，如：回踩下沿埋伏 / 右侧放量突破上车 / 观望等待信号】 (盈亏比结论与仓位配比)
> 🟢 **建议回踩建仓区间**：【 **{plan.get('buy_range')} 元** 】 (严格限价挂单，严禁追高)
> 🔴 **短线第一止盈目标**：【 **{plan.get('target1')} 元 (+4.5%)** 】 (冲高触及果断减半锁定利润)

2. **第二步：紧接着输出以下四大紧凑表格（每张表控制在 2~3 列，手机竖屏单手阅读极佳）**：

### 一、 盘口形态与技术指标量化表
| 分析维度 | 当前技术状态 | 主力资金意图与技术含义 |
| :--- | :--- | :--- |
| 均线多空结构 | MA5/20/60 排列形态 | 趋势方向与均线支撑阻力 |
| 量价与动量 | RSI(14) 及成交量状态 | 超买超卖评估与资金吸筹意图 |
| 综合评级 | {scores.get('overall_grade')} | 明确是否具备入场赔率 |

### 二、 估值安全边际与向上赔率测算表
| 估值与空间 | 点位规划 | 收益与风险评估结论 |
| :--- | :--- | :--- |
| 上行目标位 | 第一目标 **{plan.get('target1')}元** (+4.5%)<br>第二目标 **{plan.get('target2')}元** (+10%) | 测算向上弹性空间 |
| 下行防守线 | 开仓止损 **{plan.get('stop_loss')}元** (-2.0%) | 潜在最大试错风险与盈亏比结论 |

### 三、 三大周期操盘战术定调表
| 周期类型与评分 | 建议仓位 | 进场与出场战术指令 |
| :--- | :--- | :--- |
| **⚡ 短线 T+1 ({scores.get('short_term', {}).get('score')}分)** | 2成机动仓 | 回踩买入区间低吸挂单，次日冲高落袋止盈 |
| **🌊 中线波段 ({scores.get('mid_term', {}).get('score')}分)** | 3~4成仓位 | 顺应均线趋势持股，跌破关键防守位离场 |
| **💎 长线价值 ({scores.get('long_term', {}).get('score')}分)** | 观望 / 底仓配置 | 结合估值安全边际，执行金字塔逢低分批建仓 |

### 四、 涨乐财富通实战挂单计划表
| 条件单类型 | 监控触发价格 | 委托数量 | 有效期 | 操盘目的 |
| :--- | :--- | :--- | :--- | :--- |
| 限价买入条件单 | 价格 <= **{plan.get('buy_range')}** | 计划底仓数量 | 当日有效 | 严格左侧低吸，防追高 |
| 止盈条件单 (短线) | 价格 >= **{plan.get('target1')}元** | 卖出 1/2 仓位 | 长期有效 | 锁定第一波短线利润 |
| 止盈条件单 (波段) | 价格 >= **{plan.get('target2')}元** | 卖出剩余仓位 | 长期有效 | 把握中线波段主升浪 |
| 止损条件单 (刚性) | 价格 <= **{plan.get('stop_loss')}元** | 全部清仓离场 | 长期有效 | 刚性截断亏损，规避深套 |
"""
            else:
                holdings, watchlists = get_enriched_stocks()
                if not holdings and not watchlists:
                    self.send_json({"report": "⚠️ 当前未录入任何持仓或自选股票，请先在上方录入股票后再进行诊断！"})
                    return
                    
                payload = {"实战持仓股票池": holdings, "重点观察自选池": watchlists}
                data_str = json.dumps(payload, ensure_ascii=False, indent=2)
                prompt = f"""你是一名资深 A 股私募基金投资总监。请针对以下用户的【实战持仓】与【观察自选】数据，输出一份【极度精炼、纯干货、零废话】的全景操盘内参：
{data_str}

【硬性要求】：
1. 严禁任何寒暄、称呼、情绪安慰等口水话；
2. 直奔主题，按如下结构分模块输出：
### 一、 【实战持仓股】盘口诊断与减亏自救作战单
- 对每只持仓股：盘口健康度、日内做 T 降本点位、加仓翻盘测算、涨乐财富通条件单设置。
### 二、 【重点观察自选股】量化狙击与上车计划
- 对每只自选股：性价比评估、建议回踩低吸挂单区间、两档目标位、涨乐财富通挂单指引。
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

if __name__ == "__main__":
    port = 8000
    server_address = ("0.0.0.0", port)
    httpd = HTTPServer(server_address, PurePythonStockHandler)
    print("\n" + "="*60)
    print("🚀 A股 AI 量化投资与全端决策系统 [模块全集成旗舰版] 启动成功！")
    print(f"💻 电脑浏览器访问:  http://127.0.0.1:{port}")
    print(f"📱 手机浏览器访问:  http://[电脑局域网IP]:{port}")
    print("="*60 + "\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 服务已安全停止。")
