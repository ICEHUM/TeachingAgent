import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api, newOperation } from "./api";
import { ReviewView } from "./ReviewView";
import type { Classroom, ClassroomItem, Detail, Evidence } from "./types";

type GroupKey = "attention" | "progress" | "completed";
const groupNames: Record<GroupKey, string> = { attention: "需要关注", progress: "进行中", completed: "已完成" };
const reasonNames: Record<string, string> = { failure_threshold_reached: "连续检查未通过", l2_authorization_required: "需要授权局部示例", pending_review: "提交待复核", grade_published: "成绩已发布", normal: "正常学习中" };
const helpNames: Record<string, string> = { L0: "引导", L1: "定位", L2: "局部示例", NONE: "尚未指导" };
const requirementStatus = (value: string) => ({ SATISFIED: "已满足", NOT_SATISFIED: "未满足", INFRASTRUCTURE_ERROR: "环境异常", NOT_RUN: "未运行" }[value] || value);
const time = (value: string) => new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit" }).format(new Date(value));
const waitText = (seconds: number) => seconds >= 3600 ? `${Math.floor(seconds / 3600)} 小时` : seconds >= 60 ? `${Math.floor(seconds / 60)} 分钟` : "刚刚";

export function App() {
  const params = useMemo(() => new URLSearchParams(location.search), []);
  const userId = params.get("user") || params.get("user_id") || "";
  const healthRequested = params.get("view") === "health";
  const [classroom, setClassroom] = useState<Classroom | null>(null);
  const [group, setGroup] = useState<GroupKey>("attention");
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState("");
  const [prompt, setPrompt] = useState("");
  const [actionLoading, setActionLoading] = useState("");
  const [actionError, setActionError] = useState("");
  const [showTrace, setShowTrace] = useState(false);
  const [detailOpen, setDetailOpen] = useState(false);
  const [reviewId, setReviewId] = useState<string | null>(null);
  const [connection, setConnection] = useState<"connected" | "reconnecting" | "offline">("reconnecting");
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const selectedRef = useRef<string | null>(null);
  const selectionInitialized = useRef(false);
  const detailPanelRef = useRef<HTMLElement | null>(null);
  const studentTriggerRef = useRef<HTMLElement | null>(null);

  const loadClassroom = useCallback(async () => {
    if (!userId) { setLoading(false); return; }
    try {
      const data = await api<Classroom>("/api/product/teacher/classroom", userId);
      setClassroom(data); setError(""); setConnection("connected"); setLastUpdated(new Date());
      const all = [...data.groups.attention, ...data.groups.progress, ...data.groups.completed];
      if (!selectionInitialized.current || (selectedRef.current && !all.some((item) => item.attempt_id === selectedRef.current))) {
        const first = data.groups.attention[0] || data.groups.progress[0] || data.groups.completed[0];
        selectedRef.current = first?.attempt_id || null;
        setSelected(selectedRef.current);
        if (first) setGroup(first.category as GroupKey);
        selectionInitialized.current = true;
      }
    } catch (cause) { setError(cause instanceof Error ? cause.message : "课堂数据载入失败"); setConnection(navigator.onLine ? "reconnecting" : "offline"); }
    finally { setLoading(false); }
  }, [userId]);

  const loadDetail = useCallback(async (attemptId: string) => {
    setDetailLoading(true);
    try { setDetail(await api<Detail>(`/api/product/teacher/attempts/${attemptId}`, userId)); setActionError(""); }
    catch (cause) { setActionError(cause instanceof Error ? cause.message : "学生详情载入失败"); }
    finally { setDetailLoading(false); }
  }, [userId]);

  useEffect(() => { void loadClassroom(); }, [loadClassroom]);
  useEffect(() => { if (selected) void loadDetail(selected); }, [loadDetail, selected]);
  useEffect(() => {
    if (!detailOpen || !matchMedia("(max-width: 1050px)").matches) return;
    const panel = detailPanelRef.current;
    const focusable = () => panel ? Array.from(panel.querySelectorAll<HTMLElement>('button:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])')) : [];
    requestAnimationFrame(() => focusable()[0]?.focus());
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") { setDetailOpen(false); requestAnimationFrame(() => studentTriggerRef.current?.focus()); return; }
      if (event.key === "Tab") { const items = focusable(); if (!items.length) return; const first = items[0]; const last = items[items.length - 1]; if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); } else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); } }
    };
    addEventListener("keydown", close);
    return () => removeEventListener("keydown", close);
  }, [detailOpen]);
  useEffect(() => {
    const offline = () => setConnection("offline"); const online = () => { setConnection("reconnecting"); void loadClassroom().then(() => setConnection("connected")); };
    addEventListener("offline", offline); addEventListener("online", online);
    const refresh = window.setInterval(() => { if (navigator.onLine) void loadClassroom(); }, 5000);
    return () => { clearInterval(refresh); removeEventListener("offline", offline); removeEventListener("online", online); };
  }, [loadClassroom]);

  function chooseGroup(key: GroupKey) {
    setGroup(key);
    const first = classroom?.groups[key][0];
    selectedRef.current = first?.attempt_id || null;
    setSelected(selectedRef.current);
    setDetail(null);
    setDetailOpen(false);
  }

  function chooseStudent(attemptId: string, trigger: HTMLElement) {
    studentTriggerRef.current = trigger;
    selectedRef.current = attemptId;
    setSelected(attemptId);
    setDetailOpen(true);
  }

  async function takeAction(action: "continue" | "allow_l2" | "pause_ai" | "resume") {
    if (!detail) return;
    const intervention = detail.interventions.find((item) => ["WAITING_TEACHER", "RESUME_FAILED"].includes(item.status));
    if (!intervention) { setActionError("当前没有可处理的教师介入事项。"); return; }
    setActionLoading(action); setActionError("");
    try {
      await api(`/api/product/teacher/interventions/${intervention.id}/action`, userId, {
        method: "POST", body: JSON.stringify({ operation_id: newOperation(`teacher-${action}`), expected_state_version: intervention.requested_state_version, action, teacher_prompt: prompt }),
      });
      setPrompt(""); await loadClassroom(); await loadDetail(detail.attempt.id);
    } catch (cause) {
      setActionError(cause instanceof ApiError && cause.code === "resume_failed" ? "恢复失败：教师操作已保留，可以安全重试。" : cause instanceof Error ? cause.message : "教师操作失败");
      await loadDetail(detail.attempt.id);
    } finally { setActionLoading(""); }
  }

  if (!userId) return <Setup />;
  if (healthRequested) return <DemoHealthView userId={userId} />;
  if (loading) return <Loading />;
  if (error || !classroom) return <Fatal message={error || "课堂工作台不可用"} retry={() => { setLoading(true); void loadClassroom(); }} />;
  if (reviewId) return <div className="teacher-app"><header className="topbar"><div className="brand"><span className="brand-mark">AI</span><strong>教师课堂台</strong></div></header><ReviewView userId={userId} submissionId={reviewId} onBack={() => { setReviewId(null); void loadClassroom(); }} /></div>;
  const currentList = classroom.groups[group];
  const groupTitle = group === "attention" ? "现在谁需要教师帮助" : group === "progress" ? "跟进正在学习的学生" : "查看已完成的学习记录";

  return <div className="teacher-app">
    <header className="topbar">
      <div className="brand"><span className="brand-mark">AI</span><strong>教师课堂台</strong></div>
      <div className="course-context"><b>AI 应用开发实训</b><span>FAQ-001-v1 · 当前课堂</span></div>
      <div className="top-actions"><a className="health-link" href={`?user=${encodeURIComponent(userId)}&view=health`}>演示健康</a><span className={`connection ${connection}`}><i />{connection === "connected" ? "自动更新" : connection === "offline" ? "网络断开" : "正在重连"}{lastUpdated && <small>· {time(lastUpdated.toISOString())}</small>}</span><span className="avatar" aria-label={`当前教师：${classroom.teacher.display_name}`}>{classroom.teacher.display_name.slice(0, 1)}</span></div>
    </header>
    <main className="teacher-layout">
      <section className="classroom-list" aria-label="课堂学生列表">
        <header className="list-head"><div className="list-head-copy"><h1>{groupTitle}</h1><p>按介入优先级组织课堂事实，每 5 秒同步一次。</p></div><div className="classroom-overview" aria-label="课堂状态概览"><span className={classroom.groups.attention.length ? "needs-attention" : ""}><b>{classroom.groups.attention.length}</b><small>待介入</small></span><span><b>{classroom.groups.progress.length}</b><small>学习中</small></span><span><b>{classroom.groups.completed.length}</b><small>已完成</small></span></div><button className="refresh" onClick={() => void loadClassroom()}>刷新数据</button></header>
        <nav className="group-tabs" aria-label="学生状态分组">{(Object.keys(groupNames) as GroupKey[]).map((key) => <button key={key} aria-current={group === key} onClick={() => chooseGroup(key)}><span>{groupNames[key]}</span><b>{classroom.groups[key].length}</b></button>)}</nav>
        <div className="student-list">{currentList.length ? currentList.map((item) => <StudentRow key={item.attempt_id} item={item} selected={selected === item.attempt_id} onSelect={(trigger) => chooseStudent(item.attempt_id, trigger)} />) : <EmptyGroup group={group} />}{currentList.length === 1 && <div className="queue-note"><strong>当前队列已按介入优先级排序</strong><span>新的教学事件会自动归入需要关注、进行中或已完成。</span></div>}</div>
      </section>
      {detailOpen && <button className="detail-backdrop" aria-label="返回学生列表" onClick={() => setDetailOpen(false)} />}
      <aside ref={detailPanelRef} className={`intervention-panel ${detailOpen ? "open" : ""}`} role={detailOpen ? "dialog" : undefined} aria-modal={detailOpen ? "true" : undefined} aria-label="教师介入详情">
        {!selected ? <EmptyDetail /> : detailLoading || !detail ? <DetailLoading /> : <InterventionDetail key={detail.attempt.id} detail={detail} prompt={prompt} setPrompt={setPrompt} actionLoading={actionLoading} actionError={actionError} takeAction={takeAction} showTrace={showTrace} setShowTrace={setShowTrace} onClose={() => { setDetailOpen(false); requestAnimationFrame(() => studentTriggerRef.current?.focus()); }} userId={userId} onReview={(id) => setReviewId(id)} />}
      </aside>
    </main>
  </div>;
}

