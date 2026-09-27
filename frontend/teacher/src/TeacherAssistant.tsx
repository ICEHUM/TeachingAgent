import { Fragment, useEffect, useId, useMemo, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import { api, apiStream } from "./api";

type Course = { id: string; name: string; code: string };
type AssistantContext = { courses: Course[]; text_available: boolean; limitations: string[] };
type Student = { id: string; name: string; attempt_id: string };
type AssistantSource = { student_count: number; attempt_count: number; sampled_at: string; description: string };
type ChartBase = { title: string; description?: string };
type SankeyNode = { id: string; label: string; column: number };
type SankeyLink = { source: string; target: string; value: number; students: Student[] };
type SankeyData = ChartBase & { kind: "sankey"; nodes: SankeyNode[]; links: SankeyLink[] };
type BarData = ChartBase & { kind: "bar"; categories: string[]; values: number[]; students: Student[][] };
type LineData = ChartBase & { kind: "line"; categories: string[]; series: Array<{ label: string; values: number[] }>; students: Student[][] };
type TableData = ChartBase & { kind: "table"; columns: string[]; rows: Array<Array<string | number>>; students: Student[][] };
type HeatmapData = ChartBase & { kind: "heatmap"; xCategories: string[]; yCategories: string[]; cells: Array<{ x: number; y: number; value: number; students: Student[] }> };
type ChartData = SankeyData | BarData | LineData | TableData | HeatmapData | (ChartBase & { kind: string; columns?: string[]; rows?: Array<Array<string | number>>; students?: Student[][] });
type AssistantMessage = { role: "user" | "assistant"; content: string; generated_at?: string; source?: AssistantSource; chart?: ChartData | null; model_used?: boolean };
type StreamEvent = { text?: string; chart?: ChartData; source?: AssistantSource; answer?: string; model_used?: boolean; message?: string; course?: Course; generated_at?: string };
type ConversationStore = { courseId: string; histories: Record<string, AssistantMessage[]> };
type Selection = { key: string; label: string; value: string; students: Student[] };
type LiveReply = { content: string; chart: ChartData | null; source: AssistantSource | null; modelUsed: boolean };

const MAX_MESSAGE = 2000;
const MAX_HISTORY = 12;
const STREAM_PATH = "/api/product/teacher/assistant/messages/stream";

function loadConversation(userId: string): ConversationStore {
  try {
    const stored: unknown = JSON.parse(sessionStorage.getItem(`teaching-assistant:${userId}`) || "null");
    if (!stored || typeof stored !== "object") return { courseId: "", histories: {} };
    const value = stored as ConversationStore;
    if (typeof value.courseId !== "string" || !value.histories || typeof value.histories !== "object") return { courseId: "", histories: {} };
    const histories = Object.fromEntries(Object.entries(value.histories).slice(-8).map(([key, history]) => [key, Array.isArray(history) ? history.filter((message) => ["user", "assistant"].includes(message.role) && typeof message.content === "string").slice(-20) : []]));
    return { courseId: value.courseId, histories };
  } catch { return { courseId: "", histories: {} }; }
}

export function TeacherAssistant({ userId, onInspect }: { userId: string; onInspect: (attemptId: string) => void }) {
  const stored = useMemo(() => loadConversation(userId), [userId]);
  const [context, setContext] = useState<AssistantContext | null>(null);
  const [courseId, setCourseId] = useState(stored.courseId);
  const [histories, setHistories] = useState(stored.histories);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState("");
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [contextError, setContextError] = useState("");
  const [sendError, setSendError] = useState("");
  const [live, setLive] = useState<LiveReply | null>(null);
  const [reload, setReload] = useState(0);
  const [storageNotice, setStorageNotice] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const requestRef = useRef<AbortController | null>(null);
  const requestSequence = useRef(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setContextError("");
    api<AssistantContext>("/api/product/teacher/assistant/context", userId, { signal: controller.signal })
      .then((value) => {
        setContext(value);
        setCourseId((current) => value.courses.some((course) => course.id === current) ? current : value.courses[0]?.id || "");
      })
      .catch((cause) => { if (!controller.signal.aborted) setContextError(cause instanceof Error ? cause.message : "对话服务暂时不可用"); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [userId, reload]);

  useEffect(() => {
    try { sessionStorage.setItem(`teaching-assistant:${userId}`, JSON.stringify({ courseId, histories })); setStorageNotice(false); }
    catch { setStorageNotice(true); }
  }, [userId, courseId, histories]);
  useEffect(() => () => { requestSequence.current += 1; requestRef.current?.abort(); }, []);
  useEffect(() => { if (live || pending || histories[courseId]?.length) endRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }); }, [live, pending, histories, courseId]);

  const course = context?.courses.find((item) => item.id === courseId) || null;
  const messages = histories[courseId] || [];
  const canSend = !!course && !!draft.trim() && draft.length <= MAX_MESSAGE && !sending;

  function newConversation() {
    if (!course || sending) return;
    setHistories((current) => ({ ...current, [course.id]: [] }));
    setDraft(""); setPending(""); setLive(null); setSendError("");
    requestAnimationFrame(() => inputRef.current?.focus());
  }
  function stopWaiting() {
    requestSequence.current += 1; requestRef.current?.abort(); requestRef.current = null;
    setLive(null); setPending(""); setSending(false); setSendError("已停止等待。已生成的部分不会保留，输入已保留。");
  }
  async function sendMessage() {
    const message = draft.trim();
    if (!course || !message || message.length > MAX_MESSAGE || sending) return;
    const selectedCourse = course.id;
    const previous = histories[selectedCourse] || [];
    const sequence = ++requestSequence.current;
    const controller = new AbortController(); requestRef.current = controller;
    let answer = "";
    let chart: ChartData | null = null;
    let source: AssistantSource | null = null;
    let modelUsed = true;
    let streamError = "";
    let completed = false;
    const commit = () => {
      const reply: AssistantMessage = { role: "assistant", content: answer.trim(), generated_at: new Date().toISOString(), source: source || undefined, chart, model_used: modelUsed };
      const entries: AssistantMessage[] = [...previous, { role: "user", content: message }, reply];
      setHistories((current) => ({ ...current, [selectedCourse]: entries.slice(-20) }));
      setDraft(""); setPending(""); setLive(null);
    };
    setSending(true); setPending(message); setSendError(""); setLive({ content: "", chart: null, source: null, modelUsed: true });
    try {
      await apiStream<StreamEvent>(STREAM_PATH, userId, {
        method: "POST", signal: controller.signal,
        body: JSON.stringify({ course_id: selectedCourse, message, history: previous.slice(-MAX_HISTORY).map(({ role, content }) => ({ role, content: content.slice(0, 4000) })) }),
      }, (event, data) => {
        if (requestSequence.current !== sequence) return;
        if (event === "meta") {
          source = data.source || null;
          setLive((current) => current && { ...current, source: source });
        } else if (event === "chart" && data.chart) {
          chart = data.chart;
          setLive((current) => current && { ...current, chart });
        } else if (event === "delta" && data.text) {
          answer += data.text;
          setLive((current) => current && { ...current, content: answer });
        } else if (event === "error") {
          streamError = data.message || "回答中断，可以重新提问。";
        } else if (event === "done") {
          modelUsed = data.model_used !== false;
          completed = true;
        }
      });
      if (requestSequence.current !== sequence) return;
      if (streamError) throw new Error(streamError);
      if (!completed || !answer.trim()) throw new Error("回答未完成，请重试。");
      commit();
    } catch (cause) {
      if (requestSequence.current !== sequence || controller.signal.aborted) return;
      const reason = cause instanceof Error ? cause.message : "分析失败";
      setSendError(`${reason} 输入已保留，可以重试。`); setPending(""); setLive(null);
    } finally {
      if (requestSequence.current === sequence) { setSending(false); requestRef.current = null; }
    }
  }

  if (loading) return <main className="assistant-shell"><div className="assistant-loading" role="status">正在读取教师对话能力…</div></main>;
  if (contextError) return <main className="assistant-shell"><section className="assistant-error" role="alert"><h1>教学对话暂时不可用</h1><p>{contextError}</p><button className="refresh" onClick={() => setReload((value) => value + 1)}>重新连接</button></section></main>;
  if (!context?.courses.length) return <main className="assistant-shell"><section className="assistant-empty"><h1>还没有可分析的课堂</h1><p>教师加入课程后，就可以围绕课程中的学习记录进行分析。</p></section></main>;

  return <main className="assistant-shell" aria-labelledby="assistant-title">
    <header className="assistant-head">
      <div><h1 id="assistant-title">教学对话</h1><p>教学、技术或班级学情都可以聊；需要数据时我会给出真实统计和图表。</p></div>
      <div className="assistant-controls"><label htmlFor="assistant-course">分析课程</label><select id="assistant-course" value={courseId} disabled={sending} onChange={(event) => { setCourseId(event.target.value); setDraft(""); setSendError(""); }}>{context.courses.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.code}</option>)}</select><button className="refresh" onClick={newConversation} disabled={sending || (!messages.length && !draft)}>新建对话</button></div>
    </header>
    <div className="assistant-scope"><span>班级数据来自真实课堂记录</span><details><summary>数据范围</summary><ul>{context.limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}</ul><p>可以自由交流任何话题；涉及班级数据时以课程记录为准。对话保留在当前浏览器会话中。</p></details></div>
    {!context.text_available && <p className="assistant-model-notice" role="status">语言模型尚未配置，教学对话暂不可用。</p>}
    {storageNotice && <p className="assistant-model-notice" role="status">浏览器暂时无法保存对话，离开此页后可能丢失当前记录。</p>}
    <div className={`assistant-conversation ${!messages.length && !pending ? "is-empty" : ""}`} aria-busy={sending}>
      {!messages.length && !pending && <div className="assistant-welcome"><h2>想聊点什么？</h2><p>直接输入你的问题。需要分析课堂时，助手会读取当前课程记录；需要可视化时，说明你想比较什么。</p></div>}
      {messages.map((message, index) => <Message key={`${message.generated_at || index}-${index}`} message={message} onInspect={onInspect} />)}
      {pending && <article className="assistant-message user-message"><div className="message-role">你</div><p>{pending}</p></article>}
      {pending && (live ? <Message message={{ role: "assistant", content: live.content, source: live.source || undefined, chart: live.chart, model_used: live.modelUsed }} streaming onInspect={onInspect} /> : <div className="assistant-message assistant-message-pending" role="status"><div className="message-role">教学助手 <span className="assistant-activity" aria-hidden="true"><i /><i /><i /></span></div><p>正在思考…</p></div>)}
      <div ref={endRef} />
    </div>
    {sendError && <div className="assistant-send-error" role="alert"><span>{sendError}</span><button className="quiet" onClick={() => void sendMessage()} disabled={!canSend}>重试</button></div>}
    <form className="assistant-composer" onSubmit={(event) => { event.preventDefault(); void sendMessage(); }}>
      <label htmlFor="assistant-input">向教学助手提问</label>
      <textarea ref={inputRef} id="assistant-input" value={draft} maxLength={MAX_MESSAGE} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229) { event.preventDefault(); void sendMessage(); } }} placeholder="输入教学问题，或描述你想分析的课堂数据与图表。" rows={3} disabled={sending || !context.text_available} />
      <div className="composer-footer"><span>{sending ? "正在生成回答，可以随时停止。" : `Enter 发送 · Shift + Enter 换行${draft.length > 1600 ? ` · ${draft.length}/${MAX_MESSAGE}` : ""}`}</span>{sending ? <button type="button" className="refresh" onClick={stopWaiting}>停止生成</button> : <button className="primary" type="submit" disabled={!canSend || !context.text_available}>发送</button>}</div>
    </form>
  </main>;
}

