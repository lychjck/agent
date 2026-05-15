import json
from duckduckgo_search import DDGS

def test_search():
    query = "今日股市行情 A股 港股 美股 指数 涨跌"
    print(f"正在搜索关键词: '{query}' ...\n")
    
    try:
        with DDGS() as ddgs:
            # max_results 限制返回数量
            results = ddgs.text(query, max_results=3)
            
            if not results:
                print("没有搜索到任何结果。")
                return

            for i, r in enumerate(results, 1):
                print(f"--- 结果 {i} ---")
                print(f"标题: {r.get('title')}")
                print(f"链接: {r.get('href')}")
                print(f"摘要: {r.get('body')}")
                print("-" * 40 + "\n")
                
    except Exception as e:
        print(f"搜索过程发生异常: {e}")

if __name__ == "__main__":
    test_search()