function StudentRow({ item, selected, onSelect }: { item: ClassroomItem; selected: boolean; onSelect: (trigger: HTMLElement) => void }) {
  const attention = item.category === "attention";
  const status = item.intervention_status === "RESUME_FAILED" ? "恢复失败" : attention ? "等待教师" : item.category === "completed" ? "已完成" : "学习中";
  return <button className={`student-row ${selected ? "selected" : ""} ${attention ? "needs-attention" : ""}`} onClick={(event) => onSelect(event.currentTarget)} aria-pressed={selected}>
    <span className="student-identity"><span className="student-avatar" aria-hidden="true">{item.student.slice(0, 1)}</span><span><b>{item.student}</b><small>{item.stage}</small></span></span>
    <span className="student-need"><small>当前情况</small><b>{reasonNames[item.reason] || item.reason}</b>{item.intervention_status === "RESUME_FAILED" && <em>教师操作已保留，可安全重试</em>}</span>
    <span className="student-signals"><span className={item.failure_count >= 3 ? "danger" : ""}><b>{item.failure_count}</b><small>失败</small></span><span><b>{helpNames[item.help_level] || item.help_level}</b><small>已有帮助</small></span><span><b>{attention ? waitText(item.wait_seconds) : "当前"}</b><small>{attention ? "等待" : "状态"}</small></span></span>
    <span className={`status-badge row-status ${item.intervention_status === "RESUME_FAILED" ? "failed" : attention ? "waiting" : item.category === "completed" ? "completed" : "progress"}`}>{status}</span><span className="row-chevron" aria-hidden="true">›</span>
  </button>;
}

