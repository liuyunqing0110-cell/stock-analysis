# -*- coding: utf-8 -*-
"""
模块：AI 操盘顾问引擎 (AIAdvisor)
职责：
1. 负责 DeepSeek 等大语言模型客户端的初始化与鉴权 (.env 自动穿透加载)；
2. 负责【单股操盘内参】与【全景股票池内参】的核心 Prompt 构建；
3. 输出符合金融终端视觉标准的【三大核心决策牌 + 四大清晰结构化表格】(支持前端自动 Tab 切片)；
4. 全面融入【行业地位、主营护城河、行业宏观周期、产业新闻与政策催化】深度基本面研判；
5. 针对持仓股按“A1 盈利持仓”与“A2 亏损持仓”精细化输出，结合行业深度定性去留。
"""

import os
import json
import re

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

def _get(seq, idx, default=""):
    try: return seq[idx]
    except Exception: return default

def auto_load_env():
    cur = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(cur, ".env"), os.path.join(cur, "..", ".env"), os.path.join(os.getcwd(), ".env")
    ]
    for p in candidates:
        if os.path.exists(p):
            for enc in ["utf-8", "utf-8-sig", "gbk"]:
                try:
                    with open(p, "r", encoding=enc) as f:
                        for line in f:
                            line = line.strip()
                            if line and not line.startswith("#") and "=" in line:
                                k, v = line.split("=", 1)
                                os.environ[k.strip().lstrip("\ufeff")] = v.strip().strip("'\"")
                    break
                except Exception: pass
        if os.getenv("DEEPSEEK_API_KEY"): break

class AIAdvisor:
    def __init__(self, api_key=None, base_url=None, model=None):
        auto_load_env()
        self.api_key = (api_key or os.getenv("DEEPSEEK_API_KEY") or "").strip()
        self.base_url = (base_url or os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").strip()
        self.model = (model or os.getenv("AI_MODEL") or "deepseek-chat").strip()
        self.client = None
        if OpenAI and self.api_key:
            try:
                self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)
            except Exception as e:
                print(f"⚠️ 初始化 OpenAI 客户端异常: {e}")

    def is_configured(self) -> bool:
        return bool(self.client and self.api_key)

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

        if holding_item:
            cost = float(holding_item.get("成本价", 0.0))
            shares = int(holding_item.get("持仓股数", 1000) or 1000)
            loss_pct = round(((curr_p - cost) / cost) * 100, 2)
            total_loss = round((curr_p - cost) * shares, 2)
            needed_gain = round(((cost - curr_p) / curr_p) * 100, 2) if curr_p > 0 else 0.0

            if loss_pct >= 0:
                # ==================== 【场景 A1：盈利持仓股 (顺势保利·扩大利润)】 ====================
                profit_margin = curr_p - cost
                protect_line = plan.get('protect_line') or round(cost + profit_margin * 0.65, 2)
                target1 = plan.get('target1') or round(curr_p * 1.08, 2)
                target2 = plan.get('target2') or round(curr_p * 1.18, 2)
                profit_status = f"浮盈 +{loss_pct:.2f}% (累计净赚 +{total_loss:.2f} 元)"

                prompt = f"""你是一名资深 A 股私募基金投资总监。请针对用户【当前正处于盈利状态的实战持仓标的】，输出一份【顶置三大决策牌 + 四大清晰结构化表格】的顶级赢家操盘手执单。

【账户盈利与盘口基本面】：
- 标的：{stock_name} ({target_code}) | 成本：{cost:.2f} 元 | 现价：{curr_p:.2f} 元 (今日: {pct_today_str}) | 持仓：{shares} 股
- 战果：{profit_status}
- 估值与市值：市盈率PE(动)={pe_str}倍 | 市净率PB={pb_str} | 总市值={mv_str}
- 量化多因子：MA5={scores.get('ma5')} | MA20={scores.get('ma20')} | MA60={scores.get('ma60')} | RSI(14)={scores.get('rsi')}
- 三大周期评分：短线={scores.get('short_term', {}).get('score')}分 | 中线={scores.get('mid_term', {}).get('score')}分 | 长线={scores.get('long_term', {}).get('score')}分
- 关键点位：移动保利底线【{protect_line}元】 | 第一冲高落袋【{target1}元】 | 主升浪大目标【{target2}元】

【核心操盘原则】：
【机构五大投资阵营专属研判准则 (必须严格对号入座，拒绝通用套话)】：
1. 科技成长 (TMT/制造)：核心评估研发壁垒、技术代差、国产替代率与订单能见度，不纠结眼前静态PE；
2. 大消费与医药：核心评估品牌壁垒、渠道动销、毛利率稳定性与单品提价权，严禁盲目看低PB买边缘杂质股；
3. 周期与资源：核心评估大宗商品现货拐点、供需出清、PB重置成本底与反转弹性，警惕低PE周期顶陷阱；
4. 大金融赛道：核心评估宏观流动性贝塔（券商看成交额，银行看净息差与资产质量不良率）；
5. 公用事业与高股息：核心评估特许经营权年限、现金流充沛度与分红派现率，重在破净安全垫防守。

1. 坚决不提前卖飞牛股：只要主升浪未破（未破关键均线），底仓绝不轻易清仓，让利润充分奔跑！
2. 绝对不允许盈利变亏损：设立动态拉高的【移动止盈保护线 (Trailing Stop)】，跌破果断锁利！
3. 深入结合【行业地位与行业周期】：判断当前行业是否处于高景气主升期、还是行业周期见顶面临降温杀估值风险，据此指导锁利节奏。

【硬性排版要求】：
1. 顶部输出三个独立决策牌：
> 🚦 **赢家核心战术定调**：【顺应主升浪持股待涨 / 行业估值偏高分批兑现】 (结合行业地位、赛道周期与趋势健康度定调)

> 🟢 **移动止盈保护线 (防利润回吐底线)**：【 **{protect_line} 元** 】 (若盘中跳水跌破此线必须果断减仓，锁死大部分纯利润)

> 🔴 **分批止盈兑现目标**：【 **{target1} 元 (+8%)** 】 (冲高触及建议先减仓 1/3~1/2 锁定现金战果，剩余仓位继续放飞)

2. 紧接着输出以下四大紧凑表格（每张表必须包含标准表头和表格边框，每张表前后空一行）：

### 一、 行业地位、基本面估值天花板与主升空间表
*💡 【板块作用】：深入剖析标的在行业中的竞争地位、所处产业宏观周期、近期行业新闻动态，结合PE={pe_str}与PB={pb_str}评估估值是否透支。*
| 深度分析维度 | 核心数据 / 行业事实 | 操盘手定性结论与主力意图 |
| :--- | :--- | :--- |
| **行业地位与护城河** | 明确所属申万一级/二级行业，细分龙头/中军身位 | 核心业务壁垒（如特许经营、技术垄断、成本优势或品牌溢价） |
| **行业周期与新闻催化** | 赛道所处宏观阶段（高成长/成熟高股息/见顶预警） | 近期重大产业政策红利、行业供需变动或催化新闻 |
| **估值天花板测算** | PE={pe_str}倍, PB={pb_str}, 市值={mv_str} | 相比同行业平均估值是处于合理区间还是短期透支泡沫化 |
| **下方趋势生命线** | 关键均线支撑 (MA20={scores.get('ma20')}元) | 只要不跌破关键均线且行业景气度仍在，坚定持股吃透主升浪 |

### 二、 盘口技术多空与筹码格局表
*💡 【板块作用】：量化均线排列、量价与动量RSI，明确高抛低吸安全边界。*
| 技术维度 | 核心数值 / 状态 | 操盘手实战定调 |
| :--- | :--- | :--- |
| 均线多空结构 | MA5/20/60 排列形态 | 主升浪健康度评估与均线支撑阻力 |
| 动能与超买超卖 | RSI(14)={scores.get('rsi')} | 评估量价配合度与短线冲高爆发力 |

### 三、 三大周期持股与利润滚雪球路线表
*💡 【板块作用】：实操战术路线，指导如何在防范回吐的前提下把利润最大化。*
| 阶段与周期 | 触发价格条件 | 委托动作与仓位 | 战术目的与收益保障 |
| :--- | :--- | :--- | :--- |
| **短线做T** | 盘中回踩均线支撑 | 顺向低吸冲高T出 | 用利润生利润，不丢底仓核心筹码 |
| **阶段落袋** | 触及 **{target1}元** | 减仓 1/3 或 1/2 | 将一半巨额利润兑现为银行卡现金，本金彻底无忧 |
| **保底撤退** | 放量跳水跌破 **{protect_line}元** | 清仓剩余筹码 | 刚性截断回撤，锁死大头利润离开 |

### 四、 券商智能条件单实战挂单计划表（盈利守护单）
*💡 【板块作用】：手机券商执行单，将移动止盈与阶梯止盈参数录入任意券商APP，由系统自动帮您保住利润。*
| 条件单类型 | 监控触发价格 | 委托操作与数量 | 监控有效期 | 战术目的 |
| :--- | :--- | :--- | :--- | :--- |
| 阶梯止盈单 (第一波) | 价格 >= **{target1}元** | 卖出 1/3 仓位 | 长期有效 | 冲高触及自动落袋，锁定部分现金利润 |
| 阶梯止盈单 (主升浪) | 价格 >= **{target2}元** | 卖出 1/3 仓位 | 长期有效 | 兑现大波段超额利润 |
| 移动止盈单 (防回吐) | 价格 <= **{protect_line}元** | 卖出全部剩余仓位 | 长期有效 | 趋势走坏时自动截断回撤，锁死大头利润 |
"""
            else:
                # ==================== 【场景 A2：浮亏被套持仓股 (深蹲反转·做T降本·大周期翻盘)】 ====================
                profit_status = f"浮亏 {loss_pct:.2f}% ({total_loss:.2f} 元，直接回本需涨幅 +{needed_gain:.2f}%)"
                t_buy = plan.get('t_buy') or round(curr_p * 0.982, 2)
                t_sell = plan.get('t_sell') or round(curr_p * 1.032, 2)
                hard_stop = plan.get('hard_stop') or round(curr_p * 0.965, 2)

                prompt = f"""你是一名资深 A 股私募基金投资总监。请针对用户【已购入但当前浮亏被套的实战持仓标的】，输出一份【顶置三大决策牌 + 四大清晰结构化表格】的操盘手实战执单。

【持仓账户与盘口基本面】：
- 标的：{stock_name} ({target_code}) | 成本：{cost:.2f} 元 | 现价：{curr_p:.2f} 元 (今日: {pct_today_str}) | 持仓：{shares} 股
- 当前状态：{profit_status}
- 估值与市值：市盈率PE(动)={pe_str}倍 | 市净率PB={pb_str} | 总市值={mv_str}
- 量化多因子：MA5={scores.get('ma5')} | MA20={scores.get('ma20')} | MA60={scores.get('ma60')} | RSI(14)={scores.get('rsi')}
- 三大周期评分：短线={scores.get('short_term', {}).get('score')}分 | 中线={scores.get('mid_term', {}).get('score')}分 | 长线={scores.get('long_term', {}).get('score')}分
- 关键点位：日内做T买点【{t_buy}元】 | 做T冲高卖点【{t_sell}元】 | 刚性止损红线【{hard_stop}元】


【机构五大投资阵营专属研判准则 (必须严格对号入座，拒绝通用套话)】：
1. 科技成长 (TMT/制造)：核心评估研发壁垒、技术代差、国产替代率与订单能见度，不纠结眼前静态PE；
2. 大消费与医药：核心评估品牌壁垒、渠道动销、毛利率稳定性与单品提价权，严禁盲目看低PB买边缘杂质股；
3. 周期与资源：核心评估大宗商品现货拐点、供需出清、PB重置成本底与反转弹性，警惕低PE周期顶陷阱；
4. 大金融赛道：核心评估宏观流动性贝塔（券商看成交额，银行看净息差与资产质量不良率）；
5. 公用事业与高股息：核心评估特许经营权年限、现金流充沛度与分红派现率，重在破净安全垫防守。

【核心投资逻辑定调要求 - 必须深度结合【行业地位、赛道周期与产业新闻】做出建设性定性】：
1. 深入剖析该股的【行业地位与主营护城河】：它是行业细分龙头、中军还是边缘企业？
2. 彻底厘清本轮下跌性质：是由于【全行业处于周期寒冬/泥沙俱下（公司基本面未损，未来具备强力反转弹性）】，还是由于【产业被颠覆替代、沦为夕阳淘汰资产（毫无翻盘希望）】？
3. 结合近期【行业新闻、政策导向与供需催化】，明确判断：本股到底属于【仅仅反弹解套走人】，还是属于【行业底部蓄势深蹲、做T降成本后未来能翻倍大赚的优质标的】！

【硬性排版要求】：
1. 顶部输出三个独立决策牌：
> 🚦 **核心战术定调与战略性质**：【短线自救解套 / 行业底部蓄势深蹲未来翻倍】 (结合行业地位、赛道周期与终极盈利预期定性)

> 🟢 **日内做 T 回踩买点**：【 **{t_buy} 元** 】 (具体买入触发条件，拉低每股综合成本)

> 🔴 **冲高做 T 止盈卖点**：【 **{t_sell} 元** 】 (反弹阻力位交出做T筹码)

2. 紧接着输出以下四大紧凑表格（每张表必须包含标准表头和表格边框，每张表前后空一行）：

### 一、 行业地位、宏观赛道周期与困境反转表
*💡 【板块作用】：从行业身位与宏观产业周期看清下跌根源，结合近期行业新闻催化与PE={pe_str}/PB={pb_str}估值，明确是真黄金坑还是价值陷阱。*
| 深度分析维度 | 核心数据 / 行业事实 | 操盘手定性结论与实战含义 |
| :--- | :--- | :--- |
| **行业地位与竞争壁垒** | 所属申万一级/二级行业，细分赛道市占率与排名 | 核心护城河成色（特许权/牌照/研发壁垒/成本优势），抗行业寒冬能力 |
| **赛道周期与产业新闻** | 行业当前所处周期（去库存尾声/产能出清/逆周期高股息） | 近期行业重大政策催化、利好利空新闻或供需格局拐点 |
| **估值安全边际与空间** | PE={pe_str}倍, PB={pb_str}, 市值={mv_str} | 估值是否跌入历史极值坑底，破净资产安全垫厚度 |
| **反弹解套与翻倍目标** | 成本 {cost:.2f}元 vs 现价 {curr_p:.2f}元 | 行业反转后的合理估值中枢与第一回本价/第二翻倍目标价测算 |

### 二、 三大持有周期实战操作决策表（短/中/长线）
*💡 【板块作用】：时间与策略匹配，结合短线T+1、中线波段与长线价值评分，给出不同周期的具体仓位与点位打法。*
| 周期类型与评分 | 核心点位规划 | 具体仓位动作与目标 |
| :--- | :--- | :--- |
| **⚡ 短线 T+1 ({scores.get('short_term', {}).get('score')}分)** | 做T买入: **{t_buy}元**<br>冲高卖出: **{t_sell}元** | 回踩低吸加仓，冲高必须T出底仓，赚差价降本，破止损严决减仓 |
| **🌊 中线波段 ({scores.get('mid_term', {}).get('score')}分)** | 中线目标点位<br>均线加仓点位 | 保持合理底仓，未站稳MA20不盲目重仓，反弹分批兑现策略 |
| **💎 长线价值 ({scores.get('long_term', {}).get('score')}分)** | 回本目标: **{cost:.2f}元**<br>超额目标价 | 结合行业周期反转空间，评估是到成本价跑路还是继续抱牢赚大钱的翻倍计划 |

### 三、 账户当前实际盈亏针对性应对路线表（自救翻盘与继续赚钱路线）
*💡 【板块作用】：实操战术路线，明确当前亏损是该“减亏出局”还是“做T降本后继续抱着大赚”的执行步骤。*
| 战术步骤 | 触发价格条件 | 委托动作与仓位 | 战术目的与终极目标（解套还是大赚） |
| :--- | :--- | :--- | :--- |
| **步骤 1：日内做T降本** | 回踩至 **{t_buy}元** / 冲高至 **{t_sell}元** | 买入/卖出对应数量 | 测算每笔做T降低综合成本幅度，增强持股底气 |
| **步骤 2：反弹至成本线抉择** | 达到成本价 **{cost:.2f}元** | 减半本金 / 留底仓继续抱着赚 | 明确告知是该全部走人还是继续享受主升浪利润 |
| **步骤 3：刚性风险防守** | 跌破 **{hard_stop}元** | 严格执行止损减仓 | 绝不盲目死扛，守住本金底线（破位后耐心等极低点接回） |

### 四、 券商智能条件单直接照抄清单
*💡 【板块作用】：手机券商执行单，将点位与股数直接照抄录入任意券商APP智能条件单（如同花顺/银河/中信/国泰君安等），由系统自动盯盘触发。*
| 条件单类型 | 监控触发价格 | 委托操作与数量 | 监控有效期 | 战术目的 |
| :--- | :--- | :--- | :--- | :--- |
| 股价回落买入 (做T低吸) | 价格 <= **{t_buy}元** | 限价买入指定股数 | 当日有效 | 日内回踩低吸拉低成本 |
| 股价反弹卖出 (做T冲高) | 价格 >= **{t_sell}元** | 限价卖出指定股数 | 当日有效 | 冲高获利兑现做T差价 |
| 止损条件单 (防守底线) | 价格 <= **{hard_stop}元** | 市价/限价清仓离场 | 长期有效 | 破位刚性离场规避深套 |
"""
        else:
            # ==================== 【场景 B：未持仓的自选/观察股】 ====================
            buy_range = plan.get('buy_range', f"{curr_p*0.98:.2f} ~ {curr_p*0.99:.2f}")
            target1 = plan.get('target1', round(curr_p * 1.045, 2))
            target2 = plan.get('target2', round(curr_p * 1.100, 2))
            stop_loss = plan.get('stop_loss', round(curr_p * 0.980, 2))

            prompt = f"""你是一名专业私募基金投资总监。请针对以下用户【尚未持仓的观察标的】，输出一份【顶置三大核心决策牌 + 四大清晰结构化表格】的实战操盘策略。

【标的技术面与估值数据】：
- 股票标的：{stock_name} ({target_code}) | 现价：{curr_p:.2f} 元 (今日: {pct_today_str})
- 估值与市值：市盈率PE(动)={pe_str}倍 | 市净率PB={pb_str} | 总市值={mv_str}
- 技术面指标：MA5={scores.get('ma5')} | MA20={scores.get('ma20')} | MA60={scores.get('ma60')} | RSI(14)={scores.get('rsi')}
- 多因子评分：短线={scores.get('short_term', {}).get('score')}分 | 中线={scores.get('mid_term', {}).get('score')}分 | 长线={scores.get('long_term', {}).get('score')}分 | 评级={scores.get('overall_grade')}
- 计划点位：建议回踩买入区间【{buy_range}】 | 短线目标【{target1}元】 | 波段目标【{target2}元】 | 刚性止损【{stop_loss}元】


【机构五大投资阵营专属研判准则 (必须严格对号入座，拒绝通用套话)】：
1. 科技成长 (TMT/制造)：核心评估研发壁垒、技术代差、国产替代率与订单能见度，不纠结眼前静态PE；
2. 大消费与医药：核心评估品牌壁垒、渠道动销、毛利率稳定性与单品提价权，严禁盲目看低PB买边缘杂质股；
3. 周期与资源：核心评估大宗商品现货拐点、供需出清、PB重置成本底与反转弹性，警惕低PE周期顶陷阱；
4. 大金融赛道：核心评估宏观流动性贝塔（券商看成交额，银行看净息差与资产质量不良率）；
5. 公用事业与高股息：核心评估特许经营权年限、现金流充沛度与分红派现率，重在破净安全垫防守。

【核心操盘原则 - 深入结合【行业地位、赛道周期与重大产业新闻】定调】：
1. 深入分析标的所属申万行业、在细分赛道中属于龙头、中军还是高弹性标的，分析其核心护城河。
2. 结合近期行业宏观新闻与产业政策催化，明确当前板块所处的市场轮动地位（是主线热点还是防御性低估值资产）。
3. 给出建议回踩区间与两档止盈目标。

【硬性排版要求】：
1. 顶部输出三个醒目决策牌：
> 🚦 **核心战术定调**：【回踩下沿埋伏 / 右侧放量突破上车 / 观望等待信号】 (结合行业地位、赛道周期与盈亏比结论定调)

> 🟢 **建议回踩建仓区间**：【 **{buy_range} 元** 】 (严格限价挂单，严禁追高)

> 🔴 **短线第一止盈目标**：【 **{target1} 元 (+4.5%)** 】 (冲高触及果断减半锁定利润)

2. 紧接着输出以下四大紧凑表格（每张表必须包含标准表头和表格边框，每张表前后空一行）：

### 一、 行业地位、赛道周期与估值赔率表
*💡 【板块作用】：深入剖析标的在行业中的竞争地位、所处宏观赛道周期、近期行业新闻与政策催化，结合PE={pe_str}与PB={pb_str}明确向上空间与向下止损成本。*
| 深度分析维度 | 核心数据 / 行业事实 | 操盘手定性结论与实战含义 |
| :--- | :--- | :--- |
| **行业地位与核心壁垒** | 所属申万一级/二级行业，细分龙头/中军身位 | 核心护城河与竞争优势（技术/牌照/成本/特许权） |
| **赛道周期与新闻催化** | 当前所处行业周期阶段（顺周期高成长/逆周期高股息防御） | 近期重大产业政策红利、行业供需变动或重大催化新闻 |
| **上行目标弹性空间** | 第一目标 **{target1}元** (+4.5%)<br>第二目标 **{target2}元** (+10%) | 结合同行业平均估值测算向上反弹弹性空间 |
| **下行防守试错底线** | 开仓止损 **{stop_loss}元** (-2.0%) | 潜在最大试错风险与盈亏比结论 |

### 二、 盘口形态与技术指标量化表
*💡 【板块作用】：多空结构体检，量化均线排列、RSI超买超卖与量价动能，识别主力资金吸筹意图与爆发力。*
| 分析维度 | 当前技术状态 | 主力资金意图与技术含义 |
| :--- | :--- | :--- |
| 均线多空结构 | MA5/20/60 排列形态 | 趋势方向与均线支撑阻力 |
| 量价与动量 | RSI(14) 及成交量状态 | 超买超卖评估与资金吸筹意图 |
| 综合评级 | {scores.get('overall_grade')} | 明确是否具备入场赔率 |

### 三、 三大周期操盘战术定调表（未持仓·建仓与出场规划）
*💡 【板块作用】：实操建仓指南，明确非持仓标的在短线、中线、长线下该在什么点位买入、开多少仓、去哪里止盈与止损。*
| 投资周期与评分 | 建议开仓仓位 | 计划买入点位 / 触发条件 | 目标止盈与防守止损线 |
| :--- | :--- | :--- | :--- |
| **⚡ 短线 T+1 ({scores.get('short_term', {}).get('score')}分)** | 1~2成机动仓 | 限价挂单 **{buy_range}元** 低吸（严禁追高追涨） | 冲高触及 **{target1}元** 次日落袋；跌破 **{stop_loss}元** 刚性止损 |
| **🌊 中线波段 ({scores.get('mid_term', {}).get('score')}分)** | 3~4成主波段仓 | 等待缩量回踩至 MA20 (约 **{scores.get('ma20')}元**) 附近企稳吸筹 | 向上看波段目标 **{target2}元**；有效跌破 MA20 趋势破位离场 |
| **💎 长线价值 ({scores.get('long_term', {}).get('score')}分)** | 2~3成底仓配置 | 结合历史估值分位，在 **{scores.get('ma60')}元** 附近逢低分两批金字塔挂单 | 结合行业周期反转与估值修复；以大周期破位作为终极防守线 |

### 四、 券商智能条件单实战挂单计划表
*💡 【板块作用】：手机券商执行单，将限价买入、两档止盈与防守止损参数直接录入任意券商APP条件单，严格执行左侧潜伏纪律。*
| 条件单类型 | 监控触发价格 | 委托数量 | 有效期 | 操盘目的 |
| :--- | :--- | :--- | :--- | :--- |
| 限价买入条件单 | 价格 <= **{buy_range}** | 计划底仓数量 | 当日有效 | 严格左侧低吸，防追高 |
| 止盈条件单 (短线) | 价格 >= **{target1}元** | 卖出 1/2 仓位 | 长期有效 | 锁定第一波短线利润 |
| 止盈条件单 (波段) | 价格 >= **{target2}元** | 卖出剩余仓位 | 长期有效 | 把握中线波段主升浪 |
| 止损条件单 (刚性) | 价格 <= **{stop_loss}元** | 全部清仓离场 | 长期有效 | 刚性截断亏损，规避深套 |
"""

        try:
            res = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2
            )
            return _get(res.choices, 0).message.content
        except Exception as e:
            return f"❌ DeepSeek 连线异常: {e}"

    def diagnose_portfolio(self, holdings: list, watchlists: list) -> str:
        if not self.is_configured():
            return "⚠️ 请先在 .env 中配置有效的 DEEPSEEK_API_KEY。"

        payload = {"实战持仓股票池": holdings, "重点观察自选池": watchlists}
        data_str = json.dumps(payload, ensure_ascii=False, indent=2)
        prompt = f"""你是一名资深 A 股私募基金投资总监。请针对以下用户的【实战持仓】与【观察自选】数据，输出一份【极度精炼、纯干货、零废话】的全景操盘内参：
{data_str}

【硬性要求】：
1. 严禁任何寒暄、称呼、情绪安慰等口水话；
2. 直奔主题，按如下结构分模块输出：
### 一、 【实战持仓股】行业地位、盘口诊断与减亏自救作战单
- 对每只持仓股：所属细分赛道地位、行业景气度周期、盘口健康度、日内做 T 降本点位、加仓翻盘测算、实战挂单与止盈止损规划。
### 二、 【重点观察自选股】行业赛道前景、量化狙击与上车计划
- 对每只自选股：行业竞争壁垒与政策催化、性价比评估、建议回踩低吸挂单区间、两档目标位、关键风控与挂单规划。
"""
        try:
            res = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2
            )
            return _get(res.choices, 0).message.content
        except Exception as e:
            return f"❌ DeepSeek 连线异常: {e}"

_advisor_instance = None
def get_advisor() -> AIAdvisor:
    global _advisor_instance
    if _advisor_instance is None:
        _advisor_instance = AIAdvisor()
    return _advisor_instance

def diagnose(target_code, quote, scores, plan, holding_item=None) -> str:
    advisor = get_advisor()
    return advisor.diagnose_single_stock(target_code, quote, scores, plan, holding_item)