function Message({ message, streaming, onInspect }: { message: AssistantMessage; streaming?: boolean; onInspect: (attemptId: string) => void }) {
  if (message.role === "user") return <article className="assistant-message user-message"><div className="message-role">你</div><p>{message.content}</p></article>;
  return <article className={`assistant-message ${streaming ? "assistant-message-streaming" : ""}`}><div className="message-role">教学助手 {message.model_used === false && <small>课堂数据摘要</small>}</div>{message.content ? <div className="assistant-answer"><AnswerText text={message.content} chartPresent={!!message.chart} /></div> : <p className="assistant-waiting">正在组织回答…</p>}{message.chart && <DataVisualization chart={message.chart} onInspect={onInspect} />}{message.source && <details className="assistant-source"><summary>本轮读取的课堂数据 · {message.source.student_count} 名学生 · {message.source.attempt_count} 个学习记录</summary><p>{message.source.description}</p><time dateTime={message.source.sampled_at}>采集于 {new Date(message.source.sampled_at).toLocaleString("zh-CN")}</time></details>}</article>;
}

type AnswerBlock =
  | { kind: "paragraph" | "heading" | "bullets" | "numbers"; lines: string[] }
  | { kind: "code"; lines: string[]; language: string }
  | { kind: "table"; rows: string[][] };

