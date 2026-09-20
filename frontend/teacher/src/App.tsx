import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api, newOperation } from "./api";
import type { Classroom, ClassroomItem, Detail } from "./types";

type GroupKey = "attention" | "progress" | "completed";
const groupNames: Record<GroupKey, string> = { attention: "需要关注", progress: "进行中", completed: "已完成" };
const reasonNames: Record<string, string> = { failure_threshold_reached: "连续检查未通过", l2_authorization_required: "需要授权局部示例", normal: "正常学习中" };
const helpNames: Record<string, string> = { L0: "引导", L1: "定位", L2: "局部示例", NONE: "尚未指导" };
const requirementStatus = (value: string) => ({ SATISFIED: "已满足", NOT_SATISFIED: "未满足", INFRASTRUCTURE_ERROR: "环境异常", NOT_RUN: "未运行" }[value] || value);
const time = (value: string) => new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit" }).format(new Date(value));
const waitText = (seconds: number) => seconds >= 3600 ? `${Math.floor(seconds / 3600)} 小时` : seconds >= 60 ? `${Math.floor(seconds / 60)} 分钟` : "刚刚";

export function App() {
  const params = useMemo(() => new URLSearchParams(location.search), []);
  const userId = params.get("user") || params.get("user_id") || "";
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
  const [connection, setConnection] = useState<"connected" | "reconnecting" | "offline">("reconnecting");
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const selectedRef = useRef<string | null>(null);
  const selectionInitialized = useRef(false);

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
    if (!detailOpen) return;
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") setDetailOpen(false); };
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

  function chooseStudent(attemptId: string) {
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
  if (loading) return <Loading />;
  if (error || !classroom) return <Fatal message={error || "课堂工作台不可用"} retry={() => { setLoading(true); void loadClassroom(); }} />;
  const currentList = classroom.groups[group];
  const groupTitle = group === "attention" ? "现在谁需要教师帮助" : group === "progress" ? "跟进正在学习的学生" : "查看已完成的学习记录";

  return <div className="teacher-app">
    <header className="topbar">
      <div className="brand"><span className="brand-mark">AI</span><strong>教师课堂台</strong></div>
      <div className="course-context"><b>AI 应用开发实训</b><span>FAQ-001-v1 · 当前课堂</span></div>
      <div className="top-actions"><span className={`connection ${connection}`}><i />{connection === "connected" ? "自动更新" : connection === "offline" ? "网络断开" : "正在重连"}{lastUpdated && <small>· {time(lastUpdated.toISOString())}</small>}</span><span className="avatar" aria-label={`当前教师：${classroom.teacher.display_name}`}>{classroom.teacher.display_name.slice(0, 1)}</span></div>
    </header>
    <main className="teacher-layout">
      <section className="classroom-list" aria-label="课堂学生列表">
        <header className="list-head"><div className="list-head-copy"><span className="eyebrow">课堂工作台</span><h1>{groupTitle}</h1><p>依据真实教学事件，每 5 秒自动同步。</p></div><div className="classroom-overview" aria-label="课堂状态概览"><span className={classroom.groups.attention.length ? "needs-attention" : ""}><b>{classroom.groups.attention.length}</b><small>待介入</small></span><span><b>{classroom.groups.progress.length}</b><small>学习中</small></span><span><b>{classroom.groups.completed.length}</b><small>已完成</small></span></div><button className="refresh" onClick={() => void loadClassroom()}>刷新数据</button></header>
        <nav className="group-tabs" aria-label="学生状态分组">{(Object.keys(groupNames) as GroupKey[]).map((key) => <button key={key} aria-current={group === key} onClick={() => chooseGroup(key)}><span>{groupNames[key]}</span><b>{classroom.groups[key].length}</b></button>)}</nav>
        <div className="list-columns"><span>学生 / 当前阶段</span><span>原因</span><span>失败</span><span>已有帮助</span><span>等待</span></div>
        <div className="student-list">{currentList.length ? currentList.map((item) => <StudentRow key={item.attempt_id} item={item} selected={selected === item.attempt_id} onSelect={() => chooseStudent(item.attempt_id)} />) : <EmptyGroup group={group} />}</div>
      </section>
      {detailOpen && <button className="detail-backdrop" aria-label="返回学生列表" onClick={() => setDetailOpen(false)} />}
      <aside className={`intervention-panel ${detailOpen ? "open" : ""}`} aria-label="教师介入详情">
        {!selected ? <EmptyDetail /> : detailLoading || !detail ? <DetailLoading /> : <InterventionDetail key={detail.attempt.id} detail={detail} prompt={prompt} setPrompt={setPrompt} actionLoading={actionLoading} actionError={actionError} takeAction={takeAction} showTrace={showTrace} setShowTrace={setShowTrace} onClose={() => setDetailOpen(false)} />}
      </aside>
    </main>
  </div>;
}

function StudentRow({ item, selected, onSelect }: { item: ClassroomItem; selected: boolean; onSelect: () => void }) {
  const attention = item.category === "attention";
  return <button className={`student-row ${selected ? "selected" : ""}`} onClick={onSelect} aria-pressed={selected}>
    <span className="student-cell"><i className={attention ? "attention-dot" : "status-dot"}/><span><b>{item.student}</b><small>{item.stage}</small></span></span>
    <span><b className="reason">{reasonNames[item.reason] || item.reason}</b>{item.intervention_status === "RESUME_FAILED" && <small className="danger">恢复失败，可重试</small>}</span>
    <span className={item.failure_count >= 3 ? "failure danger" : "failure"}>{item.failure_count} 次</span>
    <span>{helpNames[item.help_level] || item.help_level}</span>
    <span>{attention ? waitText(item.wait_seconds) : "—"}</span>
  </button>;
}

function InterventionDetail({ detail, prompt, setPrompt, actionLoading, actionError, takeAction, showTrace, setShowTrace, onClose }: { detail: Detail; prompt: string; setPrompt: (value: string) => void; actionLoading: string; actionError: string; takeAction: (action: "continue" | "allow_l2" | "pause_ai" | "resume") => void; showTrace: boolean; setShowTrace: (value: boolean) => void; onClose: () => void }) {
  const active = detail.interventions.find((item) => ["WAITING_TEACHER", "RESUME_FAILED"].includes(item.status));
  const currentRequirement = detail.requirements.find((item) => item.status === "NOT_SATISFIED") || detail.requirements[0];
  return <>
    <header className="detail-head"><button className="detail-back" onClick={onClose} aria-label="返回学生列表">返回</button><div><span className="eyebrow">学生学习详情</span><h2>{detail.identity.display_name}</h2><p>{detail.stage.title} · {detail.requirement_summary.satisfied_count}/{detail.requirement_summary.required_count} 项满足</p></div><span className={`intervention-status ${active ? "waiting" : "resolved"}`}>{active ? active.status === "RESUME_FAILED" ? "恢复失败" : "等待处理" : "当前无需介入"}</span></header>
    <div className="detail-content">
      <div className="detail-scroll">
        <section className="detail-section"><h3>当前验收项</h3>{currentRequirement ? <div className="requirement-focus"><div><b>{currentRequirement.name}</b><span>{currentRequirement.snapshot_label || "尚未运行"}</span></div><strong className={currentRequirement.status === "SATISFIED" ? "success" : "danger"}>{requirementStatus(currentRequirement.status)}</strong><dl><div><dt>检查器</dt><dd>{currentRequirement.evaluator}</dd></div><div><dt>证据</dt><dd>{currentRequirement.evidence_refs.length ? `${currentRequirement.evidence_refs.length} 条可追溯证据` : "尚无证据"}</dd></div></dl></div> : <p className="muted">当前阶段没有验收项。</p>}</section>
        <section className="detail-section"><h3>学生观察</h3><blockquote>{detail.student_observation || "学生尚未提交有效观察。"}</blockquote></section>
        <details className="detail-disclosure" open><summary><span>最近指导</span><small>{detail.guidance_history.length} 条记录</small></summary><div className="disclosure-body guidance-history">{detail.guidance_history.length ? detail.guidance_history.slice(-3).reverse().map((item, index) => <article key={`${item.time}-${index}`}><header><b>{helpNames[item.level || ""] || "引导"}</b><time>{time(item.time)}</time></header><p>{item.message}</p>{item.success === false && <small>已使用安全模板：{item.fallback_reason}</small>}</article>) : <p className="muted">尚无智能指导记录。</p>}</div></details>
        <details className="detail-disclosure"><summary><span>版本变化</span><small>{detail.snapshot_diff.files_changed} 个文件</small></summary><div className="disclosure-body"><div className="diff-summary"><div><span>{detail.snapshot_diff.from || "—"}</span><b>→</b><span>{detail.snapshot_diff.to || "—"}</span></div><p>{detail.snapshot_diff.files_changed} 个文件变化 · <strong>+{detail.snapshot_diff.additions}</strong> / <em>−{detail.snapshot_diff.deletions}</em></p>{detail.snapshot_diff.files?.length ? <small>{detail.snapshot_diff.files.join("、")}</small> : null}</div></div></details>
        <details className="detail-disclosure"><summary><span>教学过程</span><small>{detail.timeline.length} 个事件</small></summary><div className="disclosure-body teaching-timeline"><ol>{detail.timeline.length ? detail.timeline.slice(-10).map((item) => <li key={`${item.state_version}-${item.time}`}><time>{time(item.time)}</time><i/><span>{item.label}</span></li>) : <li className="empty-line">尚无可解释业务事件</li>}</ol></div></details>
        <details className="detail-disclosure" open={showTrace} onToggle={(event) => setShowTrace(event.currentTarget.open)}><summary><span>智能体运行轨迹</span><small>教师授权可见</small></summary><div className="disclosure-body agent-trace">{detail.agent_trace.length ? detail.agent_trace.map((item, index) => <div key={`${item.kind}-${index}`}><span>{index + 1}</span><p><b>{item.label}</b><small>{item.detail}</small></p></div>) : <p className="muted">还没有可转换的真实运行事件。</p>}</div></details>
      </div>
      <section className="teacher-actions action-dock"><div className="action-heading"><div><h3>教师操作</h3><p>{active ? "根据证据选择最小必要干预。" : "当前学生没有待处理介入事项。"}</p></div>{active && <span>待处理</span>}</div><label htmlFor="teacher-prompt">补充教学提示（可选）</label><textarea id="teacher-prompt" rows={2} value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="给出检查方向，不直接提供完整答案。" disabled={!active}/>{actionError && <div className="action-error" role="alert">{actionError}</div>}<div className="action-grid"><button onClick={() => takeAction("continue")} disabled={!active || !!actionLoading}>{actionLoading === "continue" ? "处理中…" : "保持当前指导"}</button><button onClick={() => takeAction("allow_l2")} disabled={!active || !!actionLoading}>{actionLoading === "allow_l2" ? "处理中…" : "允许局部示例"}</button><button onClick={() => takeAction("pause_ai")} disabled={!active || !!actionLoading}>{actionLoading === "pause_ai" ? "处理中…" : "暂停智能指导"}</button><button className="primary" onClick={() => takeAction("resume")} disabled={!active || !!actionLoading}>{actionLoading === "resume" ? "正在恢复…" : "确认并恢复流程"}</button></div></section>
    </div>
  </>;
}

function EmptyGroup({ group }: { group: GroupKey }) { return <div className="empty-group"><strong>{group === "attention" ? "当前没有等待教师的学生" : `当前没有${groupNames[group]}的学生`}</strong><p>列表只显示来自业务数据库的真实课堂状态。</p></div>; }
function EmptyDetail() { return <div className="empty-detail"><strong>选择一名学生</strong><p>查看验收项、证据、学生观察和教学过程。</p></div>; }
function DetailLoading() { return <div className="detail-loading"><i/><span>正在读取教学事实与证据…</span></div>; }
function Loading() { return <div className="page-loading"><div className="loading-brand"><span className="brand-mark">AI</span><strong>教师课堂台</strong></div><div className="skeleton"><i/><i/></div><p>正在从业务数据库读取课堂状态…</p></div>; }
function Setup() { return <main className="setup-state"><span className="brand-mark">AI</span><h1>打开教师课堂台</h1><p>请从课程入口进入，当前页面没有收到教师身份上下文。</p></main>; }
function Fatal({ message, retry }: { message: string; retry: () => void }) { return <main className="setup-state"><span className="error-mark">!</span><h1>课堂台暂时无法载入</h1><p>{message}</p><button className="primary-retry" onClick={retry}>重新连接</button></main>; }
