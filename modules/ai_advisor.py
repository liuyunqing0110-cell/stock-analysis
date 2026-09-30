# -*- coding: utf-8 -*-
"""
AI 操盘顾问模块 (modules/ai_advisor.py)
专职负责：
- 单股操盘实战指导（步骤一三大精炼独立决策牌，步骤二至步骤五结构化紧凑表格，步骤六一页纸执单总结）
- 严防点位倒挂（亏损标的风控防线严格位于现价下方，与持仓列表刚性止损线100%对齐）
- 标的立体多维画像驱动，操盘策略严密自洽
"""

import os
import re
from datetime import datetime

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

def _get(seq, idx, default=""):
    try:
        return seq[idx] if len(seq) > idx else default
    except Exception:
        return default

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

class AIAdvisor:
    def __init__(self, api_key=None, base_url=None, model=None):
        auto_load_env()
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        self.base_url = base_url or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        self.model = model or os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        self.client = None
        if OpenAI and self.api_key:
            try:
                self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)
            except Exception:
                self.client = None

    def is_configured(self) -> bool:
        return bool(self.api_key and len(self.api_key.strip()) > 5)

    def diagnose_single_stock(self, target_code: str, quote: dict, scores: dict, plan: dict, holding_item: dict = None) -> str:
        if not self.is_configured():
            return "⚠️ 请先在 .env 中配置有效的 DEEPSEEK_API_KEY。"

        stock_name = quote.get("name", "")
        curr_p = float(quote.get("curr_price", 0.0) or 0.0)
        prev_c = float(quote.get("prev_close", 0.0) or curr_p)
        pct_today_str = f"{((curr_p - prev_c)/prev_c*100):+.2f}%" if prev_c else "0.00%"

        pe_val = scores.get('pe') if scores.get('pe') is not None else quote.get('pe', '--')
        pb_val = scores.get('pb') if scores.get('pb') is not None else quote.get('pb', '--')
        mv_val = scores.get('total_mv') if scores.get('total_mv') is not None else quote.get('total_mv', '--')

        pe_str = f"{pe_val}" if pe_val != '--' else "N/A"
        pb_str = f"{pb_val}" if pb_val != '--' else "N/A"
        mv_str = f"{mv_val} 亿元" if mv_val != '--' else "N/A"

        # 核心点位校验与纠偏（严禁亏损标的风控防线高于现价的倒挂错误）
        t_buy = plan.get('t_buy') or round(curr_p * 0.985, 2)
        t_sell = plan.get('t_sell') or round(curr_p * 1.035, 2)
        target1 = plan.get('target1') or round(curr_p * 1.06, 2)
        target2 = plan.get('target2') or round(curr_p * 1.15, 2)
        buy_range = plan.get('buy_range', f"{curr_p*0.98:.2f} ~ {curr_p*0.99:.2f}")

        if holding_item:
            cost = float(holding_item.get("成本价", 0.0) or 0.0)
            shares = int(holding_item.get("持仓股数", 1000) or 1000)
            loss_pct = round(((curr_p - cost) / cost) * 100, 2) if cost > 0 else 0.0
            total_loss = round((curr_p - cost) * shares, 2) if cost > 0 else 0.0
            account_desc = f"用户已持有持仓 | 买入成本: {cost:.2f} 元 | 持仓股数: {shares} 股 | 当前盈亏: {loss_pct:+.2f}% ({total_loss:+.2f} 元)"
            
            # 防线逻辑：盈利设保利线，亏损必在现价下方（与止损线对齐）
            if curr_p >= cost:
                protect_line = round(cost + (curr_p - cost) * 0.65, 2)
            else:
                hard_stop = float(plan.get('hard_stop') or round(curr_p * 0.965, 2))
                protect_line = hard_stop if hard_stop < curr_p else round(curr_p * 0.965, 2)
        else:
            cost = 0.0
            account_desc = "用户尚未建仓（自选/观察标的，成本为0）"
            protect_line = round(curr_p * 0.980, 2)

        prompt = f"""你是一名资深 A 股私募基金投资总监与量化操盘专家。请严格基于以下客观真实数据，对标的进行【立体多维画像】，并按照固定的【步骤一至步骤五 + 铁律 + 执单总结】框架，为用户输出操盘执行单。

【标的与账户客观数据】：
- 股票标的：{stock_name} ({target_code}) | 现价：{curr_p:.2f} 元 (今日涨跌: {pct_today_str})
- 账户处境：{account_desc}
- 真实财务估值：PE(动)={pe_str}倍 | PB={pb_str} | 总市值={mv_str}
- 盘口量化指标：MA5={scores.get('ma5')} | MA20={scores.get('ma20')} | MA60={scores.get('ma60')} | RSI(14)={scores.get('rsi')}
- 周期量化评分：短线={scores.get('short_term', {}).get('score')}分 | 中线={scores.get('mid_term', {}).get('score')}分 | 长线={scores.get('long_term', {}).get('score')}分
- 量化系统参考点位：参考支撑/买点【{t_buy}元】 | 参考阻力/卖点【{t_sell}元】 | 刚性风控防线【{protect_line}元】 | 阶段目标【{target1}元 / {target2}元】

【核心排版与操盘原则】：
1. 步骤一必须输出三张【独立精炼决策牌】：每张牌严格限定一句话（30字内），绝对严禁在引用块里写长篇大论作文！长篇分析必须放到步骤二和步骤三的表格中；
2. 拒绝单一板块机械归类：根据标的商业壁垒、资金属性、市值与均线多空，进行立体多维画像；
3. 操盘战术严密自洽：
   - 核心底仓型标的：底仓坚守吃分红防洗飞，不建议频繁做日内微T；
   - 趋势波段型标的：顺应大级别均线波段持股，在支撑加仓、阻力减半，吃主升浪大波段；
   - 超短题材型标的：纯短线情绪博弈，盯盘口快进快出，破位刚性止损，严禁长线死扛；
4. 严防价格倒挂：风控防线【{protect_line}元】在亏损时必须低于现价，严禁倒挂。

--- 请严格按照以下固定步骤输出，保证每个标题、引用块和表格前后都有空行换行 ---

### 步骤一：【定调】标的立体画像与三大独立决策牌

*💡 【板块作用】：顶置三大精炼决策牌，拒绝任何大段冗长文字堆砌，每张牌限定一句话，清晰独立呈现！*

> 🚦 **标的立体画像**：【8~12字精准定位，如：💎 高壁垒精密制造细分中军 / ⚡ 游资主导的高弹性亏损题材股】 (结合当前实际盈亏给出15字战略总基调)

&nbsp;

> 🟢 **关键支撑买点**：【 **{t_buy} 元** 】 (一句话说明低吸或回踩确认条件，严控20字内)

&nbsp;

> 🔴 **关键阻力卖点**：【 **{t_sell} 元** 】 (一句话说明冲高兑现或减半条件，严控20字内)

### 步骤二：【估值】基本面护城河与估值中枢表

*💡 【板块作用】：剖析标的所处行业地位与宏观产业周期，结合PE={pe_str}与PB={pb_str}评估真实安全边际。*

| 深度分析维度 | 核心数据 / 行业事实 | 操盘手定性结论与实战含义 |
| :--- | :--- | :--- |
| **行业地位与护城河** | [所属行业细分地位与核心壁垒] | [长期安全垫与定价权分析] |
| **产业宏观周期** | [赛道宏观阶段与供需格局变动] | [近期消息与产业催化] |
| **估值安全边际** | PE={pe_str}倍, PB={pb_str}, 市值={mv_str} | [评估估值处于低估坑底还是高估透支] |

### 步骤三：【盘口】早盘 45 分钟试金石与日内多空分水岭表

*💡 【板块作用】：实战盘口抓手，融合 9:15-9:25 集合竞价与 9:30-10:00 前半小时试金石。*

| 盘口关键时段 / 指标 | 关键临界数值 | 操盘手实战定调与盘口信号 |
| :--- | :--- | :--- |
| **9:25 集合竞价承接力** | 竞价合理区间【{curr_p*0.99:.2f} ~ {curr_p*1.015:.2f}元】 | [分析集合竞价量价异动信号] |
| **9:30-10:00 前半小时强弱** | 上攻阻力【{t_sell}元】 / 下探支撑【{t_buy}元】 | [说明突破与下探的应对] |
| **日内多空平衡线** | MA20支撑位: {scores.get('ma20')}元 | [说明生命线的得失与多空动能] |

### 步骤四：【战术】多空双向 If-Then 实战预案表

*💡 【板块作用】：杜绝死板与盲目死扛，明确底仓与机动仓分工及双向应对法则。*

| 盘面演变情景 | 触发条件与点位 | 仓位执行动作 | 战术目的与收益防守 |
| :--- | :--- | :--- | :--- |
| **情景 1：放量突破上攻** | 突破阻力位【{t_sell}元】 | [具体仓位动作] | [顺应主升浪防踏空] |
| **情景 2：冲高滞涨回落** | 触及目标位【{target1}元】无量 | [具体减仓或高抛动作] | [锁定利润或防回吐] |
| **情景 3：破位跳水防守** | 跌破防守线【{protect_line}元】 | [防守减仓或止损动作] | [截断回撤守住底线] |

### 步骤五：【条件单】手机券商直接照抄执行清单

*💡 【板块作用】：手机券商执行单，将参数直接录入任意券商APP智能条件单（同花顺/银河/中信/华泰等），系统自动盯盘执行。*

| 条件单类型 | 监控触发价格 | 委托操作与数量 | 监控有效期 | 战术目的 |
| :--- | :--- | :--- | :--- | :--- |
| 回落买入单 (低吸/建仓) | 价格 <= **{t_buy}元** | 限价买入指定仓位 | 长期有效 | [结合画像说明买入或做T意图] |
| 冲高卖出单 (止盈/高抛) | 价格 >= **{t_sell}元** | 限价卖出指定仓位 | 长期有效 | [说明高抛或兑现目的] |
| 破位防守单 (止损/减仓) | 价格 <= **{protect_line}元** | 市价/限价卖出对应仓位 | 长期有效 | [说明截断亏损或保利目的] |
| 阶段目标单 (兑现/减半) | 价格 >= **{target1}元** | 限价卖出部分仓位 | 长期有效 | [说明目标兑现目的] |
| 移动保护单 (保护利润) | 若价格站上 **{target1}元** 后回落至 **{t_buy}元** | 卖出对应仓位 | 触发后失效 | [防止坐电梯回吐] |

▲ 条件单执行铁律：
[结合上述条件单提炼 3~4 条执行铁律，说明买入单、卖出单、防守单的触发执行纪律]

### 执单总结（一页纸浓缩）

| 项目 | 结论 |
| :--- | :--- |
| **标的画像** | [输出所研判的立体画像定位] |
| **当前状态** | [概括真实盈亏幅度与盘口形态强弱] |
| **核心战术** | [根据标的画像输出完全自洽的核心打法] |
| **操作区间** | 买 {t_buy} / 卖 {t_sell} (空间约 {((t_sell-t_buy)/t_buy)*100:.1f}%) |
| **风控底线** | {protect_line} 元 [明确跌破后的执行动作] |
| **兑现纪律** | [结合画像与盈亏给出明确目标：如目标止盈 / 回本减仓 / 周期见顶兑现] |
| **操作红线** | [列出 2~3 条严格契合该标的画像的操作禁忌，绝不出现长线标的写“禁长线”等打架现象] |

——执单完毕，严守纪律，知行合一——
"""

        if self.client:
            try:
                res = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1
                )
                return res.choices[0].message.content
            except Exception as e:
                return f"⚠️ 调用 DeepSeek 模型异常: {e}"
        elif OpenAI and self.api_key:
            try:
                client = OpenAI(api_key=self.api_key, base_url=self.base_url)
                res = client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1
                )
                return res.choices[0].message.content
            except Exception as e:
                return f"⚠️ 调用 DeepSeek 模型异常: {e}"
        else:
            return "⚠️ 请在 .env 中配置有效的 DEEPSEEK_API_KEY 以生成个股专属操盘执单。"
