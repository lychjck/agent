import json
from pathlib import Path
from stock_assistant import (
    load_config,
    Holding,
    build_search_provider,
    score_classification_evidence,
    suggest_classification_with_search
)

def main():
    config = load_config(Path("config.toml"))
    
    # 我们构造一个需要调试的标的，例如 512170 医疗ETF
    holding = Holding(code="512170", name="医疗ETF")
    
    print(f"\n=== 开始调试标的: {holding.code} {holding.name} ===")
    
    # 1. 模拟搜索调用
    provider = build_search_provider(config)
    query = f"{holding.code} {holding.name} ETF 跟踪指数 行业 基金公司"
    max_results = int(config.get("search", {}).get("max_results", 5))
    
    print(f"\n[执行搜索] Query: {query}")
    results = provider.search(query, max_results)
    
    print(f"\n[搜索结果] 共返回 {len(results)} 条记录:")
    for i, res in enumerate(results, 1):
        print(f"\n--- 结果 {i} ---")
        print(f"标题: {res.get('title')}")
        print(f"链接: {res.get('url')}")
        print(f"摘要: {res.get('snippet')}")
        
    # 2. 模拟证据评分
    score = score_classification_evidence(results, config)
    print(f"\n[证据评分] 综合置信度得分: {score:.2f}")
    
    # 3. 模拟硬编码的触发逻辑
    text = " ".join(str(item.get("snippet", "")) for item in results)
    print(f"\n[关键词命雷测试]")
    if "证券公司" in text or "券商" in text:
        print(">> ⚠️ 警告: 提取的全部摘要中包含了 '证券公司' 或 '券商' 关键字，触发了硬编码的金融分类错误！")
    else:
        print(">> ✅ 正常: 未触发硬编码误判。")

if __name__ == "__main__":
    main()