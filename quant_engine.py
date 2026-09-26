import requests
import pandas as pd
import numpy as np
import json

class ReliableAShareEngine:
    def __init__(self, raw_symbol: str):
        self.raw_symbol = raw_symbol.strip()
        self.symbol = self._format_symbol(self.raw_symbol)
        self.name = "未知标的"
        self.spot_info = {}
        self.kline_df = None

    def _format_symbol(self, code: str) -> str:
        """转为通用代码：上海为 sh601318，深圳为 sz002475"""
        code = code.lower()
        if code.startswith(("sh", "sz", "bj")):
            return code
        if code.startswith(("60", "68", "90")):
            return f"sh{code}"
        elif code.startswith(("00", "30", "20")):
            return f"sz{code}"
        elif code.startswith(("43", "83", "87", "92")):
            return f"bj{code}"
        return f"sh{code}"

    def fetch_data(self):
        """直连拉取实时快照与历史K线"""
        print(f"正在拉取标的 [{self.symbol}] 的全量量化数据...")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

        # 1. 腾讯直连：获取实时最新价、PE、PB、换手率等（毫秒级）
        spot_url = f"https://qt.gtimg.cn/q={self.symbol}"
        res = requests.get(spot_url, headers=headers, timeout=8)
        res.encoding = "gbk"
        
        if "~" in res.text:
            f = res.text.split("~")
            if len(f) > 46:
                self.name = str(f)
                
                def to_float(idx, default=0.0):
                    try:
                        return float(f[idx]) if f[idx] else default
                    except:
                        return default

                self.spot_info = {
                    "代码": str(f),
                    "最新价": to_float(3),
                    "昨收": to_float(4),
                    "涨跌幅(%)": to_float(32),
                    "成交量(手)": to_float(6),
                    "换手率(%)": to_float(38),
                    "动态市盈率(PE)": to_float(39, "N/A"),
                    "市净率(PB)": to_float(46, "N/A"),
                    "总市值(亿元)": to_float(45, "N/A")
                }

        # 2. 新浪直连：获取过去 300 个交易日历史日K线（纯JSON，永不报错）
        kline_url = f"https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData?symbol={self.symbol}&scale=240&ma=no&datalen=300"
        k_res = requests.get(kline_url, headers=headers, timeout=8)
        k_data = k_res.json()
        
        if not k_data or not isinstance(k_data, list):
            raise ValueError(f"未能获取到 {self.symbol} 的历史K线，请检查代码。")

        # 组装为标准数据表
        df = pd.DataFrame(k_data)
        df = df.rename(columns={"day": "日期", "open": "开盘", "close": "收盘", "high": "最高", "low": "最低", "volume": "成交量"})
        for col in ["收盘", "最高", "最低", "成交量"]:
            df[col] = df[col].astype(float)
        self.kline_df = df

    def calc_technical_factors(self) -> dict:
        """维度一：技术与动量因子（短/中线）"""
        df = self.kline_df
        close = df["收盘"]
        volume = df["成交量"]

        ma5 = close.rolling(5).mean().iloc[-1]
        ma20 = close.rolling(20).mean().iloc[-1]
        ma60 = close.rolling(60).mean().iloc[-1]
        ma120 = close.rolling(120).mean().iloc[-1]
        latest_close = close.iloc[-1]

        # 趋势得分：均线多头排列
        trend_score = 0
        if latest_close > ma5 > ma20: trend_score += 40
        if ma20 > ma60: trend_score += 30
        if ma60 > ma120: trend_score += 30

        # 短线动量 RSI(14)
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / (loss + 1e-9)
        rsi = 100 - (100 / (1 + rs)).iloc[-1]

        # 20日涨跌动量
        momp_20 = (latest_close / close.iloc[-20] - 1) * 100 if len(close) >= 20 else 0
        
        # 量比计算 (当日量 / 前5日平均量)
        vol_ratio = volume.iloc[-1] / (volume.tail(6).iloc[:-1].mean() + 1e-9)

        return {
            "最新收盘价": round(latest_close, 2),
            "均线形态(MA5/20/60)": f"{round(ma5, 2)} / {round(ma20, 2)} / {round(ma60, 2)}",
            "均线多头得分": trend_score,
            "RSI_14": round(rsi, 2),
            "20日动量(%)": round(momp_20, 2),
            "量比": round(vol_ratio, 2),
            "今日换手率(%)": self.spot_info.get("换手率(%)", 0.0)
        }

    def calc_risk_factors(self) -> dict:
        """维度二：波动与风险控制因子（全周期风控）"""
        df = self.kline_df
        close = df["收盘"]

        # 年化波动率
        daily_returns = close.pct_change().dropna()
        annual_volatility = daily_returns.tail(250).std() * np.sqrt(250) * 100

        # 近1年最大回撤
        rolling_max = close.tail(250).cummax()
        drawdown = (close.tail(250) - rolling_max) / rolling_max
        max_drawdown = drawdown.min() * 100

        # 真实波幅 ATR(14)
        high = df["最高"]
        low = df["最低"]
        tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
        atr = tr.rolling(14).mean().iloc[-1]

        return {
            "年化波动率(%)": round(annual_volatility, 2),
            "近1年最大回撤(%)": round(max_drawdown, 2),
            "ATR_14(真实波幅)": round(atr, 2)
        }

    def evaluate_investment_horizon(self, tech, risk, spot) -> dict:
        """维度三：三大持有周期适应度打分 (0-100分)"""
        # 短线 T+1 交易打分
        t1_score = 0
        try:
            vol_ratio = tech["量比"]
            rsi = tech["RSI_14"]
            turnover = spot.get("换手率(%)", 0.0)
            if vol_ratio >= 1.2: t1_score += 35
            if 48 <= rsi <= 75: t1_score += 35
            if tech["均线多头得分"] >= 70: t1_score += 30
        except: pass

        # 中线波段趋势打分
        mid_score = tech["均线多头得分"] * 0.5 + (50 if abs(risk["近1年最大回撤(%)"]) < 25 else 20) * 0.5

        # 长线价值配置打分
        long_score = 50
        try:
            pe = spot.get("动态市盈率(PE)", "N/A")
            mv = spot.get("总市值(亿元)", 0)
            if isinstance(pe, (int, float)):
                if 0 < pe <= 20: long_score += 30
                elif pe <= 35: long_score += 15
            if isinstance(mv, (int, float)) and mv > 300:
                long_score += 20
        except: pass

        return {
            "短线T+1交易评分": round(float(t1_score), 1),
            "中线波段趋势评分": round(float(mid_score), 1),
            "长线价值配置评分": round(float(long_score), 1)
        }

    def run(self):
        self.fetch_data()
        tech = self.calc_technical_factors()
        risk = self.calc_risk_factors()
        scores = self.evaluate_investment_horizon(tech, risk, self.spot_info)

        print("\n" + "="*52)
        print(f"  【{self.name} ({self.symbol})】全维度量化分析报告")
        print("="*52)
        print("\n[🎯 周期适用度评估 (0-100分)]")
        for k, v in scores.items():
            print(f"  • {k}: {v} 分")
            
        print("\n[📈 技术与动量因子 (短/中线)]")
        for k, v in tech.items():
            print(f"  • {k}: {v}")
            
        print("\n[🛡️ 风险与波动因子 (全周期风控)]")
        for k, v in risk.items():
            print(f"  • {k}: {v}")
            
        print("\n[💰 基本面估值 (长线核心)]")
        for k in ["动态市盈率(PE)", "市净率(PB)", "总市值(亿元)"]:
            print(f"  • {k}: {self.spot_info.get(k, 'N/A')}")
        print("="*52 + "\n")

# ========= 启动执行 =========
if __name__ == "__main__":
    engine = ReliableAShareEngine(raw_symbol="601318")
    engine.run()