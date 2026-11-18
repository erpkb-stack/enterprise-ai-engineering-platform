import { Bot, Code2, ScanSearch } from "lucide-react";
import { useState } from "react";
import { Empty, MockBadge, PageHeader } from "@/components/common";
import Editor from "@/components/common/LazyEditor";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select } from "@/components/ui/form";
import { Switch } from "@/components/ui/switch";
import { useAction, useApi } from "@/hooks/api";
import { fmt } from "@/lib";
import { post } from "@/services/api";

type Finding = { rule: string; category: string; severity: string; line: number; message: string; suggestion: string; source: string };
type Res = { id: string; static: { findings: Finding[]; metrics: Record<string, unknown> }; ai: { summary?: string; suggestions?: { title: string; detail: string; confidence: string }[];
  generated_tests?: string; is_mock?: boolean; model?: string; disclaimer?: string; error?: string }; signatures: string[] };

const SAMPLE = `import asyncio
import time
import requests

API_KEY = "sk-live-0123456789abcdef"
_cache = {}


async def fetch_all(urls, results=[]):
    for url in urls:
        time.sleep(0.1)                      # blocking in async
        r = requests.get(url)                # blocking + no timeout
        results.append(r.json())
    asyncio.create_task(refresh_cache())     # fire-and-forget
    return results


async def refresh_cache():
    global _cache
    f = open("/tmp/cache.json")             # resource leak
    _cache = eval(f.read())                  # dangerous eval


def query(cur, user_id):
    cur.execute(f"SELECT * FROM users WHERE id = {user_id}")  # SQL injection
    try:
        return cur.fetchone()
    except:
        pass
`;

export default function CodeReviewPage() {
  const [code, setCode] = useState(SAMPLE);
  const [ai, setAi] = useState(true);
  const [provider, setProvider] = useState("mock");
  const history = useApi<{ id: string; filename: string; created_at: string; metrics: { by_severity?: Record<string, number> } }[]>(["code-reviews"], "/code-review");
  const run = useAction(() => post<Res>("/code-review", { code, ai_review: ai, provider, model: provider === "ollama" ? "llama3.2:3b" : "mock-reasoner-1" }), { invalidate: [["code-reviews"]] });
  const sev = { high: "danger", medium: "warning", low: "muted" } as const;
  return (
    <>
      <PageHeader icon={Code2} title="Engineering Code Review Assistant" description="Deterministic AST analysis (async blocking I/O, concurrency, leaks, security, API contracts, complexity) kept strictly separate from AI-generated suggestions."
        actions={<><label className="flex items-center gap-2 text-xs"><Switch checked={ai} onCheckedChange={setAi} label="AI review" /> AI suggestions</label>
          <Select aria-label="Provider" className="w-32" value={provider} onChange={(e) => setProvider(e.target.value)}><option>mock</option><option>ollama</option></Select>
          <Button onClick={() => run.mutate(undefined)} loading={run.isPending}><ScanSearch /> Review code</Button></>} />
      <div className="grid gap-4 xl:grid-cols-2">
        <Editor language="python" value={code} onChange={setCode} height={560} ariaLabel="Python code" />
        <div className="space-y-4">
          {!run.data ? <Empty title="Paste or edit Python code, then run the review" /> : <>
            <Card><CardHeader><CardTitle className="flex items-center gap-2"><ScanSearch className="size-4" /> Static analysis <Badge variant="brand">deterministic</Badge></CardTitle>
              <CardDescription>{String(run.data.static.metrics.lines ?? "")} lines · {String(run.data.static.metrics.functions ?? 0)} functions · {run.data.static.findings.length} findings</CardDescription></CardHeader>
              <CardContent className="max-h-80 space-y-1.5 overflow-y-auto">{run.data.static.findings.map((f, i) => (
                <div key={i} className="rounded-md border p-2 text-xs"><div className="flex flex-wrap items-center gap-1.5"><Badge variant={sev[f.severity as keyof typeof sev] ?? "muted"}>{f.severity}</Badge>
                  <Badge variant="outline">{f.category}</Badge><code className="text-[11px]">{f.rule}</code><span className="ml-auto text-muted-foreground">line {f.line}</span></div>
                  <div className="mt-1">{f.message}</div><div className="text-muted-foreground">→ {f.suggestion}</div></div>))}</CardContent></Card>
            {ai && <Card className="border-dashed"><CardHeader><CardTitle className="flex items-center gap-2"><Bot className="size-4" /> AI suggestions <Badge variant="outline">AI-generated · unverified</Badge><MockBadge mock={run.data.ai.is_mock} /></CardTitle>
              <CardDescription>{run.data.ai.disclaimer ?? run.data.ai.error}</CardDescription></CardHeader>
              <CardContent className="space-y-2 text-xs">{run.data.ai.summary && <p>{run.data.ai.summary}</p>}
                {run.data.ai.suggestions?.map((s, i) => <div key={i} className="rounded border p-2"><div className="font-medium">{s.title} <Badge variant="muted">{s.confidence} confidence</Badge></div><p className="text-muted-foreground">{s.detail}</p></div>)}
                {run.data.ai.generated_tests && <><div className="font-medium">Generated tests</div><Editor language="python" value={run.data.ai.generated_tests} readOnly height={180} ariaLabel="Generated tests" /></>}
              </CardContent></Card>}
          </>}
          <Card><CardHeader><CardTitle>History</CardTitle></CardHeader><CardContent className="space-y-1 text-xs">{history.data?.map((h) => (
            <div key={h.id} className="flex justify-between"><span>{h.filename}</span><span className="text-muted-foreground">{Object.entries(h.metrics.by_severity ?? {}).map(([k, v]) => `${v} ${k}`).join(" · ")} · {fmt.ago(h.created_at)}</span></div>))}</CardContent></Card>
        </div>
      </div>
    </>
  );
}
