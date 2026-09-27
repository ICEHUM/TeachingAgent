import { useEffect, useState, type FormEvent } from "react";
import { ThemeToggle, useTheme } from "./theme";

type LoginResult = { role: "student" | "teacher"; display_name: string; user_id: string; attempt_id: string | null };
type OpenClass = { id: string; name: string; teacher_name: string; course_name: string | null };

function errorMessage(body: unknown, fallback: string): string {
  if (!body || typeof body !== "object") return fallback;
  const detail = (body as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    const message = (detail as { message?: unknown }).message;
    if (typeof message === "string") return message;
  }
  return fallback;
}

function enter(result: LoginResult, showClasses: boolean) {
  const publicUrl = result.role === "student" ? import.meta.env.VITE_STUDENT_URL : import.meta.env.VITE_TEACHER_URL;
  const target = publicUrl ? new URL(publicUrl, location.href) : new URL(location.href);
  if (!publicUrl) {
    target.port = result.role === "student" ? "5173" : "5174";
    target.pathname = "/";
  }
  target.search = "";
  target.hash = "";
  target.searchParams.set("user", result.user_id);
  if (result.attempt_id) target.searchParams.set("attempt", result.attempt_id);
  if (showClasses && result.role === "teacher") target.searchParams.set("view", "classes");
  location.assign(target.toString());
}

