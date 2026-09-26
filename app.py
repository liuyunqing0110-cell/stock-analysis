import streamlit as st
import requests
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import sys
import os

# 导入自定义模块
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from modules.ai_advisor import AIAdvisor

# ----------------- 页面配置 -----------------
st.set_page_config(page_title="A股全维度量化与热点决策系统", layout="wide", initial_sidebar_state="expanded")

st.title("🚀 A股全维度量化分析与热点决策辅助系统")
st.caption("实时直连行情 + DeepSeek 大模型投研大脑 | 覆盖热点研判、T+1短线与长线量化诊断")

# 顶层功能标签页
tab_hotspot, tab_single = st.tabs(["🔥 市场热点预测与股票推荐", "📊 个股全维度量化与K线诊断"])

# 安全取值辅助函数（彻底避开方括号过滤）
def safe_get(lst, index, default=""):
    try:
        return lst[index]
    except Exception:
        return default

def safe_float(lst, index, default=0.0):
    try:
        val = lst[index]
        return float(val) if val else default
    except Exception:
        return default

# =========================================================================
# TAB 1: 市场热点预测与三层标的推荐
# =========================================================================
with tab_hotspot:
    st.write("### 🌐 核心赛道与热点题材雷达")
    st.caption("量化异动监控结合宏观与产业逻辑，对高关注度热点进行生命周期研判")

    HOTSPOTS_DATA = {
        "AI 算力与半导体芯片": {
            "驱动属性": "产业突破 + 海外映射",
            "热度评级": "⭐⭐⭐⭐⭐ (极高)",
            "持续性研判": "【主线级行情】行业资本开支高增，景气度可维持 2-3 个季度，非一日游题材。",
            "驱动逻辑": "全球科技巨头持续加码算力基础设施投入，先进制程与自主可控催化剂密集，资金介入深。",
            "风险提示": "板块估值阶段性偏高，警惕外盘科技股急跌带来的高开低走。",
            "进攻龙头(T+1)": {"名称": "中芯国际", "代码": "688981", "属性": "晶圆代工绝对龙头，资金关注度极高"},
            "稳健中军(中长线)": {"名称": "浪潮信息", "代码": "000977", "属性": "AI服务器核心供应商，具备业绩支撑"},
            "高性价比低价股": {"名称": "通富微电", "代码": "002156", "属性": "先进封测龙头，单价适中，弹性充沛"}
        },
        "高股息红利与低估值央国企": {
            "驱动属性": "防御避险 + 现金流重估",
            "热度评级": "⭐⭐⭐⭐ (稳健)",
            "持续性研判": "【长牛底仓配置】长线增量资金（险资、社保、养老金）持续配置，长线趋势稳定。",
            "驱动逻辑": "低利率环境下高股息资产具备极强确定性，央国企市值管理考核推进，分红率与分红意愿提升。",
            "风险提示": "进攻弹性较小，在市场成交量破万亿的普涨行情中容易跑输成长板块。",
            "进攻龙头(T+1)": {"名称": "中国神华", "代码": "601088", "属性": "现金流充沛，高分红标杆，波段回踩买点明确"},
            "稳健中军(中长线)": {"名称": "长江电力", "代码": "600900", "属性": "防御天花板，水电护城河深厚，长牛慢升"},
            "高性价比低价股": {"名称": "农业银行", "代码": "601288", "属性": "单价仅4~5元，股息率超5%，破净大底极度抗跌"}
        },
        "新能源汽车与智能驾驶": {
            "驱动属性": "技术奇点 + 出海增长",
            "热度评级": "⭐⭐⭐⭐ (活跃)",
            "持续性研判": "【波段主升行情】车企出海盈利超预期，高阶智驾普及率进入爆发拐点。",
            "驱动逻辑": "国内以旧换新政策支撑销量，海外市占率快速提升，端到端大模型赋能智驾体验落地。",
            "风险提示": "海外贸易关税政策扰动，车企价格战导致毛利率波动。",
            "进攻龙头(T+1)": {"名称": "立讯精密", "代码": "002475", "属性": "精密制造与汽车电子高弹性，放量突破形态好"},
            "稳健中军(中长线)": {"名称": "比亚迪", "代码": "002594", "属性": "整车规模效应显现，全产业链垂直整合壁垒高"},
            "高性价比低价股": {"名称": "赛力斯", "代码": "601127", "属性": "华为智驾核心伙伴，回调至均线具备极强波段赔率"}
        },
        "大金融与券商互金": {
            "驱动属性": "市场成交量放大 + 牛市风向标",
            "热度评级": "⭐⭐⭐⭐ (强弹性)",
            "持续性研判": "【事件驱动脉冲】两市成交额维持高位时具备强爆发力，适合右侧量价跟进。",
            "驱动逻辑": "市场交易情绪回暖直接利好经纪与两融业务，资本市场政策鼓励中长期资金入市。",
            "风险提示": "波动极大，若成交额萎缩易出现大幅回撤。",
            "进攻龙头(T+1)": {"名称": "东方财富", "代码": "300059", "属性": "互金核心散户风向标，短线成交量激增时必看"},
            "稳健中军(中长线)": {"名称": "中国平安", "代码": "601318", "属性": "低估值破净大蓝筹，资产端随市场回暖弹性修复"},
            "高性价比低价股": {"名称": "申万宏源", "代码": "000166", "属性": "低单价中央汇金直属券商，安全边际极高"}
        }
    }

    # 左右双栏排版（纯数字 2，绝不报错）
    c_left, c_right = st.columns(2)
    with c_left:
        selected_theme = st.radio("👉 选择要剖析的市场热点：", list(HOTSPOTS_DATA.keys()))
    
    theme_info = HOTSPOTS_DATA.get(selected_theme)

    with c_right:
        st.markdown(f"#### 🔎 【{selected_theme}】深度研判报告")
        col_t1, col_t2 = st.columns(2)
        col_t1.info(f"**驱动属性**：{theme_info['驱动属性']}")
        col_t2.success(f"**热度评级**：{theme_info['热度评级']}")
        
        st.write(f"**⚡ 持续性研判**：{theme_info['持续性研判']}")
        st.write(f"**💡 核心驱动逻辑**：{theme_info['驱动逻辑']}")
        st.warning(f"**⚠️ 潜在风险提示**：{theme_info['风险提示']}")

    st.divider()

    # 三层标的推荐
    st.write(f"### 🎯 【{selected_theme}】精选推荐梯队")
    col_aggr, col_stab, col_cheap = st.columns(3)
    
    with col_aggr:
        rec_a = theme_info["进攻龙头(T+1)"]
        st.markdown("#### ⚡ 1. 进攻龙头 (T+1)")
        st.write(f"**{rec_a['名称']} ({rec_a['代码']})**")
        st.caption(rec_a['属性'])
        st.markdown("• **策略**：短线博弈，冲高 +3%~+4.5% 分批出，破位 -2% 止损。")

    with col_stab:
        rec_s = theme_info["稳健中军(中长线)"]
        st.markdown("#### 🛡️ 2. 稳健中军 (长线)")
        st.write(f"**{rec_s['名称']} ({rec_s['代码']})**")
        st.caption(rec_s['属性'])
        st.markdown("• **策略**：回调至 20/60 日均线分批低吸，目标 +10%~+15%。")

    with col_cheap:
        rec_c = theme_info["高性价比低价股"]
        st.markdown("#### 💰 3. 高性价比低价股")
        st.write(f"**{rec_c['名称']} ({rec_c['代码']})**")
        st.caption(rec_c['属性'])
        st.markdown("• **策略**：单价亲民、低估值安全垫厚，向上赔率极大。")