const TABLE_SEPARATOR = /^\|?[\s:|-]*-[\s:|-]*\|?$/;

function splitTableRow(line: string): string[] {
  const cells = line.replace(/^\|/, "").replace(/\|$/, "").split("|");
  return cells.map((cell) => cell.trim());
}

/** Lightweight Markdown: headings, lists, bold, inline code, fenced code and tables. */
function parseAnswer(text: string): AnswerBlock[] {
  const lines = text.split("\n");
  const blocks: AnswerBlock[] = [];
  let code: { lines: string[]; language: string } | null = null;
  let index = 0;
  while (index < lines.length) {
    const raw = lines[index];
    const line = raw.trim();
    if (code) {
      if (line.startsWith("```")) { blocks.push({ kind: "code", lines: code.lines, language: code.language }); code = null; }
      else code.lines.push(raw.replace(/\s+$/, ""));
      index += 1; continue;
    }
    if (line.startsWith("```")) { code = { lines: [], language: line.slice(3).trim() }; index += 1; continue; }
    if (!line) { index += 1; continue; }
    if (line.startsWith("|") && TABLE_SEPARATOR.test((lines[index + 1] || "").trim())) {
      const rows: string[][] = [splitTableRow(line)];
      index += 2;
      while (index < lines.length && lines[index].trim().startsWith("|")) { rows.push(splitTableRow(lines[index].trim())); index += 1; }
      blocks.push({ kind: "table", rows });
      continue;
    }
    const last = blocks.at(-1);
    const heading = /^#{1,6}\s+(.*)$/.exec(line);
    const bullet = /^[-*•]\s+(.*)$/.exec(line);
    const numbered = /^\d+[.)、]\s+(.*)$/.exec(line);
    if (heading) blocks.push({ kind: "heading", lines: [heading[1]] });
    else if (bullet) { if (last?.kind === "bullets") last.lines.push(bullet[1]); else blocks.push({ kind: "bullets", lines: [bullet[1]] }); }
    else if (numbered) { if (last?.kind === "numbers") last.lines.push(numbered[1]); else blocks.push({ kind: "numbers", lines: [numbered[1]] }); }
    else if (last?.kind === "paragraph") last.lines.push(line);
    else blocks.push({ kind: "paragraph", lines: [line] });
    index += 1;
  }
  if (code) blocks.push({ kind: "code", lines: code.lines, language: code.language });
  return blocks;
}