function InterventionDetail({ detail, prompt, setPrompt, actionLoading, actionError, takeAction, showTrace, setShowTrace, onClose, userId, onReview }: { detail: Detail; prompt: string; setPrompt: (value: string) => void; actionLoading: string; actionError: string; takeAction: (action: "continue" | "allow_l2" | "pause_ai" | "resume") => void; showTrace: boolean; setShowTrace: (value: boolean) => void; onClose: () => void; userId: string; onReview: (submissionId: string) => void }) {
  const active = detail.interventions.find((item) => ["WAITING_TEACHER", "RESUME_FAILED"].includes(item.status));
  const hasResolved = detail.interventions.some((item) => item.status === "RESOLVED");
  const statusTone = active?.status === "RESUME_FAILED" ? "failed" : active ? "waiting" : detail.attempt.status === "COMPLETED" ? "completed" : hasResolved ? "resolved" : "progress";
  const statusText = active?.status === "RESUME_FAILED" ? "恢复失败" : active ? "等待处理" : detail.attempt.status === "COMPLETED" ? "已完成" : hasResolved ? "流程已恢复" : "学习中";
  const [tab, setTab] = useState<"overview" | "evidence" | "trace">(showTrace ? "trace" : "overview");
  const [selectedEvidence, setSelectedEvidence] = useState<Evidence | null>(null);
  const [evidenceLoading, setEvidenceLoading] = useState("");
  const [actionsOpen, setActionsOpen] = useState(false);
  const interventionBasis = detail.requirements.find((item) => item.status === "NOT_SATISFIED") || detail.requirements[0];
  async function openEvidence(operationId: string | null) {
    if (!operationId) return;
    setEvidenceLoading(operationId);
    try { setSelectedEvidence(await api<Evidence>(`/api/product/attempts/${detail.attempt.id}/evidence/${encodeURIComponent(operationId)}`, userId)); }
    catch { setSelectedEvidence(null); }
    finally { setEvidenceLoading(""); }
  }
  function chooseTab(next: "overview" | "evidence" | "trace") { setTab(next); if (next === "trace") setShowTrace(true); }
  return <>
    <header className="detail-head"><button className="detail-back" onClick={onClose} aria-label="返回学生列表">返回</button><span className="detail-avatar" aria-hidden="true">{detail.identity.display_name.slice(0, 1)}</span><div><h2>{detail.identity.display_name}</h2><p>{detail.stage.title} · 阶段 {detail.stage.position + 1}/{detail.stage.total}</p></div>{detail.latest_submission && <button className="refresh" onClick={() => onReview(detail.latest_submission!.id)}>{detail.latest_submission.formal_grade ? "查看评价" : "评价作品"}</button>}<span className={`status-badge intervention-status ${statusTone}`}>{statusText}</span></header>
    <nav className="detail-tabs" aria-label="学生详情分组"><button aria-current={tab === "overview"} onClick={() => chooseTab("overview")}>概览</button><button aria-current={tab === "evidence"} onClick={() => chooseTab("evidence")}>证据 <span>{detail.requirement_summary.satisfied_count}/{detail.requirement_summary.required_count}</span></button><button aria-current={tab === "trace"} onClick={() => chooseTab("trace")}>轨迹 <span>{detail.timeline.length}</span></button></nav>
    <div className="detail-content">
      <div className="detail-scroll">
        {tab === "overview" && <div className="detail-tab-panel overview-panel"><section className="learning-summary"><div><span>当前阶段</span><h3>{detail.stage.title}</h3><p>{detail.stage.objective}</p></div><div className="requirement-meter"><b>{detail.requirement_summary.satisfied_count}<small> / {detail.requirement_summary.required_count}</small></b><span>验收项已满足</span></div></section><section className="detail-section"><div className="section-heading"><h3>学生观察</h3><span>最近一次提交</span></div><blockquote>{detail.student_observation || "学生尚未提交有效观察。"}</blockquote></section><section className="detail-section"><div className="section-heading"><h3>最近指导</h3><span>{detail.guidance_history.length} 条记录</span></div><div className="guidance-history">{detail.guidance_history.length ? detail.guidance_history.slice(-2).reverse().map((item, index) => <article key={`${item.time}-${index}`}><header><b>{helpNames[item.level || ""] || "引导"}</b><time>{time(item.time)}</time></header><p>{item.message}</p>{item.success === false && <small>已使用安全模板：{item.fallback_reason}</small>}</article>) : <p className="muted">尚无智能指导记录。</p>}</div></section><section className="detail-section"><div className="section-heading"><h3>Snapshot 变化</h3><span>{detail.snapshot_diff.files_changed} 个文件</span></div><div className="diff-summary"><div><span>{detail.snapshot_diff.from || "尚无前序版本"}</span><b>→</b><span>{detail.snapshot_diff.to || "当前版本"}</span></div><p>{detail.snapshot_diff.files_changed} 个文件变化 · <strong>+{detail.snapshot_diff.additions}</strong> / <em>−{detail.snapshot_diff.deletions}</em></p>{detail.snapshot_diff.files?.length ? <small>{detail.snapshot_diff.files.join("、")}</small> : null}</div></section></div>}
        {tab === "evidence" && <div className="detail-tab-panel evidence-panel"><div className="panel-intro"><h3>当前阶段验收证据</h3><p>先阅读教学结论，再决定介入方式。</p></div><div className="teacher-requirements">{detail.requirements.map((item) => <button type="button" key={item.id} className={item.status.toLowerCase()} disabled={!item.operation_id || !!evidenceLoading} onClick={() => void openEvidence(item.operation_id)}><span className="requirement-state" aria-hidden="true">{item.status === "SATISFIED" ? "✓" : item.status === "NOT_SATISFIED" ? "×" : "·"}</span><div><h4>{item.name}</h4><p>{item.snapshot_label || "尚未运行"} · {item.evaluator}</p><small>{evidenceLoading === item.operation_id ? "正在读取证据…" : item.evidence_refs.length ? `${item.evidence_refs.length} 条证据 · 点击查看` : "尚无证据"}</small></div><b>{requirementStatus(item.status)}</b></button>)}</div>{selectedEvidence && <section className={`teacher-evidence-detail ${selectedEvidence.status === "SATISFIED" ? "passed" : "failed"}`}><header><div><span>教学结论</span><h4>{selectedEvidence.status === "SATISFIED" ? "当前要求已满足" : "当前要求尚未满足"}</h4></div><b>{selectedEvidence.snapshot}</b></header><p>{selectedEvidence.reason_code === "empty_retrieval" ? "检索流程已经运行，但已知问题没有返回可用结果。请结合学生观察判断下一步指导。" : "该结论来自当前版本的真实运行证据，可继续查看技术追溯。"}</p><dl><div><dt>检查工具</dt><dd>{selectedEvidence.tool}</dd></div><div><dt>失败原因</dt><dd>{selectedEvidence.reason_code}</dd></div><div><dt>运行时间</dt><dd>{new Date(selectedEvidence.observed_at).toLocaleString("zh-CN")}</dd></div></dl><details><summary>查看有界输出与运行标识</summary><pre>{selectedEvidence.stdout_summary || "无可展示输出"}</pre><code>{selectedEvidence.operation}</code></details></section>}</div>}
        {tab === "trace" && <div className="detail-tab-panel trace-panel"><section className="detail-section"><div className="section-heading"><h3>教学过程</h3><span>{detail.timeline.length} 个事件</span></div><div className="teaching-timeline"><ol>{detail.timeline.length ? detail.timeline.slice(-10).map((item) => <li key={`${item.state_version}-${item.time}`}><time>{time(item.time)}</time><i/><span>{item.label}</span></li>) : <li className="empty-line">尚无可解释业务事件</li>}</ol></div></section><section className="detail-section"><div className="section-heading"><h3>智能体运行轨迹</h3><span>教师授权可见</span></div><div className="agent-trace">{detail.agent_trace.length ? detail.agent_trace.map((item, index) => <div key={`${item.kind}-${index}`}><span>{index + 1}</span><p><b>{item.label}</b><small>{item.detail}</small></p></div>) : <p className="muted">还没有可转换的真实运行事件。</p>}</div></section></div>}
      </div>
      <section className={`teacher-actions action-dock ${actionsOpen ? "expanded" : "collapsed"}`}>
        <button type="button" className="action-toggle" aria-expanded={actionsOpen} onClick={() => setActionsOpen((value) => !value)}><span><b>介入操作</b><small>{active ? "待教师处理" : "当前无需操作"}</small></span><span>{actionsOpen ? "收起" : "展开"}</span></button>
        <div className="action-body">
          {active && interventionBasis && <section className="action-basis" aria-label="本次介入依据"><div><span>本次介入依据</span><b>{interventionBasis.name}</b></div><p><strong>{requirementStatus(interventionBasis.status)}</strong><span>{interventionBasis.snapshot_label || "尚未运行"}</span><small>{interventionBasis.evaluator}</small></p></section>}
          <section className="action-explanation"><div className="action-section-heading"><div><h3>教师介入说明</h3><p>{active ? "依据当前证据选择最小必要干预。" : "当前没有待处理的介入事项。"}</p></div>{active && <span className="status-badge waiting">待处理</span>}</div><label htmlFor="teacher-prompt">补充教学提示（可选）</label><textarea id="teacher-prompt" rows={2} value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="给出检查方向，不直接提供完整答案。" disabled={!active}/>{actionError && <div className="action-error" role="alert">{actionError}</div>}</section>
          <section className="action-group strategy-group"><div className="action-section-heading"><div><h3>指导策略</h3><p>控制本轮帮助边界</p></div></div><div className="action-grid strategy"><button onClick={() => takeAction("continue")} disabled={!active || !!actionLoading}>{actionLoading === "continue" ? "处理中…" : "保持当前指导"}</button><button onClick={() => takeAction("allow_l2")} disabled={!active || !!actionLoading}>{actionLoading === "allow_l2" ? "处理中…" : "允许局部示例"}</button></div></section>
          <section className="action-group flow-control"><div className="action-section-heading"><div><h3>流程控制</h3><p>暂停或恢复教学流程</p></div></div><div className="action-grid"><button onClick={() => takeAction("pause_ai")} disabled={!active || !!actionLoading}>{actionLoading === "pause_ai" ? "处理中…" : "暂停智能指导"}</button><button className="primary" onClick={() => takeAction("resume")} disabled={!active || !!actionLoading}>{actionLoading === "resume" ? "正在恢复…" : "确认并恢复流程"}</button></div></section>
          {detail.latest_submission && <section className="action-group"><div className="action-section-heading"><div><h3>作品评价</h3><p>{detail.latest_submission.snapshot_label || "已提交"} · {detail.latest_submission.formal_grade ? "成绩已发布" : "待复核"}</p></div></div><button className="primary" onClick={() => onReview(detail.latest_submission!.id)}>打开评价页</button></section>}
        </div>
      </section>
    </div>
  </>;
}

function EmptyGroup({ group }: { group: GroupKey }) { return <div className="empty-group"><strong>{group === "attention" ? "当前没有等待教师的学生" : `当前没有${groupNames[group]}的学生`}</strong><p>列表只显示来自业务数据库的真实课堂状态。</p></div>; }
function EmptyDetail() { return <div className="empty-detail"><strong>选择一名学生</strong><p>查看验收项、证据、学生观察和教学过程。</p></div>; }
function DetailLoading() { return <div className="detail-loading"><i/><span>正在读取教学事实与证据…</span></div>; }
function Loading() { return <div className="page-loading"><div className="loading-brand"><span className="brand-mark">AI</span><strong>教师课堂台</strong></div><div className="skeleton"><i/><i/></div><p>正在从业务数据库读取课堂状态…</p></div>; }
function Setup() { return <main className="setup-state"><span className="brand-mark">AI</span><h1>打开教师课堂台</h1><p>请从课程入口进入，当前页面没有收到教师身份上下文。</p></main>; }
function Fatal({ message, retry }: { message: string; retry: () => void }) { return <main className="setup-state"><span className="error-mark">!</span><h1>课堂台暂时无法载入</h1><p>{message}</p><button className="primary-retry" onClick={retry}>重新连接</button></main>; }


type HealthStatus = "normal" | "degraded" | "unavailable";
type DemoHealth = { business_database: HealthStatus; execution_environment: HealthStatus; teaching_flow: HealthStatus; model_service: HealthStatus };
const healthLabels: Record<keyof DemoHealth, string> = { business_database: "业务数据库", execution_environment: "执行环境", teaching_flow: "教学流程", model_service: "模型服务" };
const healthStatusLabels: Record<HealthStatus, string> = { normal: "正常", degraded: "降级", unavailable: "不可用" };

function DemoHealthView({ userId }: { userId: string }) {
  const [health, setHealth] = useState<DemoHealth | null>(null);
  const [error, setError] = useState("");
  const loadHealth = useCallback(async () => {
    setError("");
    try { setHealth(await api<DemoHealth>("/api/product/teacher/demo-health", userId)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "健康状态读取失败"); }
  }, [userId]);
  useEffect(() => { void loadHealth(); }, [loadHealth]);
  return <div className="teacher-app">
    <header className="topbar"><div className="brand"><span className="brand-mark">AI</span><strong>演示健康</strong></div><div className="top-actions"><a className="health-link" href={`?user=${encodeURIComponent(userId)}`}>返回课堂台</a></div></header>
    <main className="demo-health-page"><header><span>DEMO PREFLIGHT</span><h1>演示环境健康状态</h1><p>仅呈现可用性，不显示密钥、路径、容器或数据库凭据。</p></header>
      {error ? <section className="health-error"><strong>状态不可用</strong><p>{error}</p><button className="refresh" onClick={() => void loadHealth()}>重新检查</button></section> : !health ? <section className="health-loading">正在检查四个服务域…</section> : <section className="health-grid">{(Object.keys(healthLabels) as Array<keyof DemoHealth>).map((key) => <article key={key}><div><span className={`health-dot ${health[key]}`} aria-hidden="true"/><h2>{healthLabels[key]}</h2></div><b className={`health-state ${health[key]}`}>{healthStatusLabels[health[key]]}</b></article>)}</section>}
      <footer><strong>进入演示前仍须运行 CLI Preflight。</strong><span>存在 FAIL 时禁止进入演示状态。</span></footer>
    </main>
  </div>;
}
