# -*- coding: utf-8 -*-
"""
模块：AI 操盘顾问引擎 (AIAdvisor)
职责：
1. 负责 DeepSeek 等大语言模型客户端的初始化与鉴权 (.env 自动穿透加载)；
2. 负责【单股操盘内参】与【全景股票池内参】的核心 Prompt 构建；
3. 输出符合金融终端视觉标准的【三大核心决策牌 + 四大清晰结构化表格】；
4. 严格全券商通用（适配同花顺、东方财富、银河、中信、国泰君安等任意券商条件单）；
5. 针对持仓股输出自救回本路线，针对自选股输出精准开仓点位与仓位规划。
"""

import os
import json
import re

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

def _get(seq, idx, default=""):
    try:
        return seq[idx]
    except Exception:
        return default

def auto_load_env():
    cur = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(cur, ".env"),
        os.path.join(cur, "..", ".env"),
        os.path.join(os.getcwd(), ".env")
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
                except Exception:
                    pass
        if os.getenv("DEEPSEEK_API_KEY"):
            break

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
        """
        单只股票专属操盘内参报告
        """
        if not self.is_configured():
            return "⚠️ 请先在 .env 中配置有效的 DEEPSEEK_API_KEY。"

        stock_name = quote.get("name", "")
        curr_p = float(quote.get("curr_price", 0.0) or 0.0)
        prev_c = float(quote.get("prev_close", 0.0) or curr_p)
        pct_today_str = f"{((curr_p - prev_c)/prev_c*100):+.2f}%" if prev_c else "0.00%"

        if holding_item:
            # ==================== 【场景 A：实战持仓股】 ====================
            cost = float(holding_item.get("成本价", 0.0))
            shares = int(holding_item.get("持仓股数", 1000) or 1000)
            loss_pct = round(((curr_p - cost) / cost) * 100, 2)
            total_loss = round((curr_p - cost) * shares, 2)
            needed_gain = round(((cost - curr_p) / curr_p) * 100, 2) if curr_p > 0 else 0.0

            profit_status = f"盈利 +{loss_pct:.2f}% (+{total_loss:.2f} 元)" if loss_pct > 0 else (
                f"持平 0.00%" if loss_pct == 0 else f"浮亏 {loss_pct:.2f}% ({total_loss:.2f} 元，直接回本需涨幅 +{needed_gain:.2f}%)"
            )

            prompt = f"""你是一名资深 A 股私募基金投资总监。请针对用户【已购入的实战持仓标的】，输出一份【顶置三大核心决策牌 + 四大清晰结构化表格】的操盘手实战执单。

【持仓账户与盘口数据】：
- 股票标的：{stock_name} ({target_code})
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
*💡 【板块作用】：摸清战场地形，标定上方解套抛压天花板与下方多头防守地板，明确高抛低吸安全边界。*
| 诊断维度 | 核心点位 / 数据 | 操盘手定性结论与实战含义 |
| :--- | :--- | :--- |
| 成本与现价 | 成本 {cost:.2f}元 vs 现价 {curr_p:.2f}元 | 当前盈亏 {profit_status}，分析筹码处于获利盘还是套牢区 |
| 上方关键阻力带 | 具体价格区间 (如 MA20/MA60) | 反弹抛压重灾区与做T交筹码窗口 |
| 下方核心支撑带 | 具体价格区间 (如 做T买点/止损) | 多头最后防守位，跌破则趋势恶化 |

### 二、 三大持有周期实战操作决策表（短/中/长线）
*💡 【板块作用】：时间与策略匹配，结合短线T+1、中线波段与长线价值评分，给出不同周期的具体仓位与点位打法。*
| 周期类型与评分 | 核心点位规划 | 具体仓位动作与目标 |
| :--- | :--- | :--- |
| **⚡ 短线 T+1 ({scores.get('short_term', {}).get('score')}分)** | 做T买入: **{plan.get('t_buy')}元**<br>冲高卖出: **{plan.get('t_sell')}元** | 回踩低吸加仓，冲高必须T出底仓，赚差价降本，破止损严决减仓 |
| **🌊 中线波段 ({scores.get('mid_term', {}).get('score')}分)** | 建议止盈: **xx元**<br>加仓均线: **xx元** | 保持合理底仓，未站稳MA20不盲目重仓，反弹分批减仓策略 |
| **💎 长线价值 ({scores.get('long_term', {}).get('score')}分)** | 补仓点位:<br>一档: **xx元**<br>二档: **xx元** | 结合估值安全边际，评估长线回本目标价与金字塔分批布局计划 |

### 三、 账户当前实际盈亏针对性应对路线表
*💡 【板块作用】：实操战术路线，针对当前实际盈亏制定日内做T降本、遇阻分批减仓与破位刚性保命的执行步骤。*
| 战术步骤 | 触发价格条件 | 委托动作与仓位 | 战术目的与降本目标 |
| :--- | :--- | :--- | :--- |
| **步骤 1：日内做T降本** | 回踩至 **{plan.get('t_buy')}元** / 冲高至 **{plan.get('t_sell')}元** | 买入/卖出对应数量 | 测算每笔做T降低综合成本幅度 |
| **步骤 2：阻力位减仓** | 达到上方第一技术阻力位 | 分批减仓比例 | 锁定反弹战果，防止回踩再度被套 |
| **步骤 3：刚性风险防守** | 跌破 **{plan.get('hard_stop')}元** | 严格执行止损 | 绝不盲目死扛，守住本金底线 |

### 四、 券商智能条件单直接照抄清单
*💡 【板块作用】：手机券商执行单，将点位与股数直接照抄录入任意券商APP智能条件单（如同花顺/银河/中信/国泰君安等），由系统自动盯盘触发。*
| 条件单类型 | 监控触发价格 | 委托操作与数量 | 监控有效期 | 战术目的 |
| :--- | :--- | :--- | :--- | :--- |
| 股价回落买入 (做T低吸) | 价格 <= **{plan.get('t_buy')}元** | 限价买入 xx股 | 当日有效 | 日内回踩低吸拉低成本 |
| 股价反弹卖出 (做T冲高) | 价格 >= **{plan.get('t_sell')}元** | 限价卖出 xx股 | 当日有效 | 冲高获利兑现做T差价 |
| 止损条件单 (防守底线) | 价格 <= **{plan.get('hard_stop')}元** | 市价/限价卖出全部 | 长期有效 | 破位刚性离场规避深套 |
"""
        else:
            # ==================== 【场景 B：未持仓的自选/观察股】 ====================
            prompt = f"""你是一名专业私募基金投资总监。请针对以下用户【尚未持仓的观察标的】，输出一份【顶置三大核心决策牌 + 四大清晰结构化表格】的实战操盘策略。

【标的技术面实时数据】：
- 股票标的：{stock_name} ({target_code})
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
*💡 【板块作用】：多空结构体检，量化均线排列、RSI超买超卖与量价动能，识别主力资金吸筹意图与爆发力。*
| 分析维度 | 当前技术状态 | 主力资金意图与技术含义 |
| :--- | :--- | :--- |
| 均线多空结构 | MA5/20/60 排列形态 | 趋势方向与均线支撑阻力 |
| 量价与动量 | RSI(14) 及成交量状态 | 超买超卖评估与资金吸筹意图 |
| 综合评级 | {scores.get('overall_grade')} | 明确是否具备入场赔率 |

### 二、 估值安全边际与向上赔率测算表
*💡 【板块作用】：空间与盈亏比测算，明确向上两档目标获利空间与向下止损成本，评估是否具备高赔率入场价值。*
| 估值与空间 | 点位规划 | 收益与风险评估结论 |
| :--- | :--- | :--- |
| 上行目标位 | 第一目标 **{plan.get('target1')}元** (+4.5%)<br>第二目标 **{plan.get('target2')}元** (+10%) | 测算向上弹性空间 |
| 下行防守线 | 开仓止损 **{plan.get('stop_loss')}元** (-2.0%) | 潜在最大试错风险与盈亏比结论 |

### 三、 三大周期操盘战术定调表（未持仓·建仓与出场规划）
*💡 【板块作用】：实操建仓指南，明确非持仓标的在短线、中线、长线下该在什么点位买入、开多少仓、去哪里止盈与止损。*
| 投资周期与评分 | 建议开仓仓位 | 计划买入点位 / 触发条件 | 目标止盈与防守止损线 |
| :--- | :--- | :--- | :--- |
| **⚡ 短线 T+1 ({scores.get('short_term', {}).get('score')}分)** | 1~2成机动仓 | 限价挂单 **{plan.get('buy_range')}元** 低吸（严禁追高追涨） | 冲高触及 **{plan.get('target1')}元** 次日落袋；跌破 **{plan.get('stop_loss')}元** 刚性止损 |
| **🌊 中线波段 ({scores.get('mid_term', {}).get('score')}分)** | 3~4成主波段仓 | 等待缩量回踩至 MA20 (约 **{scores.get('ma20')}元**) 附近企稳吸筹 | 向上看波段目标 **{plan.get('target2')}元**；有效跌破 MA20 趋势破位离场 |
| **💎 长线价值 ({scores.get('long_term', {}).get('score')}分)** | 2~3成底仓配置 | 结合历史估值分位，在 **{scores.get('ma60')}元** 附近逢低分两批金字塔挂单 | 长线看行业周期反转与估值修复；以大周期破位作为终极防守线 |

### 四、 券商智能条件单实战挂单计划表
*💡 【板块作用】：手机券商执行单，将限价买入、两档止盈与防守止损参数直接录入任意券商APP条件单，严格执行左侧潜伏纪律。*
| 条件单类型 | 监控触发价格 | 委托数量 | 有效期 | 操盘目的 |
| :--- | :--- | :--- | :--- | :--- |
| 限价买入条件单 | 价格 <= **{plan.get('buy_range')}** | 计划底仓数量 | 当日有效 | 严格左侧低吸，防追高 |
| 止盈条件单 (短线) | 价格 >= **{plan.get('target1')}元** | 卖出 1/2 仓位 | 长期有效 | 锁定第一波短线利润 |
| 止盈条件单 (波段) | 价格 >= **{plan.get('target2')}元** | 卖出剩余仓位 | 长期有效 | 把握中线波段主升浪 |
| 止损条件单 (刚性) | 价格 <= **{plan.get('stop_loss')}元** | 全部清仓离场 | 长期有效 | 刚性截断亏损，规避深套 |
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
        """
        全景股票池内参诊断
        """
        if not self.is_configured():
            return "⚠️ 请先在 .env 中配置有效的 DEEPSEEK_API_KEY。"

        payload = {"实战持仓股票池": holdings, "重点观察自选池": watchlists}
        data_str = json.dumps(payload, ensure_ascii=False, indent=2)
        prompt = f"""你是一名资深 A 股私募基金投资总监。请针对以下用户的【实战持仓】与【观察自选】数据，输出一份【极度精炼、纯干货、零废话】的全景操盘内参：
{data_str}

【硬性要求】：
1. 严禁任何寒暄、称呼、情绪安慰等口水话；
2. 直奔主题，按如下结构分模块输出：
### 一、 【实战持仓股】盘口诊断与减亏自救作战单
- 对每只持仓股：盘口健康度、日内做 T 降本点位、加仓翻盘测算、实战挂单与止盈止损规划。
### 二、 【重点观察自选股】量化狙击与上车计划
- 对每只自选股：性价比评估、建议回踩低吸挂单区间、两档目标位、关键风控与挂单规划。
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

# 单例辅助函数
_advisor_instance = None
def get_advisor() -> AIAdvisor:
    global _advisor_instance
    if _advisor_instance is None:
        _advisor_instance = AIAdvisor()
    return _advisor_instance

def diagnose(target_code, quote, scores, plan, holding_item=None) -> str:
    advisor = get_advisor()
    return advisor.diagnose_single_stock(target_code, quote, scores, plan, holding_item)
