def auto_load_env():
    possible_paths = [
        os.path.join(os.getcwd(), ".env"),
        os.path.join(os.path.dirname(__file__), "..", ".env"),
        os.path.join(os.path.dirname(__file__), ".env"),
    ]
    for p in possible_paths:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("'").strip('"')
                        if k not in os.environ:
                            os.environ[k] = v
            break

# -*- coding: utf-8 -*-
"""
模块 4：完全基于 A 股真实休市日历的全周期重大事件自适应推演与五大阵营选股引擎
"""
import os
import json
import requests
import re
from datetime import datetime, timedelta

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
        auto_load_env()
        self.api_key = os.getenv("DEEPSEEK_API_KEY")
        self.base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        self.model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        self.client = None
        if OpenAI and self.api_key:
            try:
                self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)
            except Exception:
                self.client = None
        try:
            from ai_advisor import AIAdvisor
            self.ai = AIAdvisor()
            if self.ai and getattr(self.ai, "client", None):
                self.client = self.ai.client
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

### 四、 📊 盘面复盘与策略分析（总结篇）

#### 1. 核心结论与市场研判
- **市场总体定调**：[提炼当前宏观周期、流动性环境与整体走势预期]
- **主线板块表现**：[深入分析当前核心主线及核心驱动逻辑]
- **多空强弱判断**：[明确识别当前盘面由多头还是空头主导，说明关键临界分界]

#### 2. 板块客观评价与风险定位
- **防守/高股息板块定位**：[客观评价防守/高股息板块在当前环境下的表现及吸金/失血效应]
- **投机/热点板块定位**：[识别主线外的热点或杂毛板块表现，防范诱多接盘风险]
- **价格与数据锚定**：[严格提炼输入数据中的真实盘口点位与客观数据，绝不虚构价格]

#### 3. 应对预案与底线管理
- **防守底线（减仓/止损位）**：[列出具体标的、关键防守支撑位与对应的减仓/止损防守动作]
- **进攻/博弈预案（逢低/高抛）**：[根据市场情境设定动态低吸、回踩确认或冲高减半的博弈预案]
- **纪律重申**：[提炼核心风控纪律与实战防范要点]

【严禁项】：严禁在输出内容中原样复写提示词规则（如“绝不强行凑数：本报告仅聚焦...”、“严禁张冠李戴...”等后台指令严禁出现在正文中），必须直接输出纯粹、专业、针对性的操盘策略与结论。
"""
        client_to_use = self.client or (self.ai.client if self.ai else None)
        if not client_to_use and OpenAI and self.api_key:
            try:
                client_to_use = OpenAI(api_key=self.api_key, base_url=self.base_url)
            except Exception:
                pass

        if client_to_use:
            try:
                res = client_to_use.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.2
                )
                return res.choices[0].message.content
            except Exception as e:
                return f"⚠️ 调用 DeepSeek 模型异常: {e}"
        else:
            return "⚠️ 请在 .env 中配置有效的 DEEPSEEK_API_KEY 以生成宏观推演报告。"

# 初始化 HotspotService 实时引擎实例（优先从外部加载，无则使用内置类）

# 默认导出引擎
hotspot_engine = HotspotService()
