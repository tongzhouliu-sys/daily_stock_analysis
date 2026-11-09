import { useEffect } from "react";
import { Link, NavLink, Route, Routes } from "react-router-dom";
import { useAuth } from "../../contexts/AuthContext";
import { useData, State } from "./components/ui";
import Dashboard from "./pages/Dashboard";
import Signals from "./pages/Signals";
import Detail from "./pages/Detail";
import Journal from "./pages/Journal";
import Settings from "./pages/Settings";
import Screen from "./pages/Screen";
import ResearchList from "./pages/rule-validation/List";
import ResearchNew from "./pages/rule-validation/New";
import ResearchRun from "./pages/rule-validation/Run";
import ResearchReport from "./pages/rule-validation/Report";
import ResearchEvent from "./pages/rule-validation/Event";
import ResearchCompare from "./pages/rule-validation/Compare";
import ResearchProbes from "./pages/rule-validation/Probes";
import "./styles/app.css";
import "./styles/research.css";
import "./styles/host.css";

export default function ScannerWorkspace() {
  const { refreshStatus } = useAuth();
  const session = useData("/api/auth/session");
  useData(session.data ? "/api/configs/preferences" : null);
  useEffect(() => {
    const expired = () => { void refreshStatus(); };
    window.addEventListener("scanner-session-expired", expired);
    return () => window.removeEventListener("scanner-session-expired", expired);
  }, [refreshStatus]);
  return <section className="scanner-workspace" aria-label="交易信号扫描器">
    <header className="scanner-heading"><div><h1>交易信号扫描器</h1><p className="muted">美股研究 · 证据、人工复核与复盘</p></div><Link className="button" to="/">返回研究首页</Link></header>
    <nav className="scanner-tabs" aria-label="扫描器导航">
      {[["", "今日工作台"], ["/signals", "信号与观察"], ["/journal", "日志与复盘"], ["/rule-validation", "规则验证"], ["/settings", "设置与数据"], ["/screen", "全景大屏"]].map(([path,label]) => <NavLink key={path} end={!path} to={"/scanner"+path}>{label}</NavLink>)}
    </nav>
    {!session.data ? <State loading={session.loading} error={session.error} /> : <Routes>
      <Route index element={<Dashboard />} />
      <Route path="signals" element={<Signals />} />
      <Route path="signals/:id" element={<Detail />} />
      <Route path="journal" element={<Journal />} />
      <Route path="settings" element={<Settings />} />
      <Route path="screen" element={<Screen />} />
      <Route path="rule-validation" element={<ResearchList />} />
      <Route path="rule-validation/new" element={<ResearchNew />} />
      <Route path="rule-validation/compare" element={<ResearchCompare />} />
      <Route path="rule-validation/probes" element={<ResearchProbes />} />
      <Route path="rule-validation/runs/:runId" element={<ResearchRun />} />
      <Route path="rule-validation/runs/:runId/report" element={<ResearchReport />} />
      <Route path="rule-validation/runs/:runId/events/:eventId" element={<ResearchEvent />} />
      <Route path="*" element={<p>页面不存在。<Link to="/scanner">返回工作台</Link></p>} />
    </Routes>}
  </section>;
}
