import { useEffect, useState } from "react";

export type Theme = "light" | "dark";
const COOKIE_NAME = "teaching-agent-theme";

function preferredTheme(): Theme {
  const saved = document.cookie.split("; ").find((part) => part.startsWith(`${COOKIE_NAME}=`))?.split("=")[1];
  if (saved === "light" || saved === "dark") return saved;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(preferredTheme);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.cookie = `${COOKIE_NAME}=${theme}; Path=/; Max-Age=31536000; SameSite=Lax`;
  }, [theme]);
  return { theme, toggleTheme: () => setTheme((current) => current === "light" ? "dark" : "light") };
}

export function ThemeToggle({ theme, onToggle }: { theme: Theme; onToggle: () => void }) {
  const dark = theme === "dark";
  return <button type="button" className="theme-toggle" onClick={onToggle} aria-label={`切换到${dark ? "浅色" : "深色"}主题`} title={`切换到${dark ? "浅色" : "深色"}主题`}>
    <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      {dark ? <><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></> : <path d="M20.4 15.5A8.5 8.5 0 0 1 8.5 3.6 8.5 8.5 0 1 0 20.4 15.5Z"/>}
    </svg>
    <span>{dark ? "浅色" : "深色"}</span>
  </button>;
}
