import requests
import json
import re
import sys
import os
from datetime import datetime

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from ai_advisor import AIAdvisor

def safe_get(lst, idx, default=""):
    try: return lst[idx] if len(lst) > idx else default
    except: return default

def safe_float(lst, idx, default=0.0):
    try:
        val = lst[idx] if len(lst) > idx else ""
        return float(val) if val else default
    except: return default

class HotspotService:
    """
    模块 4：完全基于 A 股真实休市日历的全周期重大事件自适应推演引擎
    （支持 1~10 天任意长假，时间戳动态穿透，按天均衡采样）
    """
    def __init__(self):
        self.ai = AIAdvisor()
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://finance.sina.com.cn/"
        }

    # 1. 动态获取 A 股最后交易日闭市起点（直连上证指数底表）
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

    # 2. 深度穿透抓取网页正文详情
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

    # 3. 自适应抓取【自放假闭市起至今】全周期事件（最长可覆盖 10 天超长假期）
    def fetch_holiday_focus_events(self, max_pages=10) -> tuple:
        start_dt, is_trading = self.get_a_share_market_close_time()
        start_ts = int(start_dt.timestamp())
        
        all_raw_events = []
        seen_titles = set()
        
        # 自适应翻页，只要还没到放假起点，就一直拉取（最高上限 10 页，500 条大事件，足够支撑 10 天春节/国庆长假）
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
                    # 严格截止：必须是放假/闭市之后的新闻
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

        # 长假智能均衡抽样：确保假期内“每一天”的核心大事都在列表里，避免被最后一天刷屏
        if len(all_raw_events) > 15:
            grouped = {}
            for ev in all_raw_events:
                d = ev["发布日期"]
                if d not in grouped: grouped[d] = []
                grouped[d].append(ev)
            
            selected_events = []
            for d in sorted(grouped.keys()):
                # 每天选取最重要的前 2~3 条
                selected_events.extend(grouped[d][:3])
            final_events = selected_events
        else:
            final_events = all_raw_events

        # 对筛选出的重点大事件，自动下钻穿透正文细则（最多穿透 4 条最重要文章）
        for i in range(min(4, len(final_events))):
            link = final_events[i].get("url")
            if link:
                detail = self.fetch_article_detail(link)
                final_events[i]["正文深度细则"] = detail if detail else final_events[i]["摘要"]
        
        return final_events, start_dt, is_trading

    # 4. 动态抓取全市场领涨行业与三层互斥标的
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
                        "node": safe_get(p, 0), "名称": safe_get(p, 1),
                        "涨跌幅": safe_float(p, 4), "成交额(亿)": round(safe_float(p, 6) / 1e8, 1)
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
                    "板块名称": clean_name, "板块涨幅": f"{sec['涨跌幅']}%",
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
                        "最新价": price,
                        "涨跌幅(%)": float(it.get("changepercent", 0)),
                        "成交额(亿)": round(float(it.get("amount", 0)) / 1e8, 1),
                        "市净率PB": float(it.get("pb", 99.0)) if it.get("pb") else 99.0
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

    # 5. DeepSeek 结合放假全周期事件推演
    def get_comprehensive_review(self, market_data: list, holiday_events: list, start_dt: datetime, is_trading: bool) -> str:
        events_text = json.dumps(holiday_events, ensure_ascii=False, indent=2)
        market_text = json.dumps(market_data, ensure_ascii=False, indent=2)
        duration_hours = round((datetime.now() - start_dt).total_seconds() / 3600, 1)
        duration_days = round(duration_hours / 24, 1)
        status_desc = "A 股正常交易盘中" if is_trading else f"A 股休市中（自上个交易日 {start_dt.strftime('%Y-%m-%d 15:00:00')} 闭市至今已发酵 {duration_hours} 小时/约 {duration_days} 天）"

        prompt = f"""
你是一名资深 A 股私募基金首席全球宏观策略操盘手。
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
        res = self.ai.client.chat.completions.create(
            model=self.ai.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2
        )
        return res.choices[0].message.content

# ----------------- 独立测试 -----------------
if __name__ == "__main__":
    print(">>> 正在启动 [模块 4：A 股真实休市日历全周期推演引擎 (支持 1~10 天超长假)]...")
    service = HotspotService()

    # 1. 自适应拉取整个放假期间的全部重大事件
    holiday_events, start_dt, is_trading = service.fetch_holiday_focus_events(max_pages=10)
    hours_ago = round((datetime.now() - start_dt).total_seconds() / 3600, 1)
    days_ago = round(hours_ago / 24, 1)
    
    print("="*65)
    print(f"📊 【A 股真实休市时间轴已动态对齐】")
    print(f"   • 本轮休市起点: {start_dt.strftime('%Y-%m-%d 15:00:00')}")
    print(f"   • 当前休市时长: 已发酵 {hours_ago} 小时（约 {days_ago} 天）")
    print(f"   • 假期动态捕获: 涵盖 {len(holiday_events)} 条核心大事件")
    print("="*65)

    for idx, ev in enumerate(holiday_events[:6], 1):
        print(f"  [{idx}] ({ev['发布时间']}) {ev['标题']}")
        if ev.get("正文深度细则") and len(ev["正文深度细则"]) > 100:
            print(f"      ↳ [已穿透正文细则]: {ev['正文深度细刷'][:100]}..." if '正文深度细刷' in ev else f"      ↳ [已穿透正文细则]: {ev['正文深度细则'][:100]}...")

    # 2. 动态扫描板块
    print("\n📊 正在动态扫描全市场领涨行业与三层互斥标的...")
    market_data = service.fetch_realtime_hotspots(top_n=5)
    print(f"✅ 成功扫描 Top {len(market_data)} 大行业板块（互斥去重）")

    # 3. DeepSeek 全周期合力推演
    print("\n🤖 正在调用 DeepSeek 结合【整个放假周期全部累积大事件】推演节后开盘合力...\n")
    review = service.get_comprehensive_review(market_data, holiday_events, start_dt, is_trading)
    print(review)