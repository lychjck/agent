import { useState, useEffect } from 'react';
import type { ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import { RefreshCw, TrendingUp, TrendingDown, Activity, DollarSign, Wallet, ShieldAlert, Cpu, Landmark, LineChart, PieChart as PieChartIcon, RotateCcw, Box, ArrowDownUp, Database, Layers, AlertTriangle, CheckCircle2, ChevronRight, FileText, Wrench, MessageSquare, Braces } from 'lucide-react';
import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip, AreaChart, Area, XAxis, YAxis, CartesianGrid } from 'recharts';

const COLORS = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899', '#06b6d4'];

type AgentTraceEntry = {
  id: string;
  time: string;
  step: string;
  title: string;
  subtitle: string;
  tone: 'indigo' | 'cyan' | 'emerald' | 'amber' | 'red' | 'slate';
  payload: AgentPayload;
  turn?: number;
};

type AgentPayload = Record<string, unknown>;

const formatMoney = (value: any) => `¥ ${Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const formatPct = (value: any) => `${Number(value || 0).toFixed(2)}%`;
const labelOf = (key: string) => ({
  // 资产大类
  broad_index: '宽基指数',
  sector_equity: '行业权益',
  theme_equity: '主题权益',
  thematic_equity: '主题权益',
  active_equity: '主动权益',
  mixed_allocation: '混合型',
  bond: '个债',
  bond_fund: '债券基金',
  overseas: '海外资产',
  qdii: 'QDII 基金',
  commodity: '商品基金',
  cash: '现金等价物',
  money_market: '货币基金',
  active_fund: '主动管理',
  fof: 'FOF 基金',

  // 行业
  financials: '金融',
  semiconductor: '半导体',
  technology: '科技/信息技术',
  healthcare: '医疗/医药',
  consumer: '消费',
  energy: '能源',
  materials: '材料',
  industrials: '工业',
  military: '军工/航天',
  agriculture: '农业',
  real_estate: '房地产',
  infrastructure: '基建',
  media: '传媒/互联网',
  dividend: '红利',
  multi_sector: '多行业',

  // 策略
  passive_index: '被动指数',
  active_management: '主动管理',
  enhanced_index: '指数增强',
  bond_income: '债券收益',
  commodity_tracking: '商品跟踪',

  // 区域/其他
  china_a: '中国 A 股',
  etf: '场内',
  fund: '场外',
  unknown: '未知',
  "": '不适用',

  // 来源
  config: '手动配置',
  cache: '本地缓存',
  search_llm: 'AI 搜索分类',
  search_rule_fallback: '规则推断',
  local_heuristic: '本地启发式',
}[key] || key);

const aiActionTone = (type: string) => {
  if (type === 'reduce') return 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20';
  if (type === 'buy') return 'bg-red-500/10 text-red-400 border border-red-500/20';
  if (type === 'rebalance') return 'bg-blue-500/10 text-blue-400 border border-blue-500/20';
  if (type === 'classify_required') return 'bg-slate-500/10 text-slate-300 border border-slate-500/20';
  if (type === 'hold') return 'bg-indigo-500/10 text-indigo-300 border border-indigo-500/20';
  return 'bg-amber-500/10 text-amber-400 border border-amber-500/20';
};

const aiActionLabel = (type: string, prefix = '') => ({
  reduce: `${prefix}减仓/暂停加仓`,
  buy: `${prefix}分批加仓`,
  rebalance: `${prefix}再平衡`,
  classify_required: `${prefix}需确认分类`,
  hold: `${prefix}继续持有`,
  watch: `${prefix}观察`,
}[type] || `${prefix}观察`);

const pctRows = (bucket: Record<string, number> | undefined) => Object.entries(bucket || {})
  .map(([key, pct]) => ({ key, label: labelOf(key), pct: Number(pct || 0) }))
  .filter((item) => item.pct > 0)
  .sort((a, b) => b.pct - a.pct);

const agentStepNumber = (step: string | number | undefined) => {
  if (typeof step === 'number') return step;
  if (step === 'agent_start' || step === 'llm_turn' || step === 'research_plan' || step === 'llm_decision') return 1;
  if (step === 'tool_call') return 2;
  if (step === 'tool_observation' || step === 'observation_reflection') return 3;
  if (step === 'final_report' || step === 'save_snapshot' || step === 'done') return 4;
  return 3;
};

const isRecord = (value: unknown): value is AgentPayload => (
  typeof value === 'object' && value !== null && !Array.isArray(value)
);

const textValue = (value: unknown) => {
  if (value === undefined || value === null) return '';
  return String(value);
};

const formatJson = (value: unknown) => {
  if (value === undefined || value === null || value === '') return '';
  if (typeof value === 'string') {
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value;
    }
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
};

const compactSnapshotForTrace = (snapshot: unknown) => {
  if (!isRecord(snapshot)) return snapshot;
  const agentReport = isRecord(snapshot.agent_report) ? snapshot.agent_report : {};
  return {
    generated_at: snapshot.generated_at,
    model: snapshot.model,
    portfolio: snapshot.portfolio,
    risk_flags: snapshot.risk_flags,
    candidate_actions_count: Array.isArray(snapshot.candidate_actions) ? snapshot.candidate_actions.length : 0,
    action_items: agentReport.action_items,
    summary: agentReport.summary,
  };
};

const traceDisplayPayload = (payload: unknown) => {
  if (!isRecord(payload)) return payload;
  const clean = { ...payload };
  if (clean.snapshot) clean.snapshot = compactSnapshotForTrace(clean.snapshot);
  return clean;
};

const traceTitle = (payload: AgentPayload) => {
  const step = textValue(payload.step);
  const turn = payload.turn ? `第 ${payload.turn} 轮` : '';
  if (step === 'agent_start') return '启动 Agent';
  if (step === 'llm_turn') return `${turn} LLM 请求`;
  if (step === 'research_plan') return `${turn} 研究计划`;
  if (step === 'observation_reflection') return `${turn} 工具结果反思`;
  if (step === 'llm_decision') return payload.decision_type === 'final_report' ? `${turn} LLM 决定生成报告` : `${turn} LLM 决定调用工具`;
  if (step === 'tool_call') return `调用工具 ${textValue(payload.tool)}`.trim();
  if (step === 'tool_observation') return `工具返回 ${textValue(payload.tool)}`.trim();
  if (step === 'final_report') return '生成最终报告';
  if (step === 'save_snapshot') return '保存快照';
  if (step === 'done') return '流程完成';
  if (step === 'error') return '执行出错';
  return textValue(payload.status) || step || 'Agent 事件';
};

const traceSubtitle = (payload: AgentPayload) => {
  if (payload.reasoning_summary) return textValue(payload.reasoning_summary);
  if (payload.missing_capabilities) return `缺失能力：${Array.isArray(payload.missing_capabilities) ? payload.missing_capabilities.length : 0} 项`;
  if (payload.summary) return textValue(payload.summary);
  if (payload.message) return textValue(payload.message);
  if (payload.status) return textValue(payload.status);
  if (payload.error) return textValue(payload.error);
  return '';
};

const traceTone = (payload: AgentPayload): AgentTraceEntry['tone'] => {
  if (payload.step === 'error' || payload.ok === false) return 'red';
  if (payload.step === 'tool_call') return 'cyan';
  if (payload.step === 'tool_observation') return 'emerald';
  if (payload.step === 'research_plan') return 'amber';
  if (payload.step === 'observation_reflection') return 'amber';
  if (payload.step === 'final_report' || payload.step === 'done') return 'indigo';
  if (payload.step === 'save_snapshot') return 'amber';
  return 'slate';
};

const TRACE_TONE_CLASS: Record<AgentTraceEntry['tone'], string> = {
  indigo: 'border-indigo-500/30 bg-indigo-500/10 text-indigo-300',
  cyan: 'border-cyan-500/30 bg-cyan-500/10 text-cyan-300',
  emerald: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300',
  amber: 'border-amber-500/30 bg-amber-500/10 text-amber-300',
  red: 'border-red-500/30 bg-red-500/10 text-red-300',
  slate: 'border-slate-700 bg-slate-800/60 text-slate-300',
};

const buildAgentTraceEntry = (payload: AgentPayload): AgentTraceEntry => ({
  id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
  time: new Date().toLocaleTimeString([], { hour12: false }),
  step: textValue(payload.step) || 'event',
  title: traceTitle(payload),
  subtitle: traceSubtitle(payload),
  tone: traceTone(payload),
  payload,
  turn: typeof payload.turn === 'number' ? payload.turn : undefined,
});

export default function App() {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [profile, setProfile] = useState<any>(null);
  const [profileLoading, setProfileLoading] = useState(true);
  const [profileRefreshing, setProfileRefreshing] = useState(false);
  const [profileError, setProfileError] = useState<string | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [aiData, setAiData] = useState<any>(null);
  const [analysisTrace, setAnalysisTrace] = useState<AgentTraceEntry[]>([]);
  const [selectedTraceId, setSelectedTraceId] = useState<string | null>(null);
  const [currentStep, setCurrentStep] = useState(0);
  const [selectedModel, setSelectedModel] = useState('deepseek-v4-pro');
  const [technicalResults, setTechnicalResults] = useState<any[] | null>(null);
  
  const MODELS = [
    { id: 'deepseek-v4-pro', name: 'DeepSeek V4 Pro', provider: 'EasyRouter' },
    { id: 'google/gemma-4-26b-a4b', name: 'Gemma 4 26B A4B (本地)', provider: 'Local' },
    { id: 'inclusionAI/Ling-2.6-1T', name: 'Ling-2.6-1T (推荐)', provider: 'ModelScope' },
    { id: 'ZhipuAI/GLM-5.1', name: 'GLM-5.1', provider: 'ModelScope' },
    { id: 'moonshotai/Kimi-K2.5', name: 'Kimi-K2.5', provider: 'ModelScope' },
    { id: 'deepseek-ai/DeepSeek-V3', name: 'DeepSeek V3', provider: 'ModelScope' },
    { id: 'deepseek-ai/DeepSeek-V4-Pro', name: 'DeepSeek V4 Pro', provider: 'ModelScope' },
  ];
  const [hasError, setHasError] = useState(false);
  const [activeTab, setActiveTab] = useState<'overview' | 'profile' | 'analysis'>('overview');
  
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
    fetchProfile(false);
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

  const fetchProfile = async (refreshClassification: boolean) => {
    if (refreshClassification) {
      setProfileRefreshing(true);
    } else {
      setProfileLoading(true);
    }
    setProfileError(null);
    try {
      const res = await fetch(`/api/profile?refresh_classification=${refreshClassification ? 'true' : 'false'}&_t=${Date.now()}`);
      if (!res.ok) {
        const payload = await res.json().catch(() => ({}));
        throw new Error(payload.detail || `HTTP ${res.status}`);
      }
      const json = await res.json();
      setProfile(json);
    } catch (err: any) {
      console.error(err);
      setProfileError(err.message || '组合画像生成失败');
    } finally {
      setProfileLoading(false);
      setProfileRefreshing(false);
    }
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
    setActiveTab('analysis');
    setAnalyzing(true);
    setHasError(false);
    if (!resumeData) {
      setAiData(null);
      setAnalysisTrace([]);
      setSelectedTraceId(null);
      setCurrentStep(0);
      setTechnicalResults(null);
    }
    
    try {
      const response = await fetch('/api/agent/run/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
          mode: 'tool_agent',
          goal: '分析当前持仓，给出组合风险、每个 ETF 的建议和需要确认的问题',
          cached_results: resumeData,
          model: selectedModel
        })
      });
      const reader = response.body?.getReader();
      const decoder = new TextDecoder();

      if (!reader) return;

      const handleAgentPayload = (payload: AgentPayload) => {
        const entry = buildAgentTraceEntry(payload);
        setAnalysisTrace(prev => [...prev, entry]);
        setSelectedTraceId(entry.id);

        if (payload.step) {
          setCurrentStep(agentStepNumber(textValue(payload.step)));
        }
        if (payload.technical_results) {
          setTechnicalResults(payload.technical_results as unknown[]);
        }
        const snapshot = isRecord(payload.snapshot) ? payload.snapshot : null;
        if (snapshot?.technical_results) {
          setTechnicalResults(snapshot.technical_results as unknown[]);
        }
        if (snapshot?.agent_report) {
          setAiData(snapshot.agent_report);
          setHasError(false);
        }
        if (payload.result) {
          setAiData(payload.result);
          setHasError(false);
        }
        if (payload.error) {
          setHasError(true);
        }
      };

      const parseSseBlock = (block: string) => {
        const data = block
          .split('\n')
          .filter(line => line.startsWith('data:'))
          .map(line => line.replace(/^data:\s?/, ''))
          .join('\n')
          .trim();
        if (!data) return;
        try {
          const parsed = JSON.parse(data);
          if (isRecord(parsed)) {
            handleAgentPayload(parsed);
          }
        } catch (e) {
          console.error('Failed to parse SSE block:', block, e);
        }
      };

      let buffer = '';
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const blocks = buffer.split('\n\n');
        buffer = blocks.pop() || '';
        blocks.forEach(parseSseBlock);
      }
      buffer += decoder.decode();
      if (buffer.trim()) parseSseBlock(buffer);
    } catch (err: any) {
      console.error(err);
      const entry = buildAgentTraceEntry({ step: 'error', error: err.message, status: '分析过程中发生意外错误' });
      setAnalysisTrace(prev => [...prev, entry]);
      setSelectedTraceId(entry.id);
      setHasError(true);
    }
    setAnalyzing(false);
  };

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
  const selectedTrace = analysisTrace.find(item => item.id === selectedTraceId) || analysisTrace[analysisTrace.length - 1] || null;

  return (
    <div className="min-h-screen bg-[#0B1120] p-4 sm:p-6 md:p-8 xl:p-12 text-slate-100 font-sans selection:bg-amber-500/30">
      <div className="max-w-7xl mx-auto space-y-10">
        
        {/* Header Area */}
        <header className="flex flex-col md:flex-row justify-between items-start md:items-center gap-6 pb-6 border-b border-white/5">
          <div className="space-y-2">
            <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-amber-500/10 border border-amber-500/20 text-amber-400 text-xs font-semibold uppercase tracking-wider mb-2">
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75"></span>
                <span className="relative inline-flex rounded-full h-2 w-2 bg-amber-500"></span>
              </span>
              System Active
            </div>
            <h1 className="text-4xl md:text-5xl font-extrabold tracking-tight bg-gradient-to-br from-white via-amber-200 to-amber-500 bg-clip-text text-transparent flex items-center gap-3">
              驾驶舱
            </h1>
            <p className="text-slate-400 text-sm md:text-base max-w-xl leading-relaxed">
              实时监控您的场内 ETF 与场外基金资产，结合大模型多维度深度诊断，为您提供量化与基本面结合的操作建议。
            </p>
          </div>
          
          <div className="flex flex-col gap-4">
            {/* Model Selector UI */}
            <div className="flex items-center gap-3 bg-slate-900/40 backdrop-blur-md border border-white/5 shadow-[inset_0_1px_0_rgba(255,255,255,0.05)] p-2 pl-4 rounded-[1.25rem]">
              <Box className="w-4 h-4 text-violet-400" />
              <select 
                value={selectedModel}
                onChange={(e) => setSelectedModel(e.target.value)}
                disabled={analyzing}
                className="bg-transparent text-sm font-semibold text-slate-200 outline-none cursor-pointer pr-4"
              >
                {MODELS.map(m => (
                  <option key={m.id} value={m.id} className="bg-[#0B1120] text-slate-200">
                    {m.name}
                  </option>
                ))}
              </select>
            </div>

            <button 
              onClick={() => handleAnalyze(null)}
              disabled={analyzing || loading}
              className="group relative flex items-center justify-center gap-3 bg-gradient-to-b from-violet-500 to-violet-600 hover:from-violet-400 hover:to-violet-500 disabled:opacity-50 disabled:cursor-not-allowed px-8 py-3.5 rounded-[1.25rem] font-bold transition-all shadow-[0_0_40px_-10px_rgba(139,92,246,0.4)] hover:shadow-[0_0_60px_-15px_rgba(139,92,246,0.6)] hover:-translate-y-0.5 overflow-hidden border border-violet-400/30"
            >
              <div className="absolute inset-0 bg-white/20 translate-y-full group-hover:translate-y-0 transition-transform duration-300 ease-out"></div>
              {analyzing ? <RefreshCw className="animate-spin w-5 h-5 relative z-10" /> : <Cpu className="w-5 h-5 relative z-10" />}
              <span className="relative z-10">{analyzing ? '诊断模型运行中...' : '一键启动 AI 诊断'}</span>
            </button>
          </div>
        </header>

        {/* Navigation Tabs */}
        {!loading && data?.holdings && (
          <div className="flex space-x-2 bg-slate-900/40 backdrop-blur-md p-1.5 rounded-[1.25rem] border border-white/5 w-fit shadow-[inset_0_1px_0_rgba(255,255,255,0.05)]">
            <button 
              onClick={() => setActiveTab('overview')} 
              className={`px-6 py-2.5 rounded-xl text-sm font-bold transition-all ${activeTab === 'overview' ? 'bg-amber-500 shadow-lg shadow-amber-500/20 text-slate-900' : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'}`}
            >
              资产总览
            </button>
            <button 
              onClick={() => setActiveTab('profile')} 
              className={`px-6 py-2.5 rounded-xl text-sm font-bold transition-all ${activeTab === 'profile' ? 'bg-cyan-500 shadow-lg shadow-cyan-500/20 text-slate-900' : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'}`}
            >
              组合画像
            </button>
            <button 
              onClick={() => setActiveTab('analysis')} 
              className={`px-6 py-2.5 rounded-xl text-sm font-bold transition-all flex items-center gap-2 ${activeTab === 'analysis' ? 'bg-violet-500 shadow-lg shadow-violet-500/20 text-white' : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'}`}
            >
              AI 诊断
              {(aiData || analyzing) && <span className={`w-2 h-2 rounded-full ${analyzing ? 'bg-amber-400 animate-pulse' : 'bg-purple-300'}`}></span>}
            </button>
          </div>
        )}

        {/* Analysis Progress Logs */}
        {activeTab === 'analysis' && (analyzing || analysisTrace.length > 0) && (
          <section className="glass-card p-6 shadow-[0_0_50px_-12px_rgba(139,92,246,0.15)] animate-in fade-in zoom-in duration-500 relative overflow-hidden group/logs border-violet-500/20">
            <div className="absolute top-4 right-4 z-10">
               {analysisTrace.length > 0 && !analyzing && (
                 <button 
                   onClick={() => {
                     setAnalysisTrace([]);
                     setSelectedTraceId(null);
                   }}
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
                    {analyzing ? `正在执行第 ${currentStep}/4 步分析任务` : `分析流程已结束，共记录 ${analysisTrace.length} 个事件`}
                  </p>
                </div>
              </div>
              <div className="flex gap-1">
                {[1, 2, 3, 4].map(step => (
                  <div key={step} className={`w-8 h-1.5 rounded-full transition-colors duration-500 ${currentStep >= step ? 'bg-indigo-500' : 'bg-slate-800'}`}></div>
                ))}
              </div>
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-12 gap-4">
              <div className="xl:col-span-5 rounded-2xl border border-slate-800/70 bg-slate-950/40 overflow-hidden">
                <div className="px-4 py-3 border-b border-slate-800/70 flex items-center justify-between">
                  <span className="text-sm font-bold text-slate-200">执行轨迹</span>
                  <span className="text-xs text-slate-500">{analysisTrace.length} events</span>
                </div>
                <div className="max-h-[360px] overflow-y-auto custom-scrollbar p-2 space-y-2">
                  {analysisTrace.map((item) => {
                    const selected = selectedTrace?.id === item.id;
                    return (
                      <button
                        key={item.id}
                        onClick={() => setSelectedTraceId(item.id)}
                        className={`w-full text-left rounded-xl border p-3 transition-all ${
                          selected
                            ? `${TRACE_TONE_CLASS[item.tone]} shadow-lg shadow-black/10`
                            : 'border-slate-800 bg-slate-900/50 hover:bg-slate-800/70 text-slate-300'
                        }`}
                      >
                        <div className="flex items-start gap-3">
                          <div className={`mt-0.5 rounded-lg border p-1.5 ${selected ? 'border-current/30 bg-black/10' : TRACE_TONE_CLASS[item.tone]}`}>
                            <AgentTraceIcon entry={item} />
                          </div>
                          <div className="min-w-0 flex-1">
                            <div className="flex items-center justify-between gap-2">
                              <span className="text-sm font-bold truncate">{item.title}</span>
                              <span className="text-[11px] font-mono text-slate-500 shrink-0">{item.time}</span>
                            </div>
                            {item.subtitle && (
                              <p className="mt-1 text-xs leading-relaxed text-slate-400 line-clamp-2">{item.subtitle}</p>
                            )}
                          </div>
                          <ChevronRight className={`w-4 h-4 mt-1 shrink-0 transition-transform ${selected ? 'rotate-90 text-current' : 'text-slate-600'}`} />
                        </div>
                      </button>
                    );
                  })}
                  {analyzing && (
                    <div className="rounded-xl border border-indigo-500/20 bg-indigo-500/5 p-3 text-sm text-indigo-300 flex items-center gap-2 animate-pulse">
                      <RefreshCw className="w-4 h-4 animate-spin" />
                      等待下一条 Agent 事件...
                    </div>
                  )}
                </div>
              </div>

              <div className="xl:col-span-7 rounded-2xl border border-slate-800/70 bg-slate-950/40 overflow-hidden">
                <AgentTraceDetail entry={selectedTrace} />
              </div>
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
            {activeTab === 'overview' && (
            <section className="grid grid-cols-1 md:grid-cols-12 gap-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
              {/* Total Value */}
              <div className="md:col-span-4 glass-card p-6 relative overflow-hidden group">
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
              <div className="md:col-span-4 glass-card p-6 relative overflow-hidden group">
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
              <div className="md:col-span-4 glass-card p-6 flex flex-col relative overflow-hidden">
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
                        formatter={(value, name) => [
                          `¥${Number(value).toLocaleString()}`,
                          String(name ?? '市值'),
                        ]}
                      />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
              </div>
            </section>
            )}

            {/* Profile Loading/Error/Panel (only shows in profile tab) */}
            {activeTab === 'profile' && (
              <div className="animate-in fade-in slide-in-from-bottom-4 duration-500">
            {profileLoading ? (
              <section className="glass-card p-8 flex items-center gap-4">
                <RefreshCw className="w-5 h-5 animate-spin text-amber-400" />
                <div>
                  <h2 className="text-lg font-bold text-slate-100">正在生成组合画像</h2>
                  <p className="text-sm text-slate-500">默认只读取已有分类配置和缓存，不会调用分类 LLM。</p>
                </div>
              </section>
            ) : profileError ? (
              <section className="bg-red-950/20 border border-red-500/30 rounded-3xl p-8 flex flex-col md:flex-row md:items-center justify-between gap-4">
                <div className="flex items-start gap-3">
                  <AlertTriangle className="w-5 h-5 text-red-400 mt-1" />
                  <div>
                    <h2 className="text-lg font-bold text-red-200">组合画像生成失败</h2>
                    <p className="text-sm text-red-200/70 mt-1">{profileError}</p>
                  </div>
                </div>
                <button
                  onClick={() => fetchProfile(false)}
                  className="inline-flex items-center justify-center gap-2 rounded-xl bg-red-500/10 hover:bg-red-500/20 border border-red-500/30 px-4 py-2 text-sm font-bold text-red-100 transition-colors"
                >
                  <RefreshCw className="w-4 h-4" /> 重试
                </button>
              </section>
            ) : profile ? (
              <PortfolioProfilePanel 
                profile={profile} 
                refreshing={profileRefreshing}
                onRefreshClassification={() => fetchProfile(true)}
                onUpdateProfile={(newProfile) => setProfile(newProfile)}
              />            ) : null}
              </div>
            )}

            {/* AI Analysis Result Panel (only shows when aiData exists) */}
            {activeTab === 'analysis' && (
              <div className="space-y-8 animate-in fade-in slide-in-from-bottom-4 duration-500">
                {!aiData && !analyzing && analysisTrace.length === 0 && (
                  <div className="text-center py-20 text-slate-500 bg-slate-900/30 rounded-3xl border border-slate-800/50 border-dashed">
                    <Cpu className="w-12 h-12 mx-auto mb-4 opacity-20" />
                    <p>点击右上角的“一键启动 AI 诊断”开始分析您的持仓</p>
                  </div>
                )}
            {aiData && (
              <section className="relative group">
                <div className="absolute -inset-1 bg-gradient-to-r from-violet-500 via-purple-500 to-amber-500 rounded-[2.5rem] blur opacity-20 group-hover:opacity-30 transition duration-1000 group-hover:duration-200"></div>
                <div className="relative glass-card p-6 md:p-10 !rounded-[2rem]">
                  
                  {/* AI Top Summary */}
                  <div className="flex flex-col lg:flex-row justify-between items-start gap-8 mb-10 border-b border-white/5 pb-10">
                    <div className="flex-1 space-y-4">
                      <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-violet-500/10 border border-violet-500/30 text-violet-300 text-sm font-semibold">
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
                                aiActionTone(action.type)
                              }`}>
                                {aiActionLabel(action.type)}
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
              </div>
            )}

            {/* Market Trend Analysis (K-Line Chart) */}
            {activeTab === 'overview' && (
              <div className="space-y-12 animate-in fade-in slide-in-from-bottom-4 duration-500">
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
                        aiAction={aiData?.action_items?.find((a: any) => a.target === h.name || a.target === h.code)}
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
                        aiAction={aiData?.action_items?.find((a: any) => a.target === h.name || a.target === h.code)}
                        onSelect={() => setSelectedSymbol(h.code)}
                      />
                    ))}
                  </div>
                </section>
              )}

            </div>
              </div>
            )}
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

function AgentTraceIcon({ entry }: { entry: AgentTraceEntry }) {
  if (entry.step === 'research_plan') return <LineChart className="w-4 h-4" />;
  if (entry.step === 'observation_reflection') return <MessageSquare className="w-4 h-4" />;
  if (entry.step === 'llm_turn' || entry.step === 'llm_decision') return <MessageSquare className="w-4 h-4" />;
  if (entry.step === 'tool_call') return <Wrench className="w-4 h-4" />;
  if (entry.step === 'tool_observation') return <Database className="w-4 h-4" />;
  if (entry.step === 'final_report' || entry.step === 'done') return <FileText className="w-4 h-4" />;
  if (entry.step === 'error') return <AlertTriangle className="w-4 h-4" />;
  return <Activity className="w-4 h-4" />;
}

function TraceDetailBlock({ title, icon, children }: { title: string, icon: ReactNode, children: ReactNode }) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/50 overflow-hidden">
      <div className="px-4 py-2.5 border-b border-slate-800 flex items-center gap-2 text-sm font-bold text-slate-200">
        <span className="text-indigo-300">{icon}</span>
        {title}
      </div>
      <div className="p-4">{children}</div>
    </div>
  );
}

function TraceJson({ value }: { value: unknown }) {
  const text = formatJson(value);
  if (!text) return <div className="text-sm text-slate-500">无内容</div>;
  return (
    <pre className="max-h-[300px] overflow-auto custom-scrollbar whitespace-pre-wrap break-words rounded-lg bg-slate-950/80 border border-slate-800 p-3 text-xs leading-relaxed text-slate-300">
      {text}
    </pre>
  );
}

function AgentTraceDetail({ entry }: { entry: AgentTraceEntry | null }) {
  if (!entry) {
    return (
      <div className="h-full min-h-[300px] flex flex-col items-center justify-center text-slate-600">
        <Cpu className="w-10 h-10 mb-3 opacity-30" />
        <p className="text-sm">等待 Agent 事件</p>
      </div>
    );
  }

  const payload = entry.payload || {};
  const observation = payload.observation;
  const rawText = typeof payload.raw_text === 'string' ? payload.raw_text : '';
  const reasoningSummary = textValue(payload.reasoning_summary);
  const missingCapabilities = Array.isArray(payload.missing_capabilities) ? payload.missing_capabilities : [];
  const hasRawText = rawText.trim().length > 0;
  const hasParsed = payload.parsed !== undefined && payload.parsed !== null && payload.parsed !== '';
  const hasArguments = payload.arguments !== undefined && payload.arguments !== null && payload.arguments !== '';
  const hasObservation = observation !== undefined && observation !== null && observation !== '';
  const hasResearchPlan = payload.research_plan !== undefined && payload.research_plan !== null && payload.research_plan !== '';
  const hasThinkingTrace = payload.thinking_trace !== undefined && payload.thinking_trace !== null && payload.thinking_trace !== '';
  const hasObservationReflection = payload.observation_reflection !== undefined && payload.observation_reflection !== null && payload.observation_reflection !== '';

  return (
    <div className="p-4 md:p-5 space-y-4">
      <div className="flex flex-col md:flex-row md:items-start justify-between gap-3 border-b border-slate-800 pb-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-slate-100 font-bold">
            <span className={`rounded-lg border p-1.5 ${TRACE_TONE_CLASS[entry.tone]}`}>
              <AgentTraceIcon entry={entry} />
            </span>
            <span>{entry.title}</span>
          </div>
          {entry.subtitle && <p className="mt-2 text-sm leading-relaxed text-slate-400">{entry.subtitle}</p>}
        </div>
        <div className="shrink-0 text-xs font-mono text-slate-500">
          {entry.turn ? `turn ${entry.turn} · ` : ''}{entry.time}
        </div>
      </div>

      {reasoningSummary && (
        <TraceDetailBlock title="决策依据摘要" icon={<MessageSquare className="w-4 h-4" />}>
          <p className="text-sm leading-relaxed text-slate-300">{reasoningSummary}</p>
        </TraceDetailBlock>
      )}

      {hasResearchPlan && (
        <TraceDetailBlock title="研究计划" icon={<LineChart className="w-4 h-4" />}>
          <TraceJson value={payload.research_plan} />
        </TraceDetailBlock>
      )}

      {hasThinkingTrace && (
        <TraceDetailBlock title="可审计思考轨迹" icon={<Braces className="w-4 h-4" />}>
          <TraceJson value={payload.thinking_trace} />
        </TraceDetailBlock>
      )}

      {hasObservationReflection && (
        <TraceDetailBlock title="工具结果反思" icon={<MessageSquare className="w-4 h-4" />}>
          <TraceJson value={payload.observation_reflection} />
        </TraceDetailBlock>
      )}

      {missingCapabilities.length > 0 && (
        <TraceDetailBlock title="当前缺失能力" icon={<AlertTriangle className="w-4 h-4" />}>
          <div className="space-y-2">
            {missingCapabilities.map((item, index) => (
              <div key={`${String(item)}-${index}`} className="rounded-lg border border-amber-500/20 bg-amber-500/10 px-3 py-2 text-sm text-amber-200">
                {String(item)}
              </div>
            ))}
          </div>
        </TraceDetailBlock>
      )}

      {hasRawText && (
        <TraceDetailBlock title="LLM 可见输出" icon={<FileText className="w-4 h-4" />}>
          <TraceJson value={rawText} />
        </TraceDetailBlock>
      )}

      {hasParsed && (
        <TraceDetailBlock title="解析后的 LLM 决策" icon={<Braces className="w-4 h-4" />}>
          <TraceJson value={payload.parsed} />
        </TraceDetailBlock>
      )}

      {hasArguments && (
        <TraceDetailBlock title="工具入参" icon={<Wrench className="w-4 h-4" />}>
          <TraceJson value={payload.arguments} />
        </TraceDetailBlock>
      )}

      {hasObservation && (
        <TraceDetailBlock title="工具返回内容" icon={<Database className="w-4 h-4" />}>
          <TraceJson value={observation} />
        </TraceDetailBlock>
      )}

      {!hasRawText && !hasParsed && !hasArguments && !hasObservation && !hasResearchPlan && !hasThinkingTrace && !hasObservationReflection && missingCapabilities.length === 0 && (
        <TraceDetailBlock title="事件详情" icon={<Braces className="w-4 h-4" />}>
          <TraceJson value={traceDisplayPayload(payload)} />
        </TraceDetailBlock>
      )}
    </div>
  );
}

function PortfolioProfilePanel({ profile, refreshing, onRefreshClassification, onUpdateProfile }: { profile: any, refreshing: boolean, onRefreshClassification: () => void, onUpdateProfile: (newProfile: any) => void }) {
  const [filter, setFilter] = useState<{ type: 'asset_class' | 'strategy' | 'sector', value: string } | null>(null);
  const [refreshingCodes, setRefreshingCodes] = useState<Set<string>>(new Set());
  
  const summary = profile.summary || {};
  const assetRows = pctRows(summary.by_asset_class);
  const strategyRows = pctRows(summary.by_strategy);
  const sectorRows = pctRows(summary.by_sector).slice(0, 10);
  const topPositions = summary.top_positions || [];
  const positions = summary.positions || [];
  const filteredPositions = filter 
    ? positions.filter((p: any) => p[filter.type] === filter.value)
    : positions;

  const observations = profile.observations || [];
  const lowConfidence = Number(summary.low_confidence_classification_pct || 0);
  const unknown = Number(summary.unknown_classification_pct || 0);

  const handleSingleClassify = async (code: string) => {
    setRefreshingCodes(prev => new Set(prev).add(code));
    try {
      const res = await fetch(`/api/classify/${code}`, { method: 'POST' });
      if (!res.ok) throw new Error('分类失败');
      const newCls = await res.json();
      
      // 更新本地 profile 状态
      const updatedProfile = { ...profile };
      if (updatedProfile.summary && updatedProfile.summary.positions) {
        updatedProfile.summary.positions = updatedProfile.summary.positions.map((p: any) => 
          p.code === code ? { 
            ...p, 
            asset_class: newCls.asset_class,
            sector: newCls.sector,
            theme: newCls.theme,
            strategy: newCls.strategy,
            classification_confidence: newCls.confidence,
            classification_source: newCls.source
          } : p
        );
        onUpdateProfile(updatedProfile);
      }
    } catch (err) {
      console.error(err);
      alert(`${code} 分类刷新失败`);
    } finally {
      setRefreshingCodes(prev => {
        const next = new Set(prev);
        next.delete(code);
        return next;
      });
    }
  };

  return (
    <section className="space-y-6">
      <div className="flex flex-col xl:flex-row xl:items-end justify-between gap-5">
        <div>
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-cyan-500/10 border border-cyan-500/20 text-cyan-300 text-xs font-bold uppercase tracking-wider mb-3">
            <Database className="w-3.5 h-3.5" /> Portfolio Profile
          </div>
          <h2 className="text-2xl md:text-3xl font-black text-slate-100">组合画像</h2>
          <p className="text-sm text-slate-500 mt-2 max-w-2xl">
            基于已同步持仓、手动分类和本地分类缓存生成。默认不触发搜索和 LLM；只有点击刷新分类画像才会补全低置信度或缺失分类。
          </p>
        </div>
        <div className="flex flex-col sm:flex-row gap-3">
          <button
            onClick={onRefreshClassification}
            disabled={refreshing}
            className="inline-flex items-center justify-center gap-2 rounded-xl bg-cyan-500/10 hover:bg-cyan-500/20 disabled:opacity-60 border border-cyan-500/30 px-4 py-2.5 text-sm font-bold text-cyan-100 transition-colors"
            title="允许触发搜索和分类 LLM，刷新缺失、过期或低置信度分类"
          >
            <RefreshCw className={`w-4 h-4 ${refreshing ? 'animate-spin' : ''}`} />
            {refreshing ? '正在刷新分类...' : '刷新分类画像'}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
        <ProfileMetric icon={<Wallet className="w-5 h-5" />} label="画像总市值" value={formatMoney(summary.total_value)} tone="text-blue-300" />
        <ProfileMetric icon={<Layers className="w-5 h-5" />} label="持仓数量" value={`${summary.position_count || 0} 个`} tone="text-emerald-300" />
        <ProfileMetric icon={<AlertTriangle className="w-5 h-5" />} label="未知分类占比" value={formatPct(unknown)} tone={unknown > 0 ? 'text-amber-300' : 'text-slate-300'} />
        <ProfileMetric icon={<CheckCircle2 className="w-5 h-5" />} label="低置信度占比" value={formatPct(lowConfidence)} tone={lowConfidence > 0 ? 'text-amber-300' : 'text-emerald-300'} />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-12 gap-6">
        <div className="xl:col-span-7 grid grid-cols-1 lg:grid-cols-2 gap-6">
          <ProfileDistribution 
            title="资产大类" 
            rows={assetRows} 
            onSelect={(val) => setFilter({ type: 'asset_class', value: val })}
            activeValue={filter?.type === 'asset_class' ? filter.value : null}
          />
          <ProfileDistribution 
            title="策略来源" 
            rows={strategyRows} 
            onSelect={(val) => setFilter({ type: 'strategy', value: val })}
            activeValue={filter?.type === 'strategy' ? filter.value : null}
          />
        </div>

        <div className="xl:col-span-5 bg-slate-900/50 border border-slate-800 rounded-2xl p-5">
          <div className="flex items-center justify-between mb-5">
            <h3 className="text-base font-bold text-slate-100">Top 持仓</h3>
            <span className="text-xs text-slate-500">按市值排序</span>
          </div>
          <div className="space-y-4">
            {topPositions.map((item: any) => (
              <div key={item.code} className="space-y-2">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="font-bold text-slate-200 truncate">{item.name}</div>
                    <div className="text-xs text-slate-500 font-mono">{item.code} · {labelOf(item.asset_class)} · {labelOf(item.strategy)}</div>
                  </div>
                  <div className="text-right shrink-0">
                    <div className="text-sm font-bold text-slate-100">{formatPct(item.weight)}</div>
                    <div className="text-xs text-slate-500">{formatMoney(item.market_value)}</div>
                  </div>
                </div>
                <div className="h-2 rounded-full bg-slate-800 overflow-hidden">
                  <div className="h-full rounded-full bg-blue-400" style={{ width: `${Math.min(Number(item.weight || 0), 100)}%` }}></div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-12 gap-6">
        <div className="xl:col-span-5 bg-slate-900/50 border border-slate-800 rounded-2xl p-5">
          <div className="flex items-center justify-between mb-5">
            <h3 className="text-base font-bold text-slate-100">行业暴露</h3>
            <span className="text-xs text-slate-500">点击分类可过滤下方列表</span>
          </div>
          <ProfileBarList 
            rows={sectorRows} 
            onSelect={(val) => setFilter({ type: 'sector', value: val })}
            activeValue={filter?.type === 'sector' ? filter.value : null}
          />
        </div>

        <div className="xl:col-span-7 bg-slate-900/50 border border-slate-800 rounded-2xl p-5">
          <div className="flex items-center justify-between mb-5">
            <h3 className="text-base font-bold text-slate-100">事实观察项</h3>
            <span className="text-xs text-slate-500">{observations.length} 条</span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {observations.map((item: any) => (
              <div key={item.id} className="rounded-xl bg-slate-950/40 border border-slate-800 p-4">
                <div className="text-sm font-bold text-slate-200">{item.label}</div>
                <div className="mt-2 flex flex-wrap gap-2">
                  {(item.evidence || []).map((evidence: string) => (
                    <span key={evidence} className="rounded-lg bg-slate-800/70 px-2 py-1 text-xs font-mono text-slate-400">{evidence}</span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="bg-slate-900/50 border border-slate-800 rounded-2xl overflow-hidden">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-800">
          <div className="flex items-center gap-4">
            <h3 className="text-base font-bold text-slate-100">分类明细</h3>
            {filter && (
              <button 
                onClick={() => setFilter(null)}
                className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md bg-indigo-500/20 text-indigo-300 text-xs border border-indigo-500/30 hover:bg-indigo-500/30 transition-colors"
              >
                <span>正在查看: {labelOf(filter.value)}</span>
                <RotateCcw className="w-3 h-3" />
                <span>清除过滤</span>
              </button>
            )}
          </div>
          <span className="text-xs text-slate-500">按市值排序，低置信度优先人工复核</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-slate-950/40 text-slate-500">
              <tr>
                <th className="text-left font-semibold px-5 py-3">标的</th>
                <th className="text-left font-semibold px-5 py-3">资产大类</th>
                <th className="text-left font-semibold px-5 py-3">行业/主题</th>
                <th className="text-left font-semibold px-5 py-3">策略</th>
                <th className="text-right font-semibold px-5 py-3">占比</th>
                <th className="text-right font-semibold px-5 py-3">置信度</th>
                <th className="text-left font-semibold px-5 py-3">来源</th>
                <th className="text-center font-semibold px-5 py-3">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/70">
              {filteredPositions.map((item: any) => (
                <tr key={item.code} className="hover:bg-slate-800/30 transition-colors">
                  <td className="px-5 py-3">
                    <div className="font-bold text-slate-200">{item.name}</div>
                    <div className="text-xs text-slate-500 font-mono">{item.code}</div>
                  </td>
                  <td className="px-5 py-3 text-slate-300">{labelOf(item.asset_class)}</td>
                  <td className="px-5 py-3 text-slate-400">{labelOf(item.sector)} / {labelOf(item.theme)}</td>
                  <td className="px-5 py-3 text-slate-400">{labelOf(item.strategy)}</td>
                  <td className="px-5 py-3 text-right font-mono text-slate-200">{formatPct(item.weight)}</td>
                  <td className={`px-5 py-3 text-right font-mono ${Number(item.classification_confidence || 0) < 0.75 ? 'text-amber-300' : 'text-emerald-300'}`}>
                    {Number(item.classification_confidence || 0).toFixed(2)}
                  </td>
                  <td className="px-5 py-3">
                    <span className="rounded-lg bg-slate-800 px-2 py-1 text-xs text-slate-400">{labelOf(item.classification_source)}</span>
                  </td>
                  <td className="px-5 py-3 text-center">
                    <button 
                      onClick={() => handleSingleClassify(item.code)}
                      disabled={refreshingCodes.has(item.code)}
                      className="p-1.5 rounded-lg bg-slate-800 text-slate-400 hover:text-cyan-400 hover:bg-slate-700 transition-all disabled:opacity-50"
                      title="单独触发此标的的 AI 搜索与分类"
                    >
                      <RefreshCw className={`w-4 h-4 ${refreshingCodes.has(item.code) ? 'animate-spin' : ''}`} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}

function ProfileMetric({ icon, label, value, tone }: { icon: ReactNode, label: string, value: string, tone: string }) {
  return (
    <div className="bg-slate-900/50 border border-slate-800 rounded-2xl p-5">
      <div className={`mb-4 inline-flex rounded-xl bg-slate-800/70 p-2 ${tone}`}>{icon}</div>
      <div className="text-xs font-bold uppercase tracking-wider text-slate-500">{label}</div>
      <div className={`mt-1 text-2xl font-black ${tone}`}>{value}</div>
    </div>
  );
}

function ProfileDistribution({ title, rows, onSelect, activeValue }: { title: string, rows: Array<{ key: string, label: string, pct: number }>, onSelect: (val: string) => void, activeValue?: string | null }) {
  return (
    <div className="bg-slate-900/50 border border-slate-800 rounded-2xl p-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-base font-bold text-slate-100">{title}</h3>
        <span className="text-xs text-slate-500">{rows.length} 项</span>
      </div>
      <div className="h-48">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie 
              data={rows} 
              dataKey="pct" 
              nameKey="label" 
              cx="50%" cy="50%" 
              innerRadius={48} outerRadius={78} 
              stroke="none" paddingAngle={2}
              onClick={(data) => {
                if (data && data.payload && data.payload.key) {
                  onSelect(data.payload.key);
                }
              }}
              className="cursor-pointer"
            >
              {rows.map((row, index) => (
                <Cell 
                  key={`profile-cell-${index}`} 
                  fill={activeValue && activeValue !== row.key ? '#334155' : COLORS[index % COLORS.length]} 
                  className="transition-all duration-300"
                />
              ))}
            </Pie>
            <Tooltip
              contentStyle={{ backgroundColor: 'rgba(15, 23, 42, 0.95)', border: '1px solid rgba(51, 65, 85, 0.8)', borderRadius: '12px', color: '#fff' }}
              formatter={(value, name) => [formatPct(value), String(name)]}
            />
          </PieChart>
        </ResponsiveContainer>
      </div>
      <ProfileBarList rows={rows.slice(0, 5)} onSelect={onSelect} activeValue={activeValue} />
    </div>
  );
}

function ProfileBarList({ rows, onSelect, activeValue }: { rows: Array<{ key: string, label: string, pct: number }>, onSelect?: (val: string) => void, activeValue?: string | null }) {
  if (!rows.length) {
    return <div className="rounded-xl border border-slate-800 bg-slate-950/30 p-4 text-sm text-slate-500">暂无可展示数据</div>;
  }
  return (
    <div className="space-y-3">
      {rows.map((row, index) => (
        <div 
          key={row.key} 
          className={`space-y-1.5 transition-all duration-200 ${onSelect ? 'cursor-pointer hover:bg-slate-800/30 rounded-lg p-1 -m-1' : ''} ${activeValue === row.key ? 'bg-indigo-500/10 -m-1 p-1 rounded-lg border border-indigo-500/20' : ''}`}
          onClick={() => onSelect?.(row.key)}
        >
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2 min-w-0">
              <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ backgroundColor: activeValue && activeValue !== row.key ? '#334155' : COLORS[index % COLORS.length] }}></span>
              <span className={`text-sm truncate ${activeValue === row.key ? 'text-indigo-300 font-bold' : 'text-slate-300'}`}>{row.label}</span>
            </div>
            <span className="text-xs font-mono text-slate-400">{formatPct(row.pct)}</span>
          </div>
          <div className="h-1.5 rounded-full bg-slate-800 overflow-hidden">
            <div className="h-full rounded-full transition-all duration-500" style={{ width: `${Math.min(row.pct, 100)}%`, backgroundColor: activeValue && activeValue !== row.key ? '#334155' : COLORS[index % COLORS.length] }}></div>
          </div>
        </div>
      ))}
    </div>
  );
}

// Sub-component for individual asset cards
function HoldingCard({ holding, isFund = false, isActive = false, aiAction, onSelect }: { holding: any, isFund?: boolean, isActive?: boolean, aiAction?: any, onSelect: () => void }) {
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
      
      <div className="pt-4 border-t border-slate-800 space-y-3">
        {/* Rule Engine Suggestion */}
        {(!aiAction || holding.action) && (
          <div className="flex items-start gap-2">
            {isFund ? (
              <Landmark className="w-4 h-4 mt-0.5 text-slate-500 shrink-0"/>
            ) : (
              <Activity className="w-4 h-4 mt-0.5 text-slate-500 shrink-0"/>
            )}
            <div>
              <span className={`text-xs font-medium px-2 py-0.5 rounded mr-2 ${isFund ? 'bg-slate-800 text-slate-400' : 'bg-slate-800 text-slate-400'}`}>
                {holding.action || '常规持有'}
              </span>
              <p className="text-xs text-slate-500 line-clamp-1 mt-1 leading-relaxed" title={holding.reason}>
                {holding.reason || '当前暂无特殊技术面信号'}
              </p>
            </div>
          </div>
        )}

        {/* AI Action Suggestion */}
        {aiAction && (
          <div className={`flex items-start gap-2 ${(!aiAction || holding.action) ? 'pt-2 border-t border-slate-800/50' : ''}`}>
            <Cpu className="w-4 h-4 mt-0.5 text-indigo-400 shrink-0"/>
            <div>
              <span className={`text-xs font-bold px-2 py-0.5 rounded mr-2 uppercase tracking-wider ${
                aiActionTone(aiAction.type)
              }`}>
                {aiActionLabel(aiAction.type, 'AI 建议')}
              </span>
              <p className="text-xs text-indigo-200/80 line-clamp-2 mt-1 leading-relaxed" title={aiAction.reason}>
                {aiAction.reason}
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
