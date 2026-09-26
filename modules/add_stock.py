import os
import json
import requests
import urllib.parse
import re

def search_stock(keyword):
    """采用东方财富原生 UTF-8 联想接口，中文搜股 100% 极速精准"""
    keyword = str(keyword).strip()
    if not keyword: return []
    
    url = f"https://searchapi.eastmoney.com/api/suggest/get?input={urllib.parse.quote(keyword)}&type=14"
    try:
        res = requests.get(url, timeout=4).json()
        raw_list = res.get("QuotationCodeTable", {}).get("Data", [])
        if not raw_list: return []
        
        matches = []
        for item in raw_list[:5]:
            code = item.get("Code", "")
            name = item.get("Name", "")
            market_type = item.get("SecurityTypeName", "")
            
            prefix = "sh" if code.startswith("6") or code.startswith("9") else "sz" if code.startswith("0") or code.startswith("3") else "bj"
            symbol = f"{prefix}{code}"
            
            # 抓取现价供参考
            price = 0.0
            try:
                q = requests.get(f"http://hq.sinajs.cn/list={symbol}", headers={"Referer": "https://finance.sina.com.cn/"}, timeout=2)
                m = re.search(r'="([^"]+)"', q.text)
                if m:
                    fields = m.group(1).split(",")
                    idx_price = 3
                    if len(fields) > idx_price and fields[idx_price]:
                        price = float(fields[idx_price])
            except: pass
            
            matches.append({
                "code": code,
                "name": name,
                "symbol": symbol,
                "market": market_type,
                "price": price
            })
        return matches
    except Exception as e:
        print(f"搜索服务连接中: {e}")
        return []

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(script_dir)
    
    target_files = [
        os.path.join(script_dir, "my_portfolio.json"),
        os.path.join(root_dir, "my_portfolio.json")
    ]

    portfolio = []
    for tf in target_files:
        if os.path.exists(tf):
            try:
                with open(tf, "r", encoding="utf-8") as f:
                    portfolio = json.load(f)
                break
            except:
                pass

    print("\n" + "="*50)
    print("📈 A 股持仓【智能辅助录入工具 - 东方财富专线】")
    print("="*50)
    
    while True:
        query = input("\n👉 请输入股票名称、拼音或代码（如 '掌趣'、'BYD'、'300315'）：").strip()
        if not query: return
        
        print("🔍 正在联想匹配...")
        matches = search_stock(query)
        if not matches:
            print("❌ 未找到匹配股票，请重新输入关键字！")
            continue
            
        print("\n找到以下匹配结果：")
        for i, m in enumerate(matches, 1):
            print(f"  [{i}] {m['name']} ({m['code']}) - [{m['market']}]  当前参考现价: {m['price']} 元")
            
        choice_idx = 0
        if len(matches) > 1:
            c_in = input(f"\n请选择对应序号 1~{len(matches)} (默认直接回车选第 1 项): ").strip()
            if c_in.isdigit() and 1 <= int(c_in) <= len(matches):
                choice_idx = int(c_in) - 1
                
        target = matches[choice_idx]
        print(f"\n✅ 已选定: 【{target['name']} ({target['code']})】 当前现价: {target['price']} 元")
        
        # 成本价防呆输入（敲文字不会崩溃，提示重新输入）
        while True:
            cost_in = input(f"请输入您的买入成本价 (直接回车默认按现价 {target['price']} 元): ").strip()
            if not cost_in:
                cost = target['price']
                break
            try:
                cost = float(cost_in)
                break
            except ValueError:
                print("⚠️ 成本价必须是纯数字（如 5.25），请重新输入！")
                
        # 股数输入
        shares_in = input("请输入持仓股数 (直接回车默认 100 股): ").strip()
        shares = int(shares_in) if shares_in.isdigit() else 100
        
        print("\n请选择投资周期: 1.短线T+1  2.波段中线  3.长线价值")
        cycle_in = input("选择周期 (默认回车选 2): ").strip()
        cycle_map = {"1": "短线T+1", "2": "波段中线", "3": "长线价值"}
        cycle = cycle_map.get(cycle_in, "波段中线")
        
        # 更新或添加
        updated = False
        for p in portfolio:
            if p.get("代码") == target["code"] or p.get("名称") == target["name"]:
                p.update({"代码": target["code"], "名称": target["name"], "成本价": cost, "持仓股数": shares, "投资周期": cycle})
                updated = True
                break
                
        if not updated:
            portfolio.append({
                "代码": target["code"],
                "名称": target["name"],
                "成本价": cost,
                "持仓股数": shares,
                "投资周期": cycle
            })
            
        for tf in target_files:
            try:
                with open(tf, "w", encoding="utf-8") as f:
                    json.dump(portfolio, f, ensure_ascii=False, indent=2)
            except:
                pass
            
        print(f"\n🎉 成功将【{target['name']} ({target['code']})】保存到持仓！")
        print(f"📁 持仓已同步更新（当前共追踪 {len(portfolio)} 只股票）")
        print("="*50)
        break

if __name__ == "__main__":
    main()