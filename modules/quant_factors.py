# -*- coding: utf-8 -*-
"""
模块 3：全维度多因子量化与白盒打分引擎 (支持方案一估值因子注入)
"""
import pandas as pd
import numpy as np

class QuantFactorsEngine:
    def __init__(self, kline_df: pd.DataFrame, spot_info: dict, klines_info: dict = None):
        self.df = kline_df.copy() if kline_df is not None and not kline_df.empty else None
        self.spot = spot_info or {}
        self.klines_info = klines_info or {}

    def calculate_technical_factors(self) -> dict:
        if self.df is None or len(self.df) < 5:
            p = float(self.spot.get("curr_price", 0.0) or 10.0)
            return {"最新价": p, "MA5": p, "MA20": p, "MA60": p, "均线多头得分": 50, "RSI_14": 52.0, "量比": 1.0}

        close = self.df["收盘"]
        latest_close = close.iloc[-1]
        ma5 = close.rolling(5).mean().iloc[-1]
        ma20 = close.rolling(20).mean().iloc[-1]
        ma60 = close.rolling(60).mean().iloc[-1] if len(close) >= 60 else ma20

        trend_score = 0
        if latest_close >= ma5 >= ma20: trend_score += 45
        elif latest_close >= ma20: trend_score += 25
        if ma20 >= ma60: trend_score += 35
        if latest_close >= ma60: trend_score += 20

        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / (loss + 1e-9)
        rsi = 100 - (100 / (1 + rs)).iloc[-1]

        vol_ratio = self.spot.get("turnover_rate", 1.0)
        return {
            "最新价": round(latest_close, 2),
            "MA5": round(ma5, 2), "MA20": round(ma20, 2), "MA60": round(ma60, 2),
            "均线多头得分": min(100, trend_score), "RSI_14": round(rsi, 2), "量比": round(vol_ratio, 2)
        }

    def calculate_scores(self, tech: dict) -> dict:
        price = tech["最新价"]
        rsi = tech["RSI_14"]
        trend = tech["均线多头得分"]
        turnover_rate = float(self.spot.get("turnover_rate", 0.0) or 0.0)
        pe = float(self.spot.get("pe", 0.0) or 0.0)
        pb = float(self.spot.get("pb", 0.0) or 0.0)
        mv = float(self.spot.get("total_mv", 0.0) or 0.0)

        # 1. ⚡ 短线 T+1 交易量化评分 (总分 100 分，深度结合 A 股 T+1 制度与换手率核心权重)
        # 权重配比：均线多头动量 35% + 换手率筹码活跃度 30% + RSI买卖动量 20% + 量比盘口 15%
        t1_base = 50

        # A. 均线多头动量 (满分贡献 35 分)
        trend_contrib = 0
        if trend >= 75: trend_contrib = 20
        elif trend >= 45: trend_contrib = 10
        elif trend < 30: trend_contrib = -15

        # B. 换手率核心评级 (满分贡献 30 分，专为 A 股 T+1 双轨复合模型定制)
        # 权重配比：当日换手率 60% + 近5日平均换手率 40% (MA5换手率)
        # 既能捕捉当天的资金突击力度与真实承接，又能评估常态流动性底色，剔除突发脉冲式一日游杂毛
        turnover_rates = self.klines_info.get("turnover_rates", [])
        valid_rates = [float(x) for x in turnover_rates if x is not None and float(x) > 0]
        if len(valid_rates) >= 5:
            ma5_turnover = round(sum(valid_rates[-5:]) / 5.0, 2)
        elif valid_rates:
            ma5_turnover = round(sum(valid_rates) / len(valid_rates), 2)
        else:
            ma5_turnover = turnover_rate

        comp_turnover = round(turnover_rate * 0.6 + ma5_turnover * 0.4, 2)
        turnover_contrib = 0
        turnover_status = ""
        is_pulse_warning = False

        # 核心防坑识别：平时地量(<1.5%)，今天突然脉冲放量(>5%) -> 典型一日游杂毛脉冲
        if turnover_rate >= 5.0 and ma5_turnover < 1.6:
            is_pulse_warning = True

        if comp_turnover <= 0:
            turnover_contrib = 0
            turnover_status = "换手率数据收集中"
        elif comp_turnover < 1.8:
            turnover_contrib = -18
            turnover_status = f"今日{turnover_rate:.2f}%/5日均{ma5_turnover:.2f}% (地量清淡)，常态流动性不足，T+1 弹性较差严禁追高"
        elif comp_turnover < 3.5:
            turnover_contrib = -6
            turnover_status = f"今日{turnover_rate:.2f}%/5日均{ma5_turnover:.2f}% (缩量整理)，资金观望情绪偏浓，适合逢低潜伏"
        elif 3.5 <= comp_turnover < 7.0:
            turnover_contrib = 14
            turnover_status = f"今日{turnover_rate:.2f}%/5日均{ma5_turnover:.2f}% (温和良性放量)，主力换手充分，T+1 胜率优良"
        elif 7.0 <= comp_turnover < 15.0:
            turnover_contrib = 24
            turnover_status = f"今日{turnover_rate:.2f}%/5日均{ma5_turnover:.2f}% (主力抢筹黄金区)，资金持续沉淀，T+1 爆发力极强"
        elif 15.0 <= comp_turnover < 25.0:
            turnover_contrib = 8
            turnover_status = f"今日{turnover_rate:.2f}%/5日均{ma5_turnover:.2f}% (多空剧烈博弈)，分歧加剧，T+1 需防冲高回落洗盘"
        else:
            turnover_contrib = -16
            turnover_status = f"今日{turnover_rate:.2f}%/5日均{ma5_turnover:.2f}% (天量过热松动)，警惕主力借巨震冲高派发，防范次日反杀"

        if is_pulse_warning:
            turnover_contrib -= 8
            turnover_status += " 【⚠️ 警惕：突发单日脉冲放量，防一日游退潮】"
        # C. RSI 超买超卖指标 (满分贡献 20 分)
        rsi_contrib = 0
        if 48 <= rsi <= 72: rsi_contrib = 12
        elif rsi > 82: rsi_contrib = -15 # 严重超买，T+1 次日容易回踩
        elif rsi < 30: rsi_contrib = 5   # 严重超跌反弹博弈

        # D. 量比动能 (满分贡献 15 分)
        vol_ratio = tech.get("量比", 1.0)
        vol_contrib = 0
        if 1.5 <= vol_ratio <= 3.5: vol_contrib = 8
        elif vol_ratio > 5.0: vol_contrib = -5 # 脉冲过激

        t1_score = t1_base + trend_contrib + turnover_contrib + rsi_contrib + vol_contrib
        t1_score = max(20, min(95, int(t1_score)))
        # 2. 中线波段趋势
        mid_score = max(20, min(95, int(trend * 0.7 + (25 if price >= tech["MA20"] else -15) + 15)))

        # 3. 💎 长线价值配置 (五大投资阵营差异化估值体系)
        long_score = 60
        stock_name = self.spot.get("name", "")

        # 识别五大阵营属性
        is_consumer = any(k in stock_name for k in ["酒", "食", "饮", "奶", "药", "医", "家电", "百货", "旅游", "商"])
        is_tech = any(k in stock_name for k in ["芯", "微", "半导", "光", "软", "信息", "电", "信", "通信", "科技", "智能", "算力", "车"])
        is_utility = any(k in stock_name for k in ["高速", "路", "电", "水", "燃气", "港", "交"])
        is_cyclical = any(k in stock_name for k in ["油", "煤", "铝", "铜", "钢", "化", "矿", "海", "航", "农", "渔"])
        is_bank = any(k in stock_name for k in ["银行", "证券", "保", "券商"])

        if is_consumer:
            # 消费类：品牌溢价高，PB天然偏高(2~6倍正常)，重在估值中枢与稳定现金流
            if pe > 0:
                if pe <= 20.0: long_score += 22
                elif pe <= 35.0: long_score += 14
                elif pe > 60.0: long_score -= 15
            elif pe < 0: long_score -= 25
            if 1.0 <= pb <= 4.5: long_score += 10  # 消费优质品牌PB区间加分
            elif pb > 9.0: long_score -= 10
        elif is_tech:
            # 科技成长类：重在产业趋势与研发弹性，宽容较高PE
            if pe > 0:
                if pe <= 35.0: long_score += 20
                elif pe <= 55.0: long_score += 12
                elif pe > 85.0: long_score -= 15
            elif pe < 0: long_score -= 12 # 科技股研发期亏损扣分适度宽容
            if pb <= 6.0: long_score += 8
        elif is_utility or is_bank:
            # 公用事业与金融红利：看重深度破净与高股息现金流安全垫 (如福建高速PB 0.79)
            if 0 < pb <= 0.85: long_score += 25     # 深度破净特许资产加重分
            elif pb <= 1.15: long_score += 15
            elif pb > 2.5: long_score -= 15
            if 0 < pe <= 15.0: long_score += 12
        elif is_cyclical:
            # 周期与大宗资源：看PB重置成本底，防低PE陷阱
            if 0 < pb <= 1.0: long_score += 24      # 周期底部破净重置底
            elif pb <= 1.6: long_score += 12
            # 周期底部微利/亏损不致死，若破净依然有价值
            if pe < 0 and pb <= 1.0: long_score += 5 
            elif pe > 0 and pe <= 12.0: long_score += 8
        else:
            # 通用基准
            if pe > 0:
                if pe <= 15.0: long_score += 20
                elif pe <= 32.0: long_score += 10
                elif pe > 65.0: long_score -= 18
            elif pe < 0: long_score -= 25
            if 0 < pb <= 1.2: long_score += 12
            elif pb > 8.0: long_score -= 10

        if mv >= 500.0: long_score += 8          # 大盘蓝筹抗风险
        elif mv < 30.0 and mv > 0: long_score -= 10

        long_score = max(20, min(95, long_score))

        def get_tag_info(score):
            if score >= 80: return "强势进攻", "bg-rose-500/20 text-rose-400 border border-rose-500/30"
            if score >= 60: return "稳健中性", "bg-blue-500/20 text-blue-400 border border-blue-500/30"
            return "偏弱观望", "bg-emerald-500/20 text-emerald-400 border border-emerald-500/30"

        st_tag, st_cls = get_tag_info(t1_score)
        mt_tag, mt_cls = get_tag_info(mid_score)
        lt_tag, lt_cls = get_tag_info(long_score)

        return {
            "short_term": {"score": t1_score, "tag": st_tag, "style": st_cls, "desc": turnover_status},
            "mid_term": {"score": mid_score, "tag": mt_tag, "style": mt_cls, "desc": "波段均线支撑强劲" if mid_score>=75 else "中线通道运行中"},
            "long_term": {"score": long_score, "tag": lt_tag, "style": lt_cls, "desc": "估值安全边际深厚" if long_score>=75 else "估值处于合理偏高水平"},
            "ma5": tech["MA5"], "ma20": tech["MA20"], "ma60": tech["MA60"], "rsi": tech["RSI_14"],
            "pe": pe, "pb": pb, "total_mv": mv, "turnover_rate": turnover_rate, "ma5_turnover": ma5_turnover, "composite_turnover": comp_turnover,
            "overall_grade": "A+ 顶格精选" if (t1_score+mid_score)/2 >= 80 else ("A 级 优先标的" if (t1_score+mid_score)/2 >= 65 else "B 级 观察仓位")
        }

def calculate_stock_scores(code, name="", curr_price=0.0, prev_close=0.0, klines_info=None, spot_info=None):
    df = None
    if klines_info and klines_info.get("success") and klines_info.get("klines"):
        kl = klines_info["klines"]
        closes = [x[1] for x in kl]
        df = pd.DataFrame({"收盘": closes})

    spot = spot_info or {}
    spot["curr_price"] = curr_price
    engine = QuantFactorsEngine(df, spot, klines_info=klines_info)
    tech = engine.calculate_technical_factors()
    return engine.calculate_scores(tech)

calculate_factors = calculate_stock_scores
get_scores = calculate_stock_scores
analyze_stock = calculate_stock_scores
