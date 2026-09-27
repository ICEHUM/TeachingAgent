import { useEffect, useState } from "react";
import { api } from "./api";

type Course = { id: string; name: string; code: string; role: "owner" | "teacher" };
type Design = { id: string; course_id: string; file_name: string; text_chars: number; model_chars: number; preview: string; created_at: string };
type QuestionFields = { title: string; objective: string; description: string; starter_code: string; sample_input: string; sample_output: string; answer_outline: string };
type Draft = QuestionFields & { id: string; course_id: string; source_design_id: string; status: string; created_at: string; updated_at: string };

function editableFields(draft: Draft): QuestionFields {
  return {
    title: draft.title, objective: draft.objective, description: draft.description,
    starter_code: draft.starter_code, sample_input: draft.sample_input,
    sample_output: draft.sample_output, answer_outline: draft.answer_outline,
  };
}

async function uploadDesign(courseId: string, userId: string, file: File): Promise<Design> {
  const form = new FormData();
  form.append("file", file);
  const response = await fetch(`/api/product/teacher/courses/${courseId}/designs`, {
    method: "POST", headers: { "X-User-Id": userId }, body: form,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail?.message || "上传失败，请重试。");
  return body as Design;
}

const questionFields: Array<{ key: keyof QuestionFields; label: string; hint?: string; rows?: number }> = [
  { key: "title", label: "题目名称" },
  { key: "objective", label: "学习目标", rows: 2 },
  { key: "description", label: "任务说明", rows: 5 },
  { key: "starter_code", label: "学生起始代码", hint: "只放挖空或待修复代码，不放完整答案。", rows: 5 },
  { key: "sample_input", label: "样例输入", rows: 2 },
  { key: "sample_output", label: "样例输出", rows: 2 },
  { key: "answer_outline", label: "教师解题要点", hint: "只在教师端显示。", rows: 3 },
];

export function CourseBuilder({ userId }: { userId: string }) {
  const [courses, setCourses] = useState<Course[]>([]);
  const [courseId, setCourseId] = useState("");
  const [courseName, setCourseName] = useState("");
  const [designs, setDesigns] = useState<Design[]>([]);
  const [designId, setDesignId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [draftId, setDraftId] = useState("");
  const [editor, setEditor] = useState<QuestionFields | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let current = true;
    void api<{ courses: Course[] }>("/api/product/teacher/courses", userId)
      .then((data) => { if (current) { setCourses(data.courses); setCourseId((id) => id || data.courses[0]?.id || ""); } })
      .catch((cause) => { if (current) setError(cause instanceof Error ? cause.message : "课程载入失败"); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [userId]);

  useEffect(() => {
    if (!courseId) { setDesigns([]); setDrafts([]); return; }
    let current = true;
    setError(""); setDesigns([]); setDrafts([]); setDesignId(""); setDraftId(""); setEditor(null);
    void Promise.all([
      api<{ designs: Design[] }>(`/api/product/teacher/courses/${courseId}/designs`, userId),
      api<{ drafts: Draft[] }>(`/api/product/teacher/courses/${courseId}/question-drafts`, userId),
    ]).then(([source, questions]) => {
      if (!current) return;
      setDesigns(source.designs); setDesignId(source.designs[0]?.id || "");
      setDrafts(questions.drafts);
      if (questions.drafts[0]) { setDraftId(questions.drafts[0].id); setEditor(editableFields(questions.drafts[0])); }
    }).catch((cause) => { if (current) setError(cause instanceof Error ? cause.message : "课程资料载入失败"); });
    return () => { current = false; };
  }, [courseId, userId]);

  const activeCourse = courses.find((item) => item.id === courseId);
  const activeDesign = designs.find((item) => item.id === designId);

  async function createCourse(event: React.FormEvent) {
    event.preventDefault();
    if (courseName.trim().length < 2) { setError("请填写至少两个字的课程名称。"); return; }
    setBusy("course"); setError(""); setNotice("");
    try {
      const created = await api<Course>("/api/product/teacher/courses", userId, {
        method: "POST", body: JSON.stringify({ name: courseName.trim() }),
      });
      setCourses((items) => [created, ...items]); setCourseId(created.id); setCourseName("");
      setNotice(`课程“${created.name}”已创建，接下来上传课程设计。`);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "创建课程失败"); }
    finally { setBusy(""); }
  }

  async function submitDesign(event: React.FormEvent) {
    event.preventDefault();
    if (!file || !courseId) return;
    setBusy("upload"); setError(""); setNotice("");
    try {
      const uploaded = await uploadDesign(courseId, userId, file);
      setDesigns((items) => [uploaded, ...items.filter((item) => item.id !== uploaded.id)]);
      setDesignId(uploaded.id); setFile(null);
      const input = document.getElementById("course-design-file") as HTMLInputElement | null;
      if (input) input.value = "";
      setNotice(`已提取“${uploaded.file_name}”的课程内容，可以生成题目草稿。`);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "上传失败"); }
    finally { setBusy(""); }
  }

  async function generate() {
    if (!courseId || !designId) return;
    setBusy("generate"); setError(""); setNotice("");
    try {
      const result = await api<{ drafts: Draft[] }>(`/api/product/teacher/courses/${courseId}/generate-questions`, userId, {
        method: "POST", body: JSON.stringify({ design_id: designId, count: 3 }),
      });
      setDrafts((items) => [...result.drafts, ...items]);
      setDraftId(result.drafts[0].id); setEditor(editableFields(result.drafts[0]));
      setNotice("已生成 3 道题目草稿。请逐题核对任务和样例。 ");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "生成失败"); }
    finally { setBusy(""); }
  }

  async function downloadDesign() {
    if (!courseId || !activeDesign) return;
    setError("");
    try {
      const response = await fetch(`/api/product/teacher/courses/${courseId}/designs/${activeDesign.id}/download`, {
        headers: { "X-User-Id": userId },
      });
      if (!response.ok) throw new Error("下载原文件失败，请重试。");
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url; link.download = activeDesign.file_name; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "下载失败"); }
  }

  function selectDraft(draft: Draft) {
    setDraftId(draft.id); setEditor(editableFields(draft)); setError(""); setNotice("");
  }

  async function saveDraft(event: React.FormEvent) {
    event.preventDefault();
    if (!editor || !draftId) return;
    setBusy("save"); setError(""); setNotice("");
    try {
      const updated = await api<Draft>(`/api/product/teacher/question-drafts/${draftId}`, userId, {
        method: "PUT", body: JSON.stringify(editor),
      });
      setDrafts((items) => items.map((item) => item.id === updated.id ? updated : item));
      setEditor(editableFields(updated)); setNotice("草稿已保存，尚未发布给学生。");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "保存草稿失败"); }
    finally { setBusy(""); }
  }

  return <main className="course-builder" aria-labelledby="course-builder-title">
    <header className="course-builder-head"><div><h1 id="course-builder-title">课程设计</h1><p>先建课程，再上传教学设计；AI 只生成可修改的题目草稿。</p></div><span>教师工作区</span></header>
    {error && <div className="course-builder-error" role="alert">{error}</div>}
    {notice && <div className="course-builder-notice" role="status">{notice}</div>}
    <div className="course-builder-layout">
      <aside className="course-builder-side" aria-label="我的课程">
        <h2>我的课程</h2>
        <form onSubmit={(event) => void createCourse(event)} className="course-create-form">
          <label htmlFor="course-name">课程名称</label>
          <input id="course-name" value={courseName} onChange={(event) => setCourseName(event.target.value)} placeholder="例如 Python 语言编程基础" maxLength={120} required />
          <button className="primary" type="submit" disabled={!!busy}>{busy === "course" ? "创建中…" : "创建课程"}</button>
        </form>
        {loading ? <p className="course-side-note">正在读取课程…</p> : courses.length ? <div className="course-list">{courses.map((item) => <button key={item.id} className={item.id === courseId ? "selected" : ""} aria-current={item.id === courseId ? "true" : undefined} onClick={() => setCourseId(item.id)}><strong>{item.name}</strong><small>{item.role === "owner" ? "我创建的课程" : "参与授课"}</small></button>)}</div> : <p className="course-side-note">还没有课程。填写名称即可创建。</p>}
      </aside>
      <div className="course-builder-main">
        {!activeCourse ? <section className="course-empty"><h2>先创建一门课程</h2><p>课程建立后可保存课程设计，再请 AI 按教学目标生成题目草稿。</p></section> : <>
          <section className="course-source" aria-labelledby="course-source-title">
            <div className="course-section-head"><div><h2 id="course-source-title">课程资料</h2><p>{activeCourse.name} · 上传 DOCX、TXT 或 MD，最多 2 MB</p></div></div>
            <form className="course-upload-form" onSubmit={(event) => void submitDesign(event)}>
              <label htmlFor="course-design-file">课程设计文件</label>
              <input id="course-design-file" type="file" accept=".docx,.txt,.md" onChange={(event) => setFile(event.target.files?.[0] || null)} />
              <button type="submit" disabled={!file || !!busy}>{busy === "upload" ? "正在提取内容…" : "上传课程设计"}</button>
            </form>
            {designs.length ? <div className="course-design-row"><label htmlFor="course-design-select">出题依据</label><select id="course-design-select" value={designId} onChange={(event) => setDesignId(event.target.value)}>{designs.map((item) => <option key={item.id} value={item.id}>{item.file_name} · {new Date(item.created_at).toLocaleDateString("zh-CN")}</option>)}</select><span>已提取 {activeDesign?.text_chars || 0} 字，AI 使用前 {activeDesign?.model_chars || 0} 字</span></div> : <p className="course-source-empty">尚未上传课程设计。上传后可先检查提取内容，再生成题目。</p>}
            {activeDesign && <div className="course-source-actions"><details className="course-source-preview"><summary>查看提取的前 500 字</summary><p>{activeDesign.preview}{activeDesign.text_chars > activeDesign.preview.length ? "…" : ""}</p></details><button type="button" onClick={() => void downloadDesign()}>下载原文件</button></div>}
          </section>
          <section className="course-questions" aria-labelledby="course-questions-title">
            <div className="course-section-head"><div><h2 id="course-questions-title">题目草稿</h2><p>根据课程设计生成后，逐题检查目标、代码和样例。</p></div><button className="primary" disabled={!designId || !!busy} onClick={() => void generate()}>{busy === "generate" ? "AI 正在生成…" : "AI 生成 3 道题目"}</button></div>
            {!designId && <p className="course-source-empty">先上传一份课程设计，出题按钮才会启用。</p>}
            {designId && !drafts.length && <p className="course-source-empty">还没有题目草稿。AI 的结果会保存在当前课程，教师可继续修改。</p>}
            {!!drafts.length && <div className="course-draft-layout"><nav aria-label="题目草稿列表" className="course-draft-list">{drafts.map((item, index) => <button key={item.id} className={item.id === draftId ? "selected" : ""} onClick={() => selectDraft(item)}><span>草稿 {index + 1}</span><strong>{item.title}</strong></button>)}</nav>{editor && <form className="course-draft-editor" onSubmit={(event) => void saveDraft(event)}><p className="course-draft-boundary">这是教师草稿，尚未进入学生工作台。发布前需配置可运行的公开与隐藏测试。</p>{questionFields.map(({ key, label, hint, rows }) => <label key={key}><span>{label}</span>{rows ? <textarea value={editor[key]} rows={rows} onChange={(event) => setEditor((value) => value && ({ ...value, [key]: event.target.value }))} required={key === "objective" || key === "description"} /> : <input value={editor[key]} onChange={(event) => setEditor((value) => value && ({ ...value, [key]: event.target.value }))} required />}{hint && <small>{hint}</small>}</label>)}<div className="course-draft-actions"><button className="primary" type="submit" disabled={!!busy}>{busy === "save" ? "保存中…" : "保存题目草稿"}</button></div></form>}</div>}
          </section>
        </>}
      </div>
    </div>
  </main>;
}
