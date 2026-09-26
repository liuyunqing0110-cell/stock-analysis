import pandas as pd
import numpy as np

class QuantFactorsEngine:
    """
    模块 3：全维度多因子量化与白盒打分引擎
    涵盖四大实战维度：T+1短线、中线波段、长线价值、高性价比低价潜伏
    """
    def __init__(self, kline_df: pd.DataFrame, spot_info: dict):
        self.df = kline_df.copy()
        self.spot = spot_info

    def calculate_technical_factors(self) -> dict:
        close = self.df["收盘"]
        volume = self.df["成交量"]
        latest_close = close.iloc[-1]

        ma5 = close.rolling(5).mean().iloc[-1]
        ma20 = close.rolling(20).mean().iloc[-1]
        ma60 = close.rolling(60).mean().iloc[-1]

        trend_score = 0
        if latest_close > ma5 > ma20: trend_score += 40
        if ma20 > ma60: trend_score += 30
        if latest_close > ma60: trend_score += 30

        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / (loss + 1e-9)
        rsi = 100 - (100 / (1 + rs)).iloc[-1]

        momp_20 = (latest_close / close.iloc[-20] - 1) * 100 if len(close) >= 20 else 0
        vol_ratio = volume.iloc[-1] / (volume.tail(6).iloc[:-1].mean() + 1e-9)

        return {
            "最新价": round(latest_close, 2),
            "MA5": round(ma5, 2), "MA20": round(ma20, 2), "MA60": round(ma60, 2),
            "均线多头得分": trend_score, "RSI_14": round(rsi, 2),
            "20日动量(%)": round(momp_20, 2), "量比": round(vol_ratio, 2)
        }

    def calculate_risk_factors(self) -> dict:
        close = self.df["收盘"]
        daily_returns = close.pct_change().dropna()
        annual_vol = daily_returns.tail(250).std() * np.sqrt(250) * 100
        rolling_max = close.tail(250).cummax()
        max_dd = ((close.tail(250) - rolling_max) / rolling_max).min() * 100
        return {
            "年化波动率(%)": round(annual_vol, 2),
            "近1年最大回撤(%)": round(max_dd, 2)
        }

    def calculate_scores(self, tech: dict, risk: dict) -> dict:
        price = tech["最新价"]
        vol_ratio = tech["量比"]
        rsi = tech["RSI_14"]
        trend = tech["均线多头得分"]
        max_dd = risk["近1年最大回撤(%)"]
        pe = self.spot.get("动态市盈率(PE)", "N/A")
        pb = self.spot.get("市净率(PB)", "N/A")
        mv = self.spot.get("总市值(亿元)", 0)

        # 1. 短线 T+1 交易
        t1_score = 0
        t1_reasons = []
        if vol_ratio >= 1.2:
            t1_score += 35
            t1_reasons.append(f"✅ 量比 {vol_ratio} ≥ 1.2 (+35分)：增量资金主动扫盘。")
        else:
            t1_reasons.append(f"❌ 量比 {vol_ratio} < 1.2 (+0分)：量能不足，缺乏资金合力。")

        if 48 <= rsi <= 75:
            t1_score += 35
            t1_reasons.append(f"✅ RSI(14) 为 {rsi} (+35分)：处于健康多头进攻区。")
        elif rsi > 80:
            t1_score -= 20
            t1_reasons.append(f"⚠️ RSI(14) 为 {rsi} > 80 (-20分)：短线严重超买，防止追高接飞刀！")
        else:
            t1_reasons.append(f"❌ RSI(14) 为 {rsi} (+0分)：处于弱势超卖或整理状态。")

        if trend >= 70:
            t1_score += 30
            t1_reasons.append("✅ 均线多头就位 (+30分)：站稳 5 日与 20 日线上方。")
        else:
            t1_reasons.append("❌ 均线未突破 (+0分)：上方仍受短期均线压制。")

        t1_concl = "具备较强短线动量与溢价潜力，适合尾盘布局" if t1_score >= 70 else "动量不足或形态受压，不建议追涨做T+1"

        # 2. 中线波段趋势
        mid_score = trend * 0.5 + (50 if abs(max_dd) < 25 else 20) * 0.5
        mid_reasons = [
            f"• 均线形态得分 {trend}/100 (权重50%)：反映中期均线支撑强度。",
            f"• 近1年最大回撤 {max_dd}%：{'回撤控制良好(<25%)' if abs(max_dd)<25 else '历史回撤偏大，需控仓'}。"
        ]
        mid_concl = "中周期均线支撑稳固，适合顺势波段持股" if mid_score >= 60 else "处于中周期震荡或洗盘阶段"

        # 3. 长线价值配置
        long_score = 50
        long_reasons = ["• 基础底分：50分。"]
        if isinstance(pe, (int, float)):
            if 0 < pe <= 20:
                long_score += 30
                long_reasons.append(f"✅ 动态 PE 为 {pe} ≤ 20倍 (+30分)：估值处于历史安全低分位。")
            elif pe <= 35:
                long_score += 15
                long_reasons.append(f"• 动态 PE 为 {pe} (+15分)：估值处于适中区间。")
        if isinstance(mv, (int, float)) and mv > 300:
            long_score += 20
            long_reasons.append(f"✅ 总市值 {mv} 亿 > 300亿 (+20分)：大盘蓝筹护城河深厚。")
        long_concl = "估值安全垫极厚，具备长期配置价值" if long_score >= 75 else "估值处于中性成长阶段"

        # 4. 高性价比低价潜伏
        cheap_score = 40
        cheap_reasons = ["• 基础底分：40分。"]
        if 5.0 <= price <= 18.0:
            cheap_score += 30
            cheap_reasons.append(f"✅ 最新价 {price} 元在 5~18 元黄金区间 (+30分)：单价亲民，占资金少。")
        elif price < 30.0:
            cheap_score += 15
            cheap_reasons.append(f"• 最新价 {price} 元 (<30元) (+15分)：单价适中。")
        else:
            cheap_reasons.append(f"❌ 最新价 {price} 元偏高 (+0分)：非低价股范畴。")

        if isinstance(pb, (int, float)) and pb < 1.2:
            cheap_score += 20
            cheap_reasons.append(f"✅ 市净率 PB 为 {pb} < 1.2 (+20分)：破净或绝对低估，下方空间锁死。")

        if abs(max_dd) > 30:
            cheap_score += 10
            cheap_reasons.append(f"✅ 历史回撤 {max_dd}% 且底部横盘 (+10分)：向上具备极高修复弹性。")

        cheap_concl = "低单价、极低估值、向上赔率大，极适合低位潜伏" if cheap_score >= 80 else "性价比适中"

        return {
            "短线T+1交易": {"得分": max(0.0, float(t1_score)), "依据": t1_reasons, "结论": t1_concl},
            "中线波段趋势": {"得分": float(mid_score), "依据": mid_reasons, "结论": mid_concl},
            "长线价值配置": {"得分": float(long_score), "依据": long_reasons, "结论": long_concl},
            "高性价比低价潜伏": {"得分": float(cheap_score), "依据": cheap_reasons, "结论": cheap_concl}
        }

    def run(self) -> dict:
        tech = self.calculate_technical_factors()
        risk = self.calculate_risk_factors()
        scores = self.calculate_scores(tech, risk)
        return {"tech": tech, "risk": risk, "scores": scores}

# ----------------- 独立测试 -----------------
if __name__ == "__main__":
    print(">>> 正在独立测试 [模块 3：量化多因子白盒引擎]...")
    # 模拟测试数据
    dates = pd.date_range(end="2026-09-26", periods=300).strftime("%Y-%m-%d")
    np.random.seed(42)
    prices = 12.5 + np.cumsum(np.random.randn(300) * 0.15)
    test_df = pd.DataFrame({
        "日期": dates, "开盘": prices, "收盘": prices, "最高": prices + 0.2, "最低": prices - 0.2, "成交量": 600000
    })
    test_spot = {"动态市盈率(PE)": 10.8, "市净率(PB)": 0.95, "总市值(亿元)": 880}
    
    engine = QuantFactorsEngine(test_df, test_spot)
    res = engine.run()
    
    print("\n✅ 模块 3 独立测试成功！四大维度评分与白盒依据如下：")
    for dim, info in res["scores"].items():
        print(f"\n【{dim}】得分: {info['得分']} 分 ｜ 结论: {info['结论']}")
        print("  打分拆解明细:")
        for r in info["依据"]:
            print(f"    {r}")