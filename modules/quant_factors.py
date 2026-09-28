# -*- coding: utf-8 -*-
"""
模块 3：全维度多因子量化与白盒打分引擎 (支持方案一估值因子注入)
"""
import pandas as pd
import numpy as np

class QuantFactorsEngine:
    def __init__(self, kline_df: pd.DataFrame, spot_info: dict):
        self.df = kline_df.copy() if kline_df is not None and not kline_df.empty else None
        self.spot = spot_info or {}

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

        # 1. 短线 T+1 交易 (技术动量 80% + 流动性 20%)
        t1_score = 50
        if trend >= 70: t1_score += 25
        elif trend < 30: t1_score -= 15

        if 48 <= rsi <= 72: t1_score += 15
        elif rsi > 82: t1_score -= 20

        if 3.0 <= turnover_rate <= 12.0: t1_score += 10
        elif turnover_rate > 22.0: t1_score -= 10
        t1_score = max(20, min(95, t1_score))

        # 2. 中线波段趋势
        mid_score = max(20, min(95, int(trend * 0.7 + (25 if price >= tech["MA20"] else -15) + 15)))

        # 3. 💎 长线价值配置 (真实估值与抗风险权重)
        long_score = 60
        if pe > 0:
            if pe <= 15.0: long_score += 20      # 极高性价比
            elif pe <= 32.0: long_score += 10    # 合理估值
            elif pe > 65.0: long_score -= 18     # 估值透支
        elif pe < 0:
            long_score -= 25                     # 处于亏损状态，严厉扣分

        if 0 < pb <= 1.2: long_score += 12       # 破净强安全垫
        elif pb > 8.0: long_score -= 10

        if mv >= 500.0: long_score += 8          # 大盘蓝筹
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
            "short_term": {"score": t1_score, "tag": st_tag, "style": st_cls, "desc": "短线资金动量与换手配合良好" if t1_score>=75 else "短线震荡整理"},
            "mid_term": {"score": mid_score, "tag": mt_tag, "style": mt_cls, "desc": "波段均线支撑强劲" if mid_score>=75 else "中线通道运行中"},
            "long_term": {"score": long_score, "tag": lt_tag, "style": lt_cls, "desc": "估值安全边际深厚" if long_score>=75 else "估值处于合理偏高水平"},
            "ma5": tech["MA5"], "ma20": tech["MA20"], "ma60": tech["MA60"], "rsi": tech["RSI_14"],
            "pe": pe, "pb": pb, "total_mv": mv, "turnover_rate": turnover_rate,
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
    engine = QuantFactorsEngine(df, spot)
    tech = engine.calculate_technical_factors()
    return engine.calculate_scores(tech)

calculate_factors = calculate_stock_scores
get_scores = calculate_stock_scores
analyze_stock = calculate_stock_scores
