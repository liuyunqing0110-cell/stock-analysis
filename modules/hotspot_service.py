# -*- coding: utf-8 -*-
"""
模块 4：完全基于 A 股真实休市日历的全周期重大事件自适应推演与五大阵营选股引擎
"""
import os
import json
import requests
import re
from datetime import datetime

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

def _get(seq, idx, default=""):
    try:
        return seq[idx]
    except Exception:
        return default

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
                stocks_tiered, count = self.fetch_sector_constituents(sec["node"], sec["名称"])
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

    def fetch_sector_constituents(self, node_code: str, sec_name: str = "") -> tuple:
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

            # 💡【五大核心投资阵营差异化选股体系】
            sname = sec_name or ""
            if any(k in sname for k in ["芯片", "半导体", "算力", "软件", "AI", "通信", "电子", "元器件", "IT", "计算机", "军工", "光伏", "电池", "机械", "汽车"]):
                camp_type = "TECH"
                t3_title = "🚀 高弹性成长潜伏"
                # 科技股重在弹性与量能，放宽PB限制
                cheaps = sorted([s for s in rem if 5.0 <= s["最新价"] <= 35.0], key=lambda x: (-x["成交额(亿)"], abs(x["涨跌幅(%)"])))[:5]
            elif any(k in sname for k in ["酒", "食品", "饮料", "家电", "医药", "生物", "百货", "旅游", "酒店", "商业", "零售"]):
                camp_type = "CONSUMER"
                t3_title = "🍷 精选特色品牌(高弹性)"
                # 消费股剔除跨界杂质，优选 1.0 <= PB <= 4.0 的特色名优品牌，不单纯图破净
                consumer_stocks = []
                for s in rem:
                    n = s.get("名称", "")
                    if "酒" in sname and not any(k in n for k in ["酒", "曲", "葡", "酿", "特", "茅", "粮", "汾", "窖", "顺鑫", "啤酒", "红酒", "黄酒", "春", "贡"]):
                        continue # 过滤非酒杂质股
                    consumer_stocks.append(s)
                if not consumer_stocks: consumer_stocks = rem
                cheaps = sorted([s for s in consumer_stocks if 3.0 <= s["最新价"] <= 25.0 and s["市净率PB"] >= 0.9], key=lambda x: (abs(x["市净率PB"] - 2.0), -x["成交额(亿)"]))[:5]
            elif any(k in sname for k in ["石油", "煤炭", "有色", "钢铁", "化工", "材料", "矿", "海运", "航运"]):
                camp_type = "CYCLICAL"
                t3_title = "💎 周期大底重置资产"
                # 周期股看重重置成本与破净安全垫
                cheaps = sorted([s for s in rem if s["市净率PB"] <= 1.2], key=lambda x: (x["市净率PB"], -x["成交额(亿)"]))[:5]
            elif any(k in sname for k in ["银行", "证券", "券商", "保险", "金融"]):
                camp_type = "FINANCIALS"
                t3_title = "🏛️ 低估值高股息金"
                # 金融股看深度破净与高分红
                cheaps = sorted([s for s in rem if s["市净率PB"] <= 0.9], key=lambda x: (x["市净率PB"], -x["成交额(亿)"]))[:5]
            elif any(k in sname for k in ["高速", "公路", "电力", "水务", "燃气", "港口", "环保", "交通"]):
                camp_type = "UTILITY"
                t3_title = "💰 稳健高股息潜伏"
                # 公用事业看破净安全垫与充沛现金流
                cheaps = sorted([s for s in rem if s["市净率PB"] <= 1.1], key=lambda x: (x["市净率PB"], -x["成交额(亿)"]))[:5]
            else:
                camp_type = "GENERAL"
                t3_title = "💰 高性价比低价潜伏股"
                cheaps = sorted([s for s in rem if 3.0 <= s["最新价"] <= 20.0], key=lambda x: (x["市净率PB"], -x["涨跌幅(%)"]))[:5]

            if not cheaps:
                cheaps = sorted(rem, key=lambda x: x["最新价"])[:5]

            return {"⚡ 进攻龙头(T+1)": leaders, "🛡️ 稳健中军(长线)": cores, t3_title: cheaps}, len(cleaned)
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
3. **节后开盘交易纪律与执行挂单**：
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

# 默认导出引擎
hotspot_engine = HotspotService()
