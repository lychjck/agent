import { useState, useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import { RefreshCw, TrendingUp, TrendingDown, Activity, DollarSign, Wallet, ShieldAlert, Cpu, Landmark, LineChart, PieChart as PieChartIcon, RotateCcw, Box, ArrowDownUp } from 'lucide-react';
import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip, AreaChart, Area, XAxis, YAxis, CartesianGrid } from 'recharts';

export default function App() {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [analyzing, setAnalyzing] = useState(false);
  const [aiData, setAiData] = useState<any>(null);
  const [analysisLogs, setAnalysisLogs] = useState<string[]>([]);
  const [currentStep, setCurrentStep] = useState(0);
  const [selectedModel, setSelectedModel] = useState('inclusionAI/Ling-2.6-1T');
  const [technicalResults, setTechnicalResults] = useState<any[] | null>(null);
  
  const MODELS = [
    { id: 'inclusionAI/Ling-2.6-1T', name: 'Ling-2.6-1T (推荐)', provider: 'ModelScope' },
    { id: 'ZhipuAI/GLM-5.1', name: 'GLM-5.1', provider: 'ModelScope' },
    { id: 'moonshotai/Kimi-K2.5', name: 'Kimi-K2.5', provider: 'ModelScope' },
    { id: 'deepseek-ai/DeepSeek-V3', name: 'DeepSeek V3', provider: 'ModelScope' },
    { id: 'deepseek-ai/DeepSeek-V4-Pro', name: 'DeepSeek V4 Pro', provider: 'ModelScope' },
  ];
  const [hasError, setHasError] = useState(false);
  
  // Sorting state
  const [etfSortBy, setEtfSortBy] = useState<'market_value' | 'profit_pct' | 'hold_profit'>('market_value');
  const [etfSortOrder, setEtfSortOrder] = useState<'desc' | 'asc'>('desc');
  const [fundSortBy, setFundSortBy] = useState<'market_value' | 'profit_pct' | 'hold_profit'>('market_value');
  const [fundSortOrder, setFundSortOrder] = useState<'desc' | 'asc'>('desc');

  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  const [klines, setKlines] = useState<any[]>([]);
  const [klineLoading, setKlineLoading] = useState(false);

  useEffect(() => {
    fetchHoldings();
  }, []);

  useEffect(() => {
    if (selectedSymbol) {
      fetchKlines(selectedSymbol);
    }
  }, [selectedSymbol]);

  const fetchHoldings = async () => {
    setLoading(true);
    try {
      const res = await fetch(`/api/holdings?_t=${Date.now()}`);
      const json = await res.json();
      setData(json);
      // 默认选中第一个持仓查看 K 线
      if (json.holdings?.length > 0) {
        setSelectedSymbol(json.holdings[0].code);
      }
    } catch (err) {
      console.error(err);
    }
    setLoading(false);
  };

  const fetchKlines = async (symbol: string) => {
    setKlineLoading(true);
    try {
      const res = await fetch(`/api/klines/daily?symbol=${symbol}`);
      const json = await res.json();
      if (json.data) {
        setKlines(json.data.slice(-60)); // 只显示最近 60 天
      }
    } catch (err) {
      console.error(err);
    }
    setKlineLoading(false);
  };

  const handleAnalyze = async (resumeData: any[] | null = null) => {
    setAnalyzing(true);
    setHasError(false);
    if (!resumeData) {
      setAiData(null);
      setAnalysisLogs([]);
      setCurrentStep(0);
      setTechnicalResults(null);
    }
    
    try {
      const response = await fetch('/api/analyze', { 
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
          cached_results: resumeData,
          model: selectedModel
        })
      });
      const reader = response.body?.getReader();
      const decoder = new TextDecoder();

      if (!reader) return;

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        const chunk = decoder.decode(value);
        const lines = chunk.split('\n');

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const payload = JSON.parse(line.substring(6));
              if (payload.status) {
                setAnalysisLogs(prev => [...prev, payload.status]);
                if (payload.step) setCurrentStep(payload.step);
              }
              if (payload.technical_results) {
                setTechnicalResults(payload.technical_results);
              }
              if (payload.result) {
                setAiData(payload.result);
                setHasError(false);
              }
              if (payload.error) {
                setAnalysisLogs(prev => [...prev, `❌ 错误: ${payload.error}`]);
                setHasError(true);
              }
            } catch (e) {
              console.error('Failed to parse SSE line:', line, e);
            }
          }
        }
      }
    } catch (err: any) {
      console.error(err);
      setAnalysisLogs(prev => [...prev, `❌ 分析过程中发生意外错误: ${err.message}`]);
      setHasError(true);
    }
    setAnalyzing(false);
  };

  const COLORS = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899', '#06b6d4'];

  const etfHoldings = [...(data?.holdings?.filter((h: any) => h.asset_type === 'etf') || [])].sort((a, b) => {
    const valA = a[etfSortBy] || 0;
    const valB = b[etfSortBy] || 0;
    return etfSortOrder === 'desc' ? valB - valA : valA - valB;
  });
  
  const fundHoldings = [...(data?.holdings?.filter((h: any) => h.asset_type === 'fund') || [])].sort((a, b) => {
    const valA = a[fundSortBy] || 0;
    const valB = b[fundSortBy] || 0;
    return fundSortOrder === 'desc' ? valB - valA : valA - valB;
  });

  const etfProfit = etfHoldings.reduce((sum: number, h: any) => sum + (h.hold_profit || 0), 0);
  const fundProfit = fundHoldings.reduce((sum: number, h: any) => sum + (h.hold_profit || 0), 0);

  return (
    <div className="min-h-screen bg-slate-950 p-4 sm:p-6 md:p-8 xl:p-12 text-slate-100 font-sans selection:bg-indigo-500/30">
      <div className="max-w-7xl mx-auto space-y-10">
        
        {/* Header Area */}
        <header className="flex flex-col md:flex-row justify-between items-start md:items-center gap-6 pb-6 border-b border-slate-800/60">
          <div className="space-y-2">
            <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-indigo-500/10 border border-indigo-500/20 text-indigo-400 text-xs font-semibold uppercase tracking-wider mb-2">
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-indigo-400 opacity-75"></span>
                <span className="relative inline-flex rounded-full h-2 w-2 bg-indigo-500"></span>
              </span>
              System Active
            </div>
            <h1 className="text-4xl md:text-5xl font-extrabold tracking-tight bg-gradient-to-br from-white via-indigo-100 to-indigo-400 bg-clip-text text-transparent flex items-center gap-3">
              AI 投资驾驶舱
            </h1>
            <p className="text-slate-400 text-sm md:text-base max-w-xl leading-relaxed">
              实时监控您的场内 ETF 与场外基金资产，结合大模型多维度深度诊断，为您提供量化与基本面结合的操作建议。
            </p>
          </div>
          
          <div className="flex flex-col gap-4">
            {/* Model Selector UI */}
            <div className="flex items-center gap-3 bg-slate-900/50 border border-slate-800 p-2 pl-4 rounded-2xl">
              <Box className="w-4 h-4 text-indigo-400" />
              <select 
                value={selectedModel}
                onChange={(e) => setSelectedModel(e.target.value)}
                disabled={analyzing}
                className="bg-transparent text-sm font-semibold text-slate-200 outline-none cursor-pointer pr-4"
              >
                {MODELS.map(m => (
                  <option key={m.id} value={m.id} className="bg-slate-900 text-slate-200">
                    {m.name}
                  </option>
                ))}
              </select>
            </div>

            <button 
              onClick={() => handleAnalyze(null)}
              disabled={analyzing || loading}
              className="group relative flex items-center gap-3 bg-gradient-to-b from-indigo-500 to-indigo-600 hover:from-indigo-400 hover:to-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed px-8 py-3.5 rounded-2xl font-bold transition-all shadow-[0_0_40px_-10px_rgba(99,102,241,0.4)] hover:shadow-[0_0_60px_-15px_rgba(99,102,241,0.6)] hover:-translate-y-0.5 overflow-hidden"
            >
              <div className="absolute inset-0 bg-white/20 translate-y-full group-hover:translate-y-0 transition-transform duration-300 ease-out"></div>
              {analyzing ? <RefreshCw className="animate-spin w-5 h-5 relative z-10" /> : <Cpu className="w-5 h-5 relative z-10" />}
              <span className="relative z-10">{analyzing ? '诊断模型运行中...' : '一键启动 AI 诊断'}</span>
            </button>
          </div>
        </header>

        {/* Analysis Progress Logs */}
        {(analyzing || analysisLogs.length > 0) && (
          <section className="bg-slate-900/80 backdrop-blur-2xl border border-indigo-500/30 rounded-3xl p-6 shadow-[0_0_50px_-12px_rgba(99,102,241,0.3)] animate-in fade-in zoom-in duration-500 relative overflow-hidden group/logs">
            <div className="absolute top-4 right-4 z-10">
               {analysisLogs.length > 0 && !analyzing && (
                 <button 
                   onClick={() => setAnalysisLogs([])}
                   className="p-1.5 hover:bg-slate-800 rounded-lg text-slate-500 hover:text-slate-300 transition-colors"
                   title="清除日志"
                 >
                   <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
                 </button>
               )}
            </div>
            <div className="flex items-center justify-between mb-6">
              <div className="flex items-center gap-3">
                <div className="relative">
                  {analyzing ? (
                    <>
                      <div className="w-10 h-10 border-2 border-indigo-500/20 border-t-indigo-500 rounded-full animate-spin"></div>
                      <Cpu className="w-5 h-5 text-indigo-400 absolute inset-0 m-auto" />
                    </>
                  ) : (
                    <div className="w-10 h-10 bg-indigo-500/10 rounded-full flex items-center justify-center">
                      <Cpu className="w-5 h-5 text-indigo-400" />
                    </div>
                  )}
                </div>
                <div>
                  <h3 className="text-lg font-bold text-slate-100">{analyzing ? 'AI 诊断引擎正在运行' : 'AI 诊断任务已完成'}</h3>
                  <p className="text-slate-500 text-xs">
                    {analyzing ? `正在执行第 ${currentStep}/4 步分析任务` : `分析流程已结束，共执行 ${analysisLogs.length} 项检查`}
                  </p>
                </div>
              </div>
              <div className="flex gap-1">
                {[1, 2, 3, 4].map(step => (
                  <div key={step} className={`w-8 h-1.5 rounded-full transition-colors duration-500 ${currentStep >= step ? 'bg-indigo-500' : 'bg-slate-800'}`}></div>
                ))}
              </div>
            </div>
            
            <div className="bg-slate-950/50 rounded-2xl p-4 font-mono text-sm border border-slate-800/50 max-h-[200px] overflow-y-auto space-y-2 custom-scrollbar shadow-inner">
              {analysisLogs.map((log, i) => (
                <div key={i} className="flex gap-3 text-slate-400 animate-in slide-in-from-left-2 duration-300">
                  <span className="text-indigo-500/50 shrink-0">[{new Date().toLocaleTimeString([], {hour12: false})}]</span>
                  <span className={i === analysisLogs.length - 1 && analyzing ? 'text-indigo-300 font-medium' : ''}>{log}</span>
                </div>
              ))}
              {analyzing && (
                <div className="flex gap-3 text-indigo-400/60 animate-pulse">
                  <span className="shrink-0">[..:..:..]</span>
                  <span>正在处理中...</span>
                </div>
              )}
            </div>

            {/* Smart Retry Button */}
            {hasError && !analyzing && (
              <div className="mt-6 flex flex-col sm:flex-row gap-3 animate-in slide-in-from-bottom-2 duration-500">
                {technicalResults && (
                  <button 
                    onClick={() => handleAnalyze(technicalResults)}
                    className="flex-1 flex items-center justify-center gap-2 py-3 bg-indigo-500 hover:bg-indigo-400 text-white rounded-xl font-bold transition-all shadow-lg shadow-indigo-500/20"
                  >
                    <RefreshCw className="w-4 h-4" /> 仅重试 AI 诊断 (跳过 K 线)
                  </button>
                )}
                <button 
                  onClick={() => handleAnalyze(null)}
                  className="flex-1 flex items-center justify-center gap-2 py-3 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-xl font-bold transition-all border border-slate-700"
                >
                  <RotateCcw className="w-4 h-4" /> 全量重新诊断
                </button>
              </div>
            )}
          </section>
        )}

        {loading ? (
          <div className="flex flex-col items-center justify-center py-32 space-y-4">
            <div className="w-16 h-16 border-4 border-indigo-500/20 border-t-indigo-500 rounded-full animate-spin"></div>
            <p className="text-indigo-400 font-medium tracking-wide animate-pulse">正在同步账本数据...</p>
          </div>
        ) : data?.holdings ? (
          <main className="space-y-12">
            
            {/* Top Dashboard Metrics */}
            <section className="grid grid-cols-1 md:grid-cols-12 gap-6">
              {/* Total Value */}
              <div className="md:col-span-4 bg-slate-900/50 backdrop-blur-xl border border-slate-800 rounded-3xl p-6 relative overflow-hidden group">
                <div className="absolute -right-10 -top-10 w-40 h-40 bg-blue-500/10 rounded-full blur-3xl group-hover:bg-blue-500/20 transition-colors duration-500"></div>
                <div className="flex justify-between items-start mb-6">
                  <div className="p-3 bg-blue-500/10 rounded-2xl text-blue-400"><Wallet className="w-6 h-6"/></div>
                  <span className="px-3 py-1 bg-slate-800 rounded-full text-xs text-slate-400 font-medium">CNY</span>
                </div>
                <h3 className="text-slate-400 font-medium text-sm mb-1">总资产估算</h3>
                <div className="flex items-baseline gap-2">
                  <div className="text-4xl font-black text-white tracking-tight">
                    <span className="text-2xl mr-1 opacity-50">¥</span>
                    {data.total_value?.toLocaleString(undefined, {minimumFractionDigits: 2})}
                  </div>
                  {data.day_profit !== undefined && data.day_profit !== null && (
                    <div className={`text-sm font-bold px-2 py-0.5 rounded-lg ${data.day_profit >= 0 ? 'bg-red-500/10 text-red-400' : 'bg-green-500/10 text-green-400'}`}>
                      {data.day_profit >= 0 ? '+' : ''}{data.day_profit.toLocaleString()}
                    </div>
                  )}
                </div>
              </div>

              {/* Total Profit (Split) */}
              <div className="md:col-span-4 bg-slate-900/50 backdrop-blur-xl border border-slate-800 rounded-3xl p-6 relative overflow-hidden group">
                <div className="absolute -right-10 -top-10 w-40 h-40 bg-slate-800/20 rounded-full blur-3xl"></div>
                <div className="flex justify-between items-start mb-4">
                  <div className="p-3 bg-slate-800/50 rounded-2xl text-slate-400">
                    <DollarSign className="w-6 h-6"/>
                  </div>
                  <span className="px-3 py-1 bg-slate-800 rounded-full text-xs text-slate-400 font-medium">盈亏分类</span>
                </div>
                
                <div className="grid grid-cols-2 gap-4 relative z-10">
                  <div className="space-y-1">
                    <h3 className="text-slate-500 font-medium text-xs uppercase tracking-wider">场内 ETF</h3>
                    <div className={`text-xl font-bold tracking-tight ${etfProfit >= 0 ? 'text-red-400' : 'text-green-400'}`}>
                      {etfProfit > 0 ? '+' : ''}{etfProfit.toLocaleString(undefined, {minimumFractionDigits: 1})}
                    </div>
                  </div>
                  <div className="space-y-1">
                    <h3 className="text-slate-500 font-medium text-xs uppercase tracking-wider">场外基金</h3>
                    <div className={`text-xl font-bold tracking-tight ${fundProfit >= 0 ? 'text-red-400' : 'text-green-400'}`}>
                      {fundProfit > 0 ? '+' : ''}{fundProfit.toLocaleString(undefined, {minimumFractionDigits: 1})}
                    </div>
                  </div>
                </div>
                
                <div className="mt-4 pt-4 border-t border-slate-800/50 flex justify-between items-end">
                  <span className="text-slate-500 text-xs">合计盈亏</span>
                  <div className={`text-lg font-black ${data.total_profit >= 0 ? 'text-red-400' : 'text-green-400'}`}>
                    ¥ {data.total_profit?.toLocaleString(undefined, {minimumFractionDigits: 2})}
                  </div>
                </div>
              </div>

              {/* Asset Allocation Chart */}
              <div className="md:col-span-4 bg-slate-900/50 backdrop-blur-xl border border-slate-800 rounded-3xl p-6 flex flex-col relative overflow-hidden">
                <div className="flex items-center gap-2 mb-2 z-10">
                  <PieChartIcon className="w-5 h-5 text-slate-400"/>
                  <h3 className="text-slate-400 font-medium text-sm">资产分布图</h3>
                </div>
                <div className="flex-1 min-h-[120px] -mx-4 z-10">
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie data={data.holdings} dataKey="market_value" nameKey="name" cx="50%" cy="50%" innerRadius={40} outerRadius={60} stroke="none" paddingAngle={2}>
                        {data.holdings.map((_: any, index: number) => (
                          <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                        ))}
                      </Pie>
                      <Tooltip 
                        contentStyle={{backgroundColor: 'rgba(15, 23, 42, 0.9)', border: '1px solid rgba(51, 65, 85, 0.5)', borderRadius: '12px', color: '#fff', backdropFilter: 'blur(8px)', boxShadow: '0 10px 25px -5px rgba(0, 0, 0, 0.5)'}} 
                        itemStyle={{color: '#e2e8f0'}}
                        formatter={(value: any, name: string) => [`¥${Number(value).toLocaleString()}`, name]}
                      />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
              </div>
            </section>

            {/* AI Analysis Result Panel (only shows when aiData exists) */}
            {aiData && (
              <section className="relative group">
                <div className="absolute -inset-1 bg-gradient-to-r from-indigo-500 via-purple-500 to-pink-500 rounded-[2rem] blur opacity-25 group-hover:opacity-40 transition duration-1000 group-hover:duration-200"></div>
                <div className="relative bg-slate-900 border border-slate-700/50 rounded-[2rem] p-6 md:p-10 shadow-2xl">
                  
                  {/* AI Top Summary */}
                  <div className="flex flex-col lg:flex-row justify-between items-start gap-8 mb-10 border-b border-slate-800/80 pb-10">
                    <div className="flex-1 space-y-4">
                      <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-indigo-500/10 border border-indigo-500/30 text-indigo-300 text-sm font-semibold">
                        <Cpu className="w-4 h-4"/> 深度诊断完成
                      </div>
                      <h2 className="text-2xl md:text-3xl font-bold text-slate-100 leading-snug">
                        {aiData.summary?.brief || "基于多因子模型的资产健康检查完毕。"}
                      </h2>
                      
                      {aiData.risk_tags?.length > 0 && (
                        <div className="flex flex-wrap gap-2 pt-2">
                          {aiData.risk_tags.map((tag: string, i: number) => (
                            <span key={i} className="px-3 py-1.5 bg-red-500/10 border border-red-500/20 text-red-400 rounded-lg text-sm font-medium flex items-center gap-1.5 shadow-inner">
                              <ShieldAlert className="w-4 h-4"/> {tag}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                    
                    <div className="flex flex-col items-center justify-center bg-slate-950/50 rounded-3xl p-8 min-w-[200px] border border-slate-800/80 shadow-inner">
                      <span className="text-slate-400 text-sm font-medium mb-2 tracking-wide uppercase">整体健康分</span>
                      <span className="text-6xl font-black text-transparent bg-clip-text bg-gradient-to-b from-emerald-300 to-emerald-600 drop-shadow-sm">
                        {aiData.summary?.health_score}
                      </span>
                      <span className="mt-3 px-4 py-1 bg-emerald-500/10 text-emerald-400 rounded-full text-sm font-bold">{aiData.summary?.status}</span>
                    </div>
                  </div>

                  {/* AI Actionable Cards */}
                  {aiData.action_items?.length > 0 && (
                    <div className="mb-10">
                      <h3 className="text-lg font-bold text-slate-300 mb-4 flex items-center gap-2">
                        <Activity className="w-5 h-5 text-indigo-400"/> 具体标的建议
                      </h3>
                      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                        {aiData.action_items.map((action: any, i: number) => (
                          <div key={i} className="bg-slate-800/40 hover:bg-slate-800/80 transition-colors border border-slate-700/50 rounded-2xl p-5 flex flex-col gap-3 group">
                            <div className="flex justify-between items-start">
                              <span className="font-bold text-lg text-slate-100 group-hover:text-indigo-300 transition-colors">{action.target}</span>
                              <span className={`text-xs px-3 py-1 rounded-full font-bold uppercase tracking-wider ${
                                action.type === 'reduce' ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' : 
                                action.type === 'buy' ? 'bg-red-500/10 text-red-400 border border-red-500/20' : 'bg-amber-500/10 text-amber-400 border border-amber-500/20'
                              }`}>
                                {action.type === 'reduce' ? '减仓' : action.type === 'buy' ? '加仓' : '观望'}
                              </span>
                            </div>
                            <p className="text-sm text-slate-400 leading-relaxed">{action.reason}</p>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* AI Markdown Analysis Details */}
                  {aiData.detailed_analysis && (
                    <div className="pt-8 border-t border-slate-800/80">
                      <h3 className="text-lg font-bold text-slate-300 mb-6 flex items-center gap-2">
                        <LineChart className="w-5 h-5 text-indigo-400"/> 深度研报逻辑
                      </h3>
                      <div className="prose prose-invert prose-indigo max-w-none prose-p:leading-relaxed prose-headings:font-bold prose-a:text-indigo-400 prose-li:text-slate-300 bg-slate-950/30 p-6 md:p-8 rounded-3xl border border-slate-800/60 shadow-inner">
                        <ReactMarkdown>{aiData.detailed_analysis}</ReactMarkdown>
                      </div>
                    </div>
                  )}
                </div>
              </section>
            )}

            {/* Market Trend Analysis (K-Line Chart) */}
            <section className="bg-slate-900/50 backdrop-blur-xl border border-slate-800 rounded-3xl p-6 md:p-8">
              <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4 mb-8">
                <div className="flex items-center gap-3">
                  <div className="p-2 bg-indigo-500/10 rounded-lg text-indigo-400"><Activity className="w-5 h-5"/></div>
                  <div>
                    <h2 className="text-xl font-bold text-slate-100">市场趋势监控</h2>
                    <p className="text-slate-500 text-sm">正在查看: {data?.holdings?.find((h: any) => h.code === selectedSymbol)?.name || selectedSymbol} ({selectedSymbol})</p>
                  </div>
                </div>
                <div className="flex gap-2">
                  <span className="px-3 py-1 bg-slate-800 rounded-full text-xs text-slate-400 border border-slate-700">最近 60 交易日</span>
                  <span className="px-3 py-1 bg-indigo-500/10 text-indigo-400 rounded-full text-xs font-medium border border-indigo-500/20">数据源: yinglian.site</span>
                </div>
              </div>

              <div className="h-[300px] w-100% -ml-4">
                {klineLoading ? (
                  <div className="h-full w-full flex items-center justify-center">
                    <RefreshCw className="animate-spin text-slate-600 w-8 h-8" />
                  </div>
                ) : klines.length > 0 ? (
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={klines}>
                      <defs>
                        <linearGradient id="colorClose" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#6366f1" stopOpacity={0.3}/>
                          <stop offset="95%" stopColor="#6366f1" stopOpacity={0}/>
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
                      <XAxis 
                        dataKey="timestamp" 
                        tick={{fill: '#64748b', fontSize: 10}} 
                        axisLine={false}
                        tickLine={false}
                        minTickGap={30}
                        tickFormatter={(val) => val.split(' ')[0]}
                      />
                      <YAxis 
                        domain={['auto', 'auto']} 
                        tick={{fill: '#64748b', fontSize: 10}} 
                        axisLine={false}
                        tickLine={false}
                        tickFormatter={(val) => val.toFixed(3)}
                      />
                      <Tooltip 
                        contentStyle={{backgroundColor: '#0f172a', border: '1px solid #1e293b', borderRadius: '12px', fontSize: '12px'}}
                        itemStyle={{color: '#818cf8'}}
                        labelStyle={{color: '#94a3b8', marginBottom: '4px'}}
                        labelFormatter={(label) => `日期: ${label}`}
                      />
                      <Area 
                        type="monotone" 
                        dataKey="close" 
                        stroke="#6366f1" 
                        strokeWidth={2}
                        fillOpacity={1} 
                        fill="url(#colorClose)" 
                        animationDuration={1000}
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                ) : (
                  <div className="h-full w-full flex flex-col items-center justify-center text-slate-600">
                    <LineChart className="w-12 h-12 mb-2 opacity-20" />
                    <p>暂无趋势数据</p>
                  </div>
                )}
              </div>
            </section>

            {/* Holdings Sections */}
            <div className="space-y-10">
              
              {/* Section 1: ETFs */}
              {etfHoldings.length > 0 && (
                <section>
                  <div className="flex items-center justify-between mb-6">
                    <div className="flex items-center gap-3">
                      <div className="p-2 bg-indigo-500/10 rounded-lg text-indigo-400"><LineChart className="w-5 h-5"/></div>
                      <h2 className="text-2xl font-bold text-slate-100">场内持仓 <span className="text-slate-500 font-normal text-lg ml-2">(ETF / 股票)</span></h2>
                    </div>
                    <div className="flex items-center gap-2 text-sm bg-slate-900 border border-slate-800 rounded-lg p-1">
                      <select 
                        className="bg-transparent text-slate-300 outline-none px-2 py-1 cursor-pointer appearance-none"
                        value={etfSortBy}
                        onChange={(e) => setEtfSortBy(e.target.value as any)}
                      >
                        <option value="market_value" className="bg-slate-900">按市值</option>
                        <option value="profit_pct" className="bg-slate-900">按涨跌幅</option>
                        <option value="hold_profit" className="bg-slate-900">按持仓盈亏</option>
                      </select>
                      <div className="w-px h-4 bg-slate-800"></div>
                      <button 
                        className="p-1.5 hover:bg-slate-800 rounded-md text-slate-400 transition-colors"
                        onClick={() => setEtfSortOrder(o => o === 'desc' ? 'asc' : 'desc')}
                        title={etfSortOrder === 'desc' ? '降序排列' : '升序排列'}
                      >
                        <ArrowDownUp className="w-4 h-4"/>
                      </button>
                    </div>
                  </div>
                  <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-5">
                    {etfHoldings.map((h: any, i: number) => (
                      <HoldingCard 
                        key={`etf-${i}`} 
                        holding={h} 
                        isActive={selectedSymbol === h.code}
                        onSelect={() => setSelectedSymbol(h.code)}
                      />
                    ))}
                  </div>
                </section>
              )}

              {/* Section 2: Funds */}
              {fundHoldings.length > 0 && (
                <section>
                  <div className="flex items-center justify-between mb-6">
                    <div className="flex items-center gap-3">
                      <div className="p-2 bg-amber-500/10 rounded-lg text-amber-400"><Landmark className="w-5 h-5"/></div>
                      <h2 className="text-2xl font-bold text-slate-100">场外持仓 <span className="text-slate-500 font-normal text-lg ml-2">(公募基金)</span></h2>
                    </div>
                    <div className="flex items-center gap-2 text-sm bg-slate-900 border border-slate-800 rounded-lg p-1">
                      <select 
                        className="bg-transparent text-slate-300 outline-none px-2 py-1 cursor-pointer appearance-none"
                        value={fundSortBy}
                        onChange={(e) => setFundSortBy(e.target.value as any)}
                      >
                        <option value="market_value" className="bg-slate-900">按市值</option>
                        <option value="profit_pct" className="bg-slate-900">按涨跌幅</option>
                        <option value="hold_profit" className="bg-slate-900">按持仓盈亏</option>
                      </select>
                      <div className="w-px h-4 bg-slate-800"></div>
                      <button 
                        className="p-1.5 hover:bg-slate-800 rounded-md text-slate-400 transition-colors"
                        onClick={() => setFundSortOrder(o => o === 'desc' ? 'asc' : 'desc')}
                        title={fundSortOrder === 'desc' ? '降序排列' : '升序排列'}
                      >
                        <ArrowDownUp className="w-4 h-4"/>
                      </button>
                    </div>
                  </div>
                  <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-5">
                    {fundHoldings.map((h: any, i: number) => (
                      <HoldingCard 
                        key={`fund-${i}`} 
                        holding={h} 
                        isFund={true} 
                        isActive={selectedSymbol === h.code}
                        onSelect={() => setSelectedSymbol(h.code)}
                      />
                    ))}
                  </div>
                </section>
              )}

            </div>
          </main>
        ) : (
          <div className="bg-slate-900/50 backdrop-blur-xl border border-slate-800 rounded-3xl p-16 text-center text-slate-400 flex flex-col items-center shadow-2xl">
            <div className="w-24 h-24 bg-slate-800 rounded-full flex items-center justify-center mb-6 border border-slate-700">
              <ShieldAlert className="w-10 h-10 text-slate-500" />
            </div>
            <h2 className="text-2xl font-bold text-slate-200 mb-2">未能读取到资产数据</h2>
            <p className="max-w-md mx-auto text-slate-500">
              请确保在 config.toml 中配置了正确的 <code>[ledger] mode="tzzb_api"</code>，并且后端的模拟登录与数据抓取服务运行正常。
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

// Sub-component for individual asset cards
function HoldingCard({ holding, isFund = false, isActive = false, onSelect }: { holding: any, isFund?: boolean, isActive?: boolean, onSelect: () => void }) {
  const isProfit = holding.profit_pct >= 0;
  
  return (
    <div 
      onClick={onSelect}
      className={`bg-slate-900/40 hover:bg-slate-800/60 transition-all duration-300 border rounded-2xl p-5 flex flex-col justify-between group cursor-pointer ${
        isActive ? 'border-indigo-500/50 shadow-[0_0_20px_-5px_rgba(99,102,241,0.2)] bg-slate-800/40' : 'border-slate-800 hover:border-slate-700'
      }`}
    >
      <div>
        <div className="flex justify-between items-start mb-4">
          <div>
            <h4 className={`font-bold text-lg transition-colors ${isActive ? 'text-indigo-300' : 'text-slate-100 group-hover:text-indigo-300'}`}>{holding.name}</h4>
            <div className="flex items-center gap-2 mt-1 text-slate-500 text-sm font-mono">
              <span>{holding.code}</span>
              <span className="w-1 h-1 bg-slate-600 rounded-full"></span>
              <span>占比 {(holding.weight || 0).toFixed(1)}%</span>
            </div>
          </div>
          <div className={`px-2.5 py-1 rounded-lg text-sm font-bold flex flex-col items-end gap-0.5 ${isProfit ? 'bg-red-500/10 text-red-400' : 'bg-green-500/10 text-green-400'}`}>
            <div className="flex items-center gap-1">
              <span className="text-[10px] uppercase opacity-70">总盈亏</span>
              {isProfit ? <TrendingUp className="w-3.5 h-3.5"/> : <TrendingDown className="w-3.5 h-3.5"/>}
              <span>{holding.hold_profit > 0 ? '+' : ''}{holding.hold_profit?.toFixed(2)}</span>
            </div>
            <span className="text-xs opacity-80">{holding.profit_pct > 0 ? '+' : ''}{holding.profit_pct?.toFixed(2)}%</span>
          </div>
        </div>
        
        <div className="grid grid-cols-3 gap-2 mb-4">
          <div>
            <p className="text-slate-500 text-[11px] mb-1">当前市值</p>
            <p className="font-semibold text-slate-200 text-sm">¥ {holding.market_value?.toLocaleString(undefined, {minimumFractionDigits: 2})}</p>
          </div>
          <div>
            <p className="text-slate-500 text-[11px] mb-1">今日涨跌</p>
            <p className={`font-semibold text-sm ${holding.day_profit >= 0 ? 'text-red-400' : 'text-green-400'}`}>
              {holding.day_profit > 0 ? '+' : ''}{holding.day_profit?.toLocaleString(undefined, {minimumFractionDigits: 2})}
            </p>
          </div>
          <div>
            <p className="text-slate-500 text-[11px] mb-1">成本价</p>
            <p className="font-semibold text-slate-200 text-sm">¥ {holding.cost_price?.toLocaleString(undefined, {minimumFractionDigits: 3})}</p>
          </div>
        </div>
      </div>
      
      <div className="pt-4 border-t border-slate-800">
        <div className="flex items-start gap-2">
          {isFund ? (
            <Landmark className="w-4 h-4 mt-0.5 text-slate-500 shrink-0"/>
          ) : (
            <Activity className="w-4 h-4 mt-0.5 text-indigo-400 shrink-0"/>
          )}
          <div>
            <span className={`text-xs font-medium px-2 py-0.5 rounded mr-2 ${isFund ? 'bg-slate-800 text-slate-400' : 'bg-indigo-500/10 text-indigo-300'}`}>
              {holding.action || '数据不足'}
            </span>
            <p className="text-xs text-slate-400 line-clamp-2 mt-1 leading-relaxed" title={holding.reason}>
              {holding.reason || '暂无系统分析建议'}
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