export function AuthEntry() {
  const { theme, toggleTheme } = useTheme();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [role, setRole] = useState<"student" | "teacher">("student");
  const [account, setAccount] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [name, setName] = useState("");
  const [classes, setClasses] = useState<OpenClass[]>([]);
  const [classId, setClassId] = useState("");
  const [classLoading, setClassLoading] = useState(false);
  const [classReload, setClassReload] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => { document.title = "AI 实训教练 · 登录"; }, []);
  useEffect(() => {
    if (mode !== "register" || role !== "student") return;
    let current = true;
    setClassLoading(true);
    void fetch("/api/product/auth/classes")
      .then(async (response) => {
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(errorMessage(body, "班级列表载入失败"));
        if (current) {
          const rows = (body as { classes: OpenClass[] }).classes;
          setClasses(rows);
          setClassId((selected) => rows.some((item) => item.id === selected) ? selected : rows[0]?.id || "");
        }
      })
      .catch((cause) => { if (current) setError(cause instanceof Error ? cause.message : "班级列表载入失败"); })
      .finally(() => { if (current) setClassLoading(false); });
    return () => { current = false; };
  }, [mode, role, classReload]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    if (mode === "register" && password !== confirm) { setError("两次输入的密码不一致。"); return; }
    if (mode === "register" && role === "student" && !classId) { setError("请先选择班级；如果列表为空，请老师先创建班级。"); return; }
    setSubmitting(true);
    try {
      const path = mode === "login" ? "/api/product/auth/login" : `/api/product/auth/register/${role}`;
      const payload = mode === "login" ? { account, password } : {
        account, password, display_name: name, ...(role === "student" ? { class_id: classId } : {}),
      };
      const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(errorMessage(body, mode === "login" ? "登录失败" : "注册失败"));
      enter(body as LoginResult, mode === "register");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "请求失败，请检查服务状态。"); }
    finally { setSubmitting(false); }
  }

  return <main className="login-page">
    <ThemeToggle theme={theme} onToggle={toggleTheme} />
    <section className="login-shell" aria-labelledby="login-title">
      <div className="login-context">
        <div className="login-brand"><span className="brand-mark">AI</span><strong>实训教练</strong></div>
        <h1 id="login-title">进入实训课堂</h1>
        <p>从编写代码到运行反馈，再到教师介入，每一步都有清晰的依据。</p>
        <dl><div><dt>学生</dt><dd>编辑代码、运行检查、获取分级指导</dd></div><div><dt>教师</dt><dd>创建班级、查看课堂状态、评价学习证据</dd></div></dl>
      </div>
      <form className="login-form" onSubmit={(event) => void submit(event)}>
        <div className="auth-mode-tabs" role="tablist" aria-label="账号操作">
          <button type="button" role="tab" aria-selected={mode === "login"} onClick={() => { setMode("login"); setError(""); }}>登录</button>
          <button type="button" role="tab" aria-selected={mode === "register"} onClick={() => { setMode("register"); setError(""); }}>注册</button>
        </div>
        <header><h2>{mode === "login" ? "登录工作台" : "创建新账号"}</h2><p>{mode === "login" ? "用已有账号进入学生或教师工作区。" : "教师可创建班级；学生注册时加入指定班级。"}</p></header>
        {mode === "register" && <>
          <fieldset className="auth-role"><legend>注册身份</legend><button type="button" aria-pressed={role === "student"} onClick={() => setRole("student")}>学生</button><button type="button" aria-pressed={role === "teacher"} onClick={() => setRole("teacher")}>教师</button></fieldset>
          <label><span>姓名</span><input autoComplete="name" value={name} onChange={(event) => setName(event.target.value)} placeholder="请输入姓名" minLength={2} maxLength={120} required /></label>
        </>}
        <label><span>账号</span><input autoFocus autoComplete="username" value={account} onChange={(event) => setAccount(event.target.value)} placeholder={mode === "login" ? "请输入账号" : "4–40 位字母、数字或下划线"} minLength={mode === "register" ? 4 : undefined} required /></label>
        <label><span>密码</span><input type="password" autoComplete={mode === "login" ? "current-password" : "new-password"} value={password} onChange={(event) => setPassword(event.target.value)} placeholder={mode === "login" ? "请输入密码" : "至少 6 位"} minLength={mode === "register" ? 6 : undefined} required /></label>
        {mode === "register" && <label><span>确认密码</span><input type="password" autoComplete="new-password" value={confirm} onChange={(event) => setConfirm(event.target.value)} placeholder="再次输入密码" required /></label>}
        {mode === "register" && role === "student" && <><label><span>加入班级</span><select value={classId} onChange={(event) => setClassId(event.target.value)} disabled={classLoading || classes.length === 0} required><option value="">{classLoading ? "正在载入班级…" : classes.length ? "请选择班级" : "暂无可选班级"}</option>{classes.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.teacher_name}{item.course_name ? ` · ${item.course_name}` : ""}</option>)}</select></label>{!classLoading && classes.length === 0 && <div className="auth-class-empty"><span>请老师先创建班级，再回来选择。</span><button type="button" onClick={() => setClassReload((value) => value + 1)}>刷新班级</button></div>}</>}
        {error && <div className="login-error" role="alert"><b>!</b><span>{error}</span></div>}
        <button className="login-submit" type="submit" disabled={submitting || (mode === "register" && role === "student" && (classLoading || classes.length === 0))}>{submitting ? "正在处理…" : mode === "login" ? "进入教学空间" : role === "teacher" ? "注册并管理班级" : "注册并加入班级"}</button>
        {mode === "login" && <div className="login-accounts"><span>演示账号</span><button type="button" aria-pressed={account === "demo_student"} onClick={() => setAccount("demo_student")}>学生 <strong>demo_student</strong></button><button type="button" aria-pressed={account === "demo_teacher"} onClick={() => setAccount("demo_teacher")}>教师一 <strong>demo_teacher</strong></button><button type="button" aria-pressed={account === "demo_teacher2"} onClick={() => setAccount("demo_teacher2")}>教师二 <strong>demo_teacher2</strong></button><button type="button" aria-pressed={account === "demo_teacher3"} onClick={() => setAccount("demo_teacher3")}>教师三 <strong>demo_teacher3</strong></button></div>}
        <footer>{mode === "login" ? <>演示密码：<code>123456</code> · 仅供测试</> : "本地试运行账号；教师创建班级后，学生即可选择加入。"}</footer>
      </form>
    </section>
  </main>;
}
