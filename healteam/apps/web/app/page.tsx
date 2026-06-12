import Link from "next/link";
import type { AgentInfo } from "@/app/lib/types";

type AgentCard = { name: string; role: string; speaks: boolean };

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

async function fetchAgents(): Promise<AgentCard[] | null> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 5000);

  try {
    const res = await fetch(`${API_BASE}/api/v1/agents`, {
      cache: "no-store",
      signal: controller.signal,
    });
    if (!res.ok) return null;
    const data = (await res.json()) as AgentInfo[];
    return data.map((a) => ({ name: a.name, role: a.role, speaks: a.speaks_in_session }));
  } catch {
    return null;
  } finally {
    clearTimeout(timeout);
  }
}

export default async function HomePage() {
  const agents = await fetchAgents();
  return (
    <main className="mx-auto max-w-3xl px-6 py-12">
      <header className="mb-10">
        <h1 className="text-4xl font-semibold text-heal-700">HealTeam</h1>
        <p className="mt-2 text-heal-900/70">
          家庭治疗 AI 陪练系统 · <span className="font-mono text-sm">W1 foundation · v0.1.0</span>
        </p>
        <p className="mt-4 text-base">
          5 个 AI Agent 协作的"家庭治疗室" — 让心理学学员低成本、高保真地练习干预技能。
        </p>
      </header>

      <section className="mb-10 rounded-lg border border-heal-100 bg-white p-6 shadow-sm">
        <h2 className="mb-3 text-lg font-semibold">服务状态</h2>
        <p className="text-sm text-heal-900/70">
          API base URL: <code className="rounded bg-heal-100 px-1 py-0.5">{API_BASE}</code>
        </p>
        <p className="mt-2 text-sm text-heal-900/70">
          <Link className="text-heal-500 underline" href={`${API_BASE}/healthz`} target="_blank" rel="noreferrer">
            /healthz
          </Link>
          {" · "}
          <Link className="text-heal-500 underline" href={`${API_BASE}/docs`} target="_blank" rel="noreferrer">
            /docs (Swagger)
          </Link>
        </p>
      </section>

      <section className="mb-10 rounded-lg border border-heal-100 bg-white p-6 shadow-sm">
        <h2 className="mb-3 text-lg font-semibold">5 个 Agent（W1 占位）</h2>
        {agents ? (
          <ul className="divide-y divide-heal-100">
            {agents.map((a) => (
              <li key={a.name} className="flex items-center justify-between py-2 text-sm">
                <div>
                  <span className="font-mono text-heal-700">{a.name}</span>
                  <span className="ml-3 text-heal-900/80">{a.role}</span>
                </div>
                <span className={a.speaks ? "text-emerald-600" : "text-heal-900/40"}>
                  {a.speaks ? "speaks in session" : "side-channel / supervision"}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-heal-900/60">
            API 不可用 — 请确认后端服务已启动（<code>{API_BASE}</code>）
          </p>
        )}
      </section>

      <footer className="text-xs text-heal-900/50">
        <p>
          W1 ships architecture + contracts only. 案例选择 / 治疗会话 / 实时辅助 / 复盘报告 are W2 tasks.
        </p>
      </footer>
    </main>
  );
}