function renderInline(text: string) {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).filter(Boolean).map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={index}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`")) return <code key={index}>{part.slice(1, -1)}</code>;
    return <Fragment key={index}>{part}</Fragment>;
  });
}

function isRenderedChartConfig(block: AnswerBlock) {
  if (block.kind !== "code") return false;
  const lines = block.lines.map((line) => line.trim()).filter(Boolean);
  return lines.length >= 2
    && lines.some((line) => /^kind\s*:\s*(?:bar|line|sankey|heatmap|table)\s*$/i.test(line))
    && lines.every((line) => /^(?:kind|group_by|split_by|title|task_key|date_granularity)\s*:/i.test(line));
}

function AnswerText({ text, chartPresent = false }: { text: string; chartPresent?: boolean }) {
  return <>{parseAnswer(text).filter((block) => !chartPresent || !isRenderedChartConfig(block)).map((block, index) => {
    if (block.kind === "heading") return <h4 key={index}>{renderInline(block.lines.join(" "))}</h4>;
    if (block.kind === "bullets") return <ul key={index}>{block.lines.map((line, lineIndex) => <li key={lineIndex}>{renderInline(line)}</li>)}</ul>;
    if (block.kind === "numbers") return <ol key={index}>{block.lines.map((line, lineIndex) => <li key={lineIndex}>{renderInline(line)}</li>)}</ol>;
    if (block.kind === "code") return <pre key={index} className="assistant-code"><code>{block.lines.join("\n")}</code></pre>;
    if (block.kind === "table") return <div className="assistant-table-wrap" key={index}><table><thead><tr>{block.rows[0].map((cell, cellIndex) => <th scope="col" key={cellIndex}>{renderInline(cell)}</th>)}</tr></thead><tbody>{block.rows.slice(1).map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex}>{renderInline(cell)}</td>)}</tr>)}</tbody></table></div>;
    return <p key={index}>{renderInline(block.lines.join("\n"))}</p>;
  })}</>;
}

function DataVisualization({ chart, onInspect }: { chart: ChartData; onInspect: (attemptId: string) => void }) {
  if (chart.kind === "sankey" && "nodes" in chart && "links" in chart) return <SankeyChart chart={chart as SankeyData} onInspect={onInspect} />;
  if (chart.kind === "bar" && "categories" in chart && "values" in chart) return <BarChart chart={chart as BarData} onInspect={onInspect} />;
  if (chart.kind === "line" && "categories" in chart && "series" in chart) return <LineChart chart={chart as LineData} onInspect={onInspect} />;
  if (chart.kind === "heatmap" && "xCategories" in chart && "cells" in chart) return <HeatmapChart chart={chart as HeatmapData} onInspect={onInspect} />;
  if (chart.kind === "table" && "columns" in chart && "rows" in chart) return <DataTable chart={chart as TableData} onInspect={onInspect} />;
  const fallback = chart as ChartBase & { kind: string; columns?: string[]; rows?: Array<Array<string | number>>; students?: Student[][] };
  return <section className="data-chart-block"><h3>{chart.title}</h3><p className="chart-note">这个图表类型暂不支持。{fallback.rows?.length ? "以下展示原始表格数据。" : "可以继续提问，改用柱状图、折线图、热力图、桑基图或表格。"}</p>{fallback.columns && fallback.rows && <DataTable chart={{ ...fallback, kind: "table", columns: fallback.columns, rows: fallback.rows, students: fallback.students || [] }} onInspect={onInspect} />}</section>;
}

function ChartHeader({ chart, headingId }: { chart: ChartBase; headingId: string }) {
  return <header className="sankey-heading"><div><h3 id={headingId}>{chart.title}</h3>{chart.description && <p>{chart.description}</p>}</div></header>;
}

function StudentSelection({ selection, onClear, onInspect }: { selection: Selection | null; onClear: () => void; onInspect: (attemptId: string) => void }) {
  if (!selection) return null;
  const students = uniqueStudents(selection.students);
  return <div className="chart-selection" aria-live="polite"><header><div><strong>{selection.label}</strong><span>{selection.value}</span></div><button type="button" className="quiet" onClick={onClear}>清除选择</button></header>{students.length ? <ul>{students.map((student) => <li key={`${student.id}-${student.attempt_id}`}><button type="button" className="student-inspect" disabled={!student.attempt_id} onClick={() => onInspect(student.attempt_id)}>{student.name}<small>{student.attempt_id ? "查看学习记录" : "尚无学习记录"}</small></button></li>)}</ul> : <p>这个分组没有可打开的学生记录。</p>}</div>;
}

function ChartTable({ title, columns, rows, selections, onSelect }: { title: string; columns: string[]; rows: Array<Array<string | number>>; selections: Selection[]; onSelect: (selection: Selection) => void }) {
  return <div className="assistant-table-wrap"><table><caption>{title}</caption><thead><tr>{columns.map((column, index) => <th scope="col" key={`${column}-${index}`}>{column}</th>)}<th scope="col">相关学生</th></tr></thead><tbody>{rows.map((row, index) => <tr key={index}>{columns.map((_, cellIndex) => <td key={cellIndex}>{row[cellIndex] ?? "—"}</td>)}<td><button type="button" className="quiet" onClick={() => onSelect(selections[index])}>查看名单</button></td></tr>)}</tbody></table></div>;
}

function SankeyChart({ chart, onInspect }: { chart: SankeyData; onInspect: (attemptId: string) => void }) {
  const headingId = useId(); const [selection, setSelection] = useState<Selection | null>(null); const [hover, setHover] = useState<Selection | null>(null);
  const links = chart.links.filter((link) => link.value > 0 && chart.nodes.some((node) => node.id === link.source) && chart.nodes.some((node) => node.id === link.target));
  const columns = [...new Set(chart.nodes.map((node) => node.column))].sort((a, b) => a - b);
  const width = Math.max(720, columns.length * 220); const padding = 36; const nodeWidth = 14; const gap = 46;
  const counts = new Map(chart.nodes.map((node) => [node.id, Math.max(links.filter((link) => link.source === node.id).reduce((sum, link) => sum + link.value, 0), links.filter((link) => link.target === node.id).reduce((sum, link) => sum + link.value, 0))]));
  const maxNodes = Math.max(1, ...columns.map((column) => chart.nodes.filter((node) => node.column === column).length));
  const height = Math.max(300, maxNodes * 78 + 32);
  const scale = Math.min(...columns.map((column) => { const nodes = chart.nodes.filter((node) => node.column === column); const total = nodes.reduce((sum, node) => sum + (counts.get(node.id) || 0), 0); return (height - padding * 2 - gap * Math.max(0, nodes.length - 1)) / Math.max(total, 1); }));
  const positions = new Map<string, { x: number; y: number; h: number }>();
  columns.forEach((column, columnIndex) => {
    const nodes = chart.nodes.filter((node) => node.column === column); const totalHeight = nodes.reduce((sum, node) => sum + (counts.get(node.id) || 0) * scale, 0) + gap * Math.max(0, nodes.length - 1); let y = (height - totalHeight) / 2;
    nodes.forEach((node) => { const h = Math.max(2, (counts.get(node.id) || 0) * scale); positions.set(node.id, { x: padding + (width - padding * 2 - nodeWidth) * columnIndex / Math.max(columns.length - 1, 1), y, h }); y += h + gap; });
  });
  const outgoing = new Map<string, number>(); const incoming = new Map<string, number>();
  const selections = links.map((link, index) => ({ key: `flow-${index}`, label: `${labelFor(chart.nodes, link.source)} → ${labelFor(chart.nodes, link.target)}`, value: `${link.value} 人`, students: link.students || [] }));
  const shapes = links.map((link, index) => {
    const source = positions.get(link.source)!; const target = positions.get(link.target)!; const thickness = link.value * scale;
    const sy = source.y + (outgoing.get(link.source) || 0); const ty = target.y + (incoming.get(link.target) || 0);
    outgoing.set(link.source, (outgoing.get(link.source) || 0) + thickness); incoming.set(link.target, (incoming.get(link.target) || 0) + thickness);
    const x1 = source.x + nodeWidth; const x2 = target.x; const middle = (x1 + x2) / 2;
    return { selection: selections[index], path: `M${x1},${sy} C${middle},${sy} ${middle},${ty} ${x2},${ty} L${x2},${ty + thickness} C${middle},${ty + thickness} ${middle},${sy + thickness} ${x1},${sy + thickness} Z` };
  });
  return <section className="data-chart-block" aria-labelledby={headingId}><ChartHeader chart={chart} headingId={headingId} /><p className="chart-interaction">选择连线或阶段，查看相关学生。连线宽度表示人数。</p>{!links.length ? <p className="chart-empty">当前没有可展示的分布记录。</p> : <><div className="sankey-scroll"><svg className="sankey-svg" viewBox={`0 0 ${width} ${height}`} role="group" aria-label={chart.title}><g>{shapes.map((shape) => <path key={shape.selection.key} className={`sankey-ribbon ${selection?.key === shape.selection.key ? "selected" : ""}`} d={shape.path} tabIndex={0} role="button" aria-label={`${shape.selection.label}，${shape.selection.value}，查看学生`} onMouseEnter={() => setHover(shape.selection)} onMouseLeave={() => setHover(null)} onFocus={() => setHover(shape.selection)} onBlur={() => setHover(null)} onClick={() => setSelection(shape.selection)} onKeyDown={(event) => activateKey(event, () => setSelection(shape.selection))}><title>{`${shape.selection.label} · ${shape.selection.value}`}</title></path>)}</g><g>{chart.nodes.map((node) => { const position = positions.get(node.id)!; const last = node.column === columns.at(-1); const related = uniqueStudents(links.filter((link) => link.source === node.id || link.target === node.id).flatMap((link) => link.students || [])); const nodeSelection = { key: node.id, label: node.label, value: `${counts.get(node.id) || 0} 人`, students: related }; return <g key={node.id} className={`sankey-node ${selection?.key === node.id ? "selected" : ""}`} tabIndex={0} role="button" aria-label={`${node.label}，${nodeSelection.value}，查看学生`} onMouseEnter={() => setHover(nodeSelection)} onMouseLeave={() => setHover(null)} onFocus={() => setHover(nodeSelection)} onBlur={() => setHover(null)} onClick={() => setSelection(nodeSelection)} onKeyDown={(event) => activateKey(event, () => setSelection(nodeSelection))}><rect x={position.x} y={position.y} width={nodeWidth} height={position.h} rx="2" /><text x={last ? position.x + nodeWidth : position.x} y={position.y - 8} textAnchor={last ? "end" : "start"}>{node.label} · {counts.get(node.id) || 0}</text></g>; })}</g></svg></div><div className="chart-hover" role="status">{hover ? `${hover.label} · ${hover.value}` : "悬停或用 Tab 聚焦图形，查看人数"}</div><details className="chart-table"><summary>查看表格数据</summary><ChartTable title={chart.title} columns={["起点", "终点", "人数"]} rows={links.map((link) => [labelFor(chart.nodes, link.source), labelFor(chart.nodes, link.target), link.value])} selections={selections} onSelect={setSelection} /></details></> }<StudentSelection selection={selection} onClear={() => setSelection(null)} onInspect={onInspect} /></section>;
}

function BarChart({ chart, onInspect }: { chart: BarData; onInspect: (attemptId: string) => void }) {
  const headingId = useId(); const [selection, setSelection] = useState<Selection | null>(null); const max = Math.max(...chart.values, 1);
  const selections = chart.categories.map((category, index) => ({ key: `bar-${index}`, label: category, value: String(chart.values[index] ?? 0), students: chart.students?.[index] || [] }));
  return <section className="data-chart-block" aria-labelledby={headingId}><ChartHeader chart={chart} headingId={headingId} /><p className="chart-interaction">选择一个分类，查看相关学生。</p><div className="interactive-bars">{chart.categories.map((category, index) => <button key={`${category}-${index}`} type="button" className={`interactive-bar ${selection?.key === selections[index].key ? "selected" : ""}`} onClick={() => setSelection(selections[index])} aria-label={`${category}，${chart.values[index] ?? 0}，查看学生`}><span className="bar-category">{category}</span><span className="bar-track"><i style={{ width: `${Math.max(0, (chart.values[index] || 0) / max) * 100}%` }} /></span><b>{chart.values[index] ?? 0}</b></button>)}</div><details className="chart-table"><summary>查看表格数据</summary><ChartTable title={chart.title} columns={["分类", "数值"]} rows={chart.categories.map((category, index) => [category, chart.values[index] ?? 0])} selections={selections} onSelect={setSelection} /></details><StudentSelection selection={selection} onClear={() => setSelection(null)} onInspect={onInspect} /></section>;
}

function LineChart({ chart, onInspect }: { chart: LineData; onInspect: (attemptId: string) => void }) {
  const headingId = useId(); const [selection, setSelection] = useState<Selection | null>(null); const [hover, setHover] = useState<Selection | null>(null);
  const width = Math.max(640, chart.categories.length * 64); const height = 280; const allValues = chart.series.flatMap((series) => series.values); const max = Math.max(...allValues, 1); const min = Math.min(...allValues, 0); const x = (index: number) => 48 + (width - 94) * index / Math.max(chart.categories.length - 1, 1); const y = (value: number) => 30 + (height - 94) * (1 - (value - min) / Math.max(max - min, 1));
  const selections = chart.categories.map((category, index) => ({ key: `point-${index}`, label: category, value: chart.series.map((series) => `${series.label} ${series.values[index] ?? 0}`).join(" · "), students: chart.students?.[index] || [] }));
  return <section className="data-chart-block" aria-labelledby={headingId}><ChartHeader chart={chart} headingId={headingId} /><p className="chart-interaction">选择数据点，查看该时间或分组的相关学生。</p><div className="chart-legend">{chart.series.map((series, index) => <span key={series.label}><i style={{ background: chartColor(index) }} />{series.label}</span>)}</div><div className="chart-scroll"><svg className="line-chart-svg" viewBox={`0 0 ${width} ${height}`} role="group" aria-label={chart.title}><line x1="48" y1={height - 64} x2={width - 46} y2={height - 64} /><line x1="48" y1="30" x2="48" y2={height - 64} /><text x="35" y="34" textAnchor="end">{max}</text><text x="35" y={height - 60} textAnchor="end">{min}</text>{chart.series.map((series, seriesIndex) => <g key={series.label}><polyline style={{ stroke: chartColor(seriesIndex) }} points={series.values.map((value, index) => `${x(index)},${y(value)}`).join(" ")} />{series.values.map((value, index) => <circle key={index} className={selection?.key === selections[index]?.key ? "selected" : ""} style={{ stroke: chartColor(seriesIndex) }} cx={x(index)} cy={y(value)} r="5" tabIndex={0} role="button" aria-label={`${series.label}，${chart.categories[index]}，${value}，查看学生`} onMouseEnter={() => setHover(selections[index])} onMouseLeave={() => setHover(null)} onFocus={() => setHover(selections[index])} onBlur={() => setHover(null)} onClick={() => setSelection(selections[index])} onKeyDown={(event) => activateKey(event, () => setSelection(selections[index]))}><title>{`${series.label} · ${chart.categories[index]}: ${value}`}</title></circle>)}</g>)}{chart.categories.map((category, index) => <text className="line-label" key={`${category}-${index}`} x={x(index)} y={height - 38}>{category}</text>)}</svg></div><div className="chart-hover" role="status">{hover ? `${hover.label} · ${hover.value}` : "悬停或用 Tab 聚焦数据点，查看数值"}</div><details className="chart-table"><summary>查看表格数据</summary><ChartTable title={chart.title} columns={["分类", ...chart.series.map((series) => series.label)]} rows={chart.categories.map((category, index) => [category, ...chart.series.map((series) => series.values[index] ?? 0)])} selections={selections} onSelect={setSelection} /></details><StudentSelection selection={selection} onClear={() => setSelection(null)} onInspect={onInspect} /></section>;
}

function HeatmapChart({ chart, onInspect }: { chart: HeatmapData; onInspect: (attemptId: string) => void }) {
  const headingId = useId(); const [selection, setSelection] = useState<Selection | null>(null); const max = Math.max(...chart.cells.map((cell) => cell.value), 1);
  return <section className="data-chart-block" aria-labelledby={headingId}><ChartHeader chart={chart} headingId={headingId} /><p className="chart-interaction">颜色越深，数值越高。选择单元格查看相关学生。</p><div className="assistant-table-wrap"><table className="interactive-heatmap"><caption>{chart.title}</caption><thead><tr><th scope="col">分组</th>{chart.xCategories.map((category, index) => <th scope="col" key={`${category}-${index}`}>{category}</th>)}</tr></thead><tbody>{chart.yCategories.map((category, rowIndex) => <tr key={`${category}-${rowIndex}`}><th scope="row">{category}</th>{chart.xCategories.map((column, columnIndex) => { const cell = chart.cells.find((item) => item.x === columnIndex && item.y === rowIndex); const value = cell?.value ?? 0; const item = { key: `heat-${columnIndex}-${rowIndex}`, label: `${category} · ${column}`, value: String(value), students: cell?.students || [] }; return <td key={columnIndex}><button type="button" className={selection?.key === item.key ? "selected" : ""} style={{ backgroundColor: `rgba(64, 99, 175, ${0.07 + value / max * 0.3})` }} onClick={() => setSelection(item)} aria-label={`${item.label}，${value}，查看学生`}>{value}</button></td>; })}</tr>)}</tbody></table></div><StudentSelection selection={selection} onClear={() => setSelection(null)} onInspect={onInspect} /></section>;
}

function DataTable({ chart, onInspect }: { chart: TableData; onInspect: (attemptId: string) => void }) {
  const headingId = useId(); const [selection, setSelection] = useState<Selection | null>(null);
  const selections = chart.rows.map((row, index) => ({ key: `row-${index}`, label: String(row[0] ?? `第 ${index + 1} 行`), value: row.slice(1).join(" · "), students: chart.students?.[index] || [] }));
  return <section className="data-chart-block" aria-labelledby={headingId}><ChartHeader chart={chart} headingId={headingId} /><ChartTable title={chart.title} columns={chart.columns} rows={chart.rows} selections={selections} onSelect={setSelection} /><StudentSelection selection={selection} onClear={() => setSelection(null)} onInspect={onInspect} /></section>;
}

function labelFor(nodes: SankeyNode[], id: string) { return nodes.find((node) => node.id === id)?.label || id; }
function uniqueStudents(students: Student[]) { return [...new Map(students.map((student) => [`${student.id}-${student.attempt_id}`, student])).values()]; }
function chartColor(index: number) { return ["#365fab", "#23745c", "#936622", "#8753a0"][index % 4]; }
function activateKey(event: ReactKeyboardEvent, action: () => void) { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); action(); } }