# =========================================================================
# TAB 2: 单个股票全维度量化分析 + DeepSeek 操盘手内参
# =========================================================================
with tab_single:
    PRESET_STOCKS = {
        "【金融/长线底仓】中国平安 (601318)": "601318",
        "【白酒/核心资产】贵州茅台 (600519)": "600519",
        "【高股息/稳健红利】长江电力 (600900)": "600900",
        "【高股息/能源龙头】中国神华 (601088)": "601088",
        "【消费电子/果链】立讯精密 (002475)": "002475",
        "【新能源/动力电池】宁德时代 (300750)": "300750",
        "【新能源车/整车龙头】比亚迪 (002594)": "002594",
        "【芯片半导体/制造】中芯国际 (688981)": "688981",
        "【券商互联网/弹性标的】东方财富 (300059)": "300059",
        "【人工智能/AI算力】浪潮信息 (000977)": "000977",
        "✏️ 手动输入其他股票代码": "CUSTOM"
    }

    with st.sidebar:
        st.header("🎯 单股量化检索")
        selected_option = st.selectbox("选择要分析的个股：", list(PRESET_STOCKS.keys()), index=0)
        
        if PRESET_STOCKS.get(selected_option) == "CUSTOM":
            target_code = st.text_input("输入 6 位 A 股代码", value="600519")
        else:
            target_code = PRESET_STOCKS.get(selected_option, "601318")
            st.info(f"已选代码: **{target_code}**")
            
        kline_days = st.slider("K 线展示周期（交易日）", min_value=30, max_value=250, value=90, step=10)
        btn_run = st.button("🚀 刷新数据", type="primary", use_container_width=True)

    def fetch_and_analyze(code_str: str):
        code = code_str.strip().lower()
        if not code.startswith(("sh", "sz", "bj")):
            if code.startswith(("60", "68", "90")): symbol = f"sh{code}"
            elif code.startswith(("00", "30", "20")): symbol = f"sz{code}"
            elif code.startswith(("43", "83", "87", "92")): symbol = f"bj{code}"
            else: symbol = f"sh{code}"
        else:
            symbol = code

        headers = {"User-Agent": "Mozilla/5.0"}
        
        # 1. 实时行情与估值
        spot_url = f"https://qt.gtimg.cn/q={symbol}"
        res = requests.get(spot_url, headers=headers, timeout=8)
        res.encoding = "gbk"
        
        if "~" not in res.text: return None, "未能获取到实时行情。"
        f = res.text.split("~")
        if len(f) <= 46: return None, "数据解析异常。"
        
        spot_info = {
            "名称": safe_get(f, 1, "未知"),
            "代码": safe_get(f, 2, code),
            "最新价": safe_float(f, 3),
            "昨收": safe_float(f, 4),
            "涨跌幅(%)": safe_float(f, 32),
            "换手率(%)": safe_float(f, 38),
            "动态市盈率(PE)": safe_float(f, 39, "N/A"),
            "市净率(PB)": safe_float(f, 46, "N/A"),
            "总市值(亿元)": safe_float(f, 45, "N/A")
        }

        # 2. 历史日K线
        kline_url = f"https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData?symbol={symbol}&scale=240&ma=no&datalen=300"
        k_res = requests.get(kline_url, headers=headers, timeout=8)
        k_data = k_res.json()
        
        if not k_data or not isinstance(k_data, list): return None, "未能获取到历史K线。"

        df = pd.DataFrame(k_data)
        df = df.rename(columns={"day": "日期", "open": "开盘", "close": "收盘", "high": "最高", "low": "最低", "volume": "成交量"})
        for col in ["开盘", "收盘", "最高", "最低", "成交量"]: df[col] = df[col].astype(float)

        close = df["收盘"]
        volume = df["成交量"]
        latest_close = close.iloc[-1]
        
        df["MA5"] = close.rolling(5).mean()
        df["MA20"] = close.rolling(20).mean()
        df["MA60"] = close.rolling(60).mean()
        
        ma5 = df["MA5"].iloc[-1]
        ma20 = df["MA20"].iloc[-1]
        ma60 = df["MA60"].iloc[-1]
        
        trend_score = 0
        if latest_close > ma5 > ma20: trend_score += 40
        if ma20 > ma60: trend_score += 30
        if latest_close > ma60: trend_score += 30

        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / (loss + 1e-9)
        rsi = 100 - (100 / (1 + rs)).iloc[-1]

        vol_ratio = volume.iloc[-1] / (volume.tail(6).iloc[:-1].mean() + 1e-9)
        
        daily_returns = close.pct_change().dropna()
        annual_vol = daily_returns.tail(250).std() * np.sqrt(250) * 100
        rolling_max = close.tail(250).cummax()
        max_dd = ((close.tail(250) - rolling_max) / rolling_max).min() * 100

        t1_score = 0
        turnover = spot_info.get("换手率(%)", 0.0)
        if vol_ratio >= 1.2: t1_score += 35
        if 48 <= rsi <= 75: t1_score += 35
        if trend_score >= 70: t1_score += 30

        mid_score = trend_score * 0.5 + (50 if abs(max_dd) < 25 else 20) * 0.5

        long_score = 50
        pe = spot_info.get("动态市盈率(PE)", "N/A")
        mv = spot_info.get("总市值(亿元)", 0)
        if isinstance(pe, (int, float)):
            if 0 < pe <= 20: long_score += 30
            elif pe <= 35: long_score += 15
        if isinstance(mv, (int, float)) and mv > 300:
            long_score += 20

        return {
            "spot": spot_info,
            "symbol": symbol,
            "kline": df,
            "tech": {
                "最新收盘价": round(latest_close, 2),
                "均线多头得分": trend_score,
                "RSI_14": round(rsi, 2),
                "量比": round(vol_ratio, 2),
                "MA5": round(ma5, 2),
                "MA20": round(ma20, 2),
                "MA60": round(ma60, 2)
            },
            "risk": {
                "年化波动率(%)": round(annual_vol, 2),
                "近1年最大回撤(%)": round(max_dd, 2)
            },
            "scores": {
                "短线T+1交易": round(float(t1_score), 1),
                "中线波段趋势": round(float(mid_score), 1),
                "长线价值配置": round(float(long_score), 1)
            }
        }, None

    def create_candlestick_chart(df, days):
        plot_df = df.tail(days).copy()
        fig = make_subplots(
            rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[0.75, 0.25],
            subplot_titles=("日K线与均线系统 (MA5 / MA20 / MA60)", "成交量 (Volume)")
        )
        fig.add_trace(go.Candlestick(
            x=plot_df["日期"], open=plot_df["开盘"], high=plot_df["最高"], low=plot_df["最低"], close=plot_df["收盘"],
            name="日K", increasing_line_color="#ef5350", decreasing_line_color="#26a69a"
        ), row=1, col=1)
        fig.add_trace(go.Scatter(x=plot_df["日期"], y=plot_df["MA5"], line=dict(color="#ff9800", width=1.5), name="MA5"), row=1, col=1)
        fig.add_trace(go.Scatter(x=plot_df["日期"], y=plot_df["MA20"], line=dict(color="#2196f3", width=1.5), name="MA20"), row=1, col=1)
        fig.add_trace(go.Scatter(x=plot_df["日期"], y=plot_df["MA60"], line=dict(color="#9c27b0", width=1.5), name="MA60"), row=1, col=1)
        colors = ["#ef5350" if r["收盘"] >= r["开盘"] else "#26a69a" for _, r in plot_df.iterrows()]
        fig.add_trace(go.Bar(x=plot_df["日期"], y=plot_df["成交量"], marker_color=colors, name="成交量"), row=2, col=1)
        fig.update_layout(xaxis_rangeslider_visible=False, height=500, margin=dict(l=10, r=10, t=30, b=10))
        return fig

    # 渲染单股数据
    with st.spinner("正在加载个股数据..."):
        data, err = fetch_and_analyze(target_code)

    if err:
        st.error(err)
    else:
        spot = data["spot"]
        scores = data["scores"]
        tech = data["tech"]
        risk = data["risk"]

        st.subheader(f"📌 {spot['名称']} ({data['symbol']})")
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("最新价", f"{spot['最新价']} 元", f"{spot['涨跌幅(%)']}%")
        c2.metric("换手率", f"{spot['换手率(%)']}%")
        c3.metric("动态市盈率(PE)", f"{spot['动态市盈率(PE)']}")
        c4.metric("市净率(PB)", f"{spot['市净率(PB)']}")
        c5.metric("总市值", f"{spot['总市值(亿元)']} 亿")

        st.divider()

        # 专业 K 线图
        st.write("### 🕯️ 交互式日 K 线走势图")
        st.plotly_chart(create_candlestick_chart(data["kline"], kline_days), use_container_width=True)

        st.divider()

        # 三大周期量化评分卡片
        st.write("### 🎯 三大投资周期量化评分")
        sc1, sc2, sc3 = st.columns(3)
        with sc1:
            st.markdown("#### ⚡ 短线 T+1 交易")
            st.progress(scores["短线T+1交易"] / 100)
            st.metric("评分", f"{scores['短线T+1交易']} 分")
        with sc2:
            st.markdown("#### 🌊 中线波段趋势")
            st.progress(scores["中线波段趋势"] / 100)
            st.metric("评分", f"{scores['中线波段趋势']} 分")
        with sc3:
            st.markdown("#### 💎 长线价值配置")
            st.progress(scores["长线价值配置"] / 100)
            st.metric("评分", f"{scores['长线价值配置']} 分")

        st.divider()

        # 🔥 DeepSeek 操盘手深度内参板块
        st.write("### 🤖 DeepSeek AI 操盘手深度实战内参")
        st.caption("基于以上多因子量化指标，调用 DeepSeek 大模型生成老股民专属实战策略与涨乐富挂单点位")
        
        btn_deepseek = st.button("🚀 立即生成 DeepSeek 深度投研内参", type="primary", use_container_width=True)
        
        if btn_deepseek:
            with st.spinner("🤖 DeepSeek 首席操盘手正在深度审阅盘口、均线与估值数据..."):
                try:
                    advisor = AIAdvisor()
                    ai_report = advisor.generate_analysis(
                        stock_name=spot["名称"],
                        symbol=data["symbol"],
                        spot=spot,
                        tech=tech,
                        risk=risk,
                        scores=scores
                    )
                    st.success("✅ DeepSeek 操盘手内参生成完毕：")
                    st.markdown(ai_report)
                except Exception as e:
                    st.error(f"调用 DeepSeek 失败: {e}")