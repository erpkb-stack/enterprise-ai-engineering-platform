import { BookOpenText, Search, Sparkles, Trash2, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { Empty, ErrorState, Loading, MockBadge, PageHeader, StatusBadge } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select } from "@/components/ui/form";
import { Switch } from "@/components/ui/switch";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { GroundingView } from "@/features/ExecutionView";
import { useAction, useApi, type Page } from "@/hooks/api";
import { fmt } from "@/lib";
import { api, del, post } from "@/services/api";
import { useAuth } from "@/stores/auth";
import type { Grounding, Hit } from "@/types";

type Doc = { id: string; filename: string; collection: string; mime_type: string; size_bytes: number; status: string; chunk_count: number;
  embedding_model: string; created_at: string; is_demo: boolean; chunk_size: number; chunk_overlap: number };
type SearchRes = { hits: Hit[]; strategy: string; candidates: { semantic: number; keyword: number }; timings_ms: Record<string, number>; latency_ms: number };
type AnswerRes = { answer: string; retrieval: SearchRes; verification: Grounding; provider: string; model: string; is_mock: boolean; latency_ms: number; tokens_in: number; tokens_out: number };

export default function KnowledgePage() {
  const can = useAuth((s) => s.can);
  const docs = useApi<Page<Doc>>(["docs"], "/knowledge/documents?limit=200");
  const cols = useApi<{ collection: string; documents: number; chunks: number }[]>(["collections"], "/knowledge/collections");
  const fileRef = useRef<HTMLInputElement>(null);
  const [up, setUp] = useState({ collection: "engineering", chunk_size: 800, chunk_overlap: 120 });
  const [chunksOf, setChunksOf] = useState<Doc | null>(null);
  const upload = useAction(async (file: File) => {
    const fd = new FormData();
    fd.append("file", file); fd.append("collection", up.collection); fd.append("chunk_size", String(up.chunk_size)); fd.append("chunk_overlap", String(up.chunk_overlap));
    return api<Doc>("/knowledge/documents", { method: "POST", form: fd });
  }, { invalidate: [["docs"], ["collections"]], success: (d) => `${d.filename}: ${d.chunk_count} chunks embedded` });
  const remove = useAction((d: Doc) => del(`/knowledge/documents/${d.id}`), { invalidate: [["docs"], ["collections"]], success: "Document deleted" });

  return (
    <>
      <PageHeader icon={BookOpenText} title="RAG Knowledge Center" description="Ingest PDF, DOCX, TXT, Markdown and CSV; chunk, embed into pgvector, and debug semantic / keyword / hybrid / reranked retrieval with full provenance." />
      <RetrievalDebugger collections={cols.data?.map((c) => c.collection) ?? []} />
      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        <Card>
          <CardHeader><CardTitle>Upload</CardTitle><CardDescription>Max 15 MB · duplicate content is rejected per collection</CardDescription></CardHeader>
          <CardContent className="space-y-3">
            <Field label="Collection"><Input value={up.collection} onChange={(e) => setUp({ ...up, collection: e.target.value })} /></Field>
            <div className="grid grid-cols-2 gap-2">
              <Field label="Chunk size (chars)"><Input type="number" min={100} value={up.chunk_size} onChange={(e) => setUp({ ...up, chunk_size: Number(e.target.value) })} /></Field>
              <Field label="Overlap"><Input type="number" min={0} value={up.chunk_overlap} onChange={(e) => setUp({ ...up, chunk_overlap: Number(e.target.value) })} /></Field>
            </div>
            <input ref={fileRef} type="file" className="hidden" accept=".pdf,.docx,.txt,.md,.markdown,.csv" aria-label="Upload document"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) upload.mutate(f); e.target.value = ""; }} />
            <Button className="w-full" onClick={() => fileRef.current?.click()} loading={upload.isPending} disabled={!can("knowledge:write")}><Upload /> Choose file</Button>
            <div className="space-y-1 pt-2">{cols.data?.map((c) => <div key={c.collection} className="flex justify-between text-xs"><span className="font-medium">{c.collection}</span><span className="text-muted-foreground">{c.documents} docs · {c.chunks} chunks</span></div>)}</div>
          </CardContent>
        </Card>
        <Card className="xl:col-span-2">
          <CardHeader><CardTitle>Documents</CardTitle></CardHeader>
          <CardContent className="p-0">
            {docs.isLoading ? <Loading /> : docs.error ? <ErrorState error={docs.error} /> : !docs.data!.items.length ? <div className="p-4"><Empty title="No documents" /></div> : (
              <Table><THead><TR><TH>File</TH><TH>Collection</TH><TH>Status</TH><TH>Chunks</TH><TH>Size</TH><TH>Embedding</TH><TH /></TR></THead>
                <TBody>{docs.data!.items.map((d) => (
                  <TR key={d.id}><TD><button className="text-left font-medium hover:underline" onClick={() => setChunksOf(d)}>{d.filename}</button>{d.is_demo && <Badge variant="warning" className="ml-2">DEMO</Badge>}</TD>
                    <TD>{d.collection}</TD><TD><StatusBadge status={d.status} /></TD><TD className="tabular-nums">{d.chunk_count}</TD>
                    <TD className="tabular-nums">{(d.size_bytes / 1024).toFixed(1)} KB</TD><TD className="font-mono text-[11px]">{d.embedding_model}</TD>
                    <TD>{can("knowledge:write") && <Button size="icon" variant="ghost" aria-label={`Delete ${d.filename}`} onClick={() => confirm(`Delete ${d.filename}?`) && remove.mutate(d)}><Trash2 /></Button>}</TD></TR>))}</TBody></Table>
            )}
          </CardContent>
        </Card>
      </div>
      {chunksOf && <Chunks doc={chunksOf} onClose={() => setChunksOf(null)} />}
    </>
  );
}

function RetrievalDebugger({ collections }: { collections: string[] }) {
  const [q, setQ] = useState("How quickly must a Sev2 incident be acknowledged?");
  const [strategy, setStrategy] = useState("hybrid_rerank");
  const [topK, setTopK] = useState(4);
  const [collection, setCollection] = useState("");
  const [compress, setCompress] = useState(false);
  const [provider, setProvider] = useState("mock");
  const body = () => ({ query: q, strategy, top_k: topK, collection: collection || null, compress });
  const search = useAction(() => post<SearchRes>("/knowledge/search", body()));
  const answer = useAction(() => post<AnswerRes>("/knowledge/answer", { ...body(), provider, model: provider === "ollama" ? "llama3.2:3b" : "mock-reasoner-1" }));
  const res = answer.data?.retrieval ?? search.data;
  const maxScores = res?.hits.reduce<Record<string, number>>((m, h) => { Object.entries(h.scores).forEach(([k, v]) => (m[k] = Math.max(m[k] ?? 0, v))); return m; }, {}) ?? {};
  return (
    <Card>
      <CardHeader><CardTitle>Retrieval debugger</CardTitle><CardDescription>Inspect exactly which chunks contribute, with per-retriever scores, fusion (RRF) and rerank.</CardDescription></CardHeader>
      <CardContent className="space-y-3">
        <div className="grid gap-2 md:grid-cols-[1fr_auto_auto_auto_auto]">
          <Input aria-label="Query" value={q} onChange={(e) => setQ(e.target.value)} />
          <Select aria-label="Strategy" value={strategy} onChange={(e) => setStrategy(e.target.value)}>
            <option value="semantic">Semantic (pgvector)</option><option value="keyword">Keyword (full-text)</option><option value="hybrid">Hybrid (RRF)</option><option value="hybrid_rerank">Hybrid + rerank</option></Select>
          <Select aria-label="Collection" value={collection} onChange={(e) => setCollection(e.target.value)}><option value="">All collections</option>{collections.map((c) => <option key={c}>{c}</option>)}</Select>
          <Input aria-label="Top K" type="number" className="w-20" min={1} max={20} value={topK} onChange={(e) => setTopK(Number(e.target.value))} />
          <Select aria-label="Answer provider" value={provider} onChange={(e) => setProvider(e.target.value)}><option value="mock">mock</option><option value="ollama">ollama</option></Select>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-xs"><Switch checked={compress} onCheckedChange={setCompress} label="Context compression" /> Context compression (experimental flag)</label>
          <Button variant="outline" onClick={() => search.mutate(undefined)} loading={search.isPending}><Search /> Retrieve</Button>
          <Button onClick={() => answer.mutate(undefined)} loading={answer.isPending}><Sparkles /> Answer with citations</Button>
          {res && <span className="text-[11px] text-muted-foreground">{res.candidates.semantic} semantic / {res.candidates.keyword} keyword candidates · {Object.entries(res.timings_ms).map(([k, v]) => `${k} ${v.toFixed(1)}ms`).join(" · ")}</span>}
        </div>
        {answer.data && (
          <div className="grid gap-4 rounded-lg border p-3 xl:grid-cols-2">
            <div><div className="mb-1 flex items-center gap-2 text-xs font-medium">Answer <MockBadge mock={answer.data.is_mock} /><span className="font-normal text-muted-foreground">{fmt.ms(answer.data.latency_ms)}</span></div>
              <p className="whitespace-pre-wrap text-sm">{answer.data.answer}</p></div>
            <GroundingView g={answer.data.verification} passages={answer.data.retrieval.hits.map((h) => h.content)} files={answer.data.retrieval.hits.map((h) => h.filename)} />
          </div>
        )}
        {res && (res.hits.length ? (
          <ol className="space-y-2">{res.hits.map((h, i) => (
            <li key={h.chunk_id} className="rounded-lg border p-3">
              <div className="flex flex-wrap items-center gap-2 text-xs"><Badge variant="brand">[{i + 1}]</Badge><span className="font-medium">{h.filename}</span>
                <span className="text-muted-foreground">chunk #{h.ordinal} · chars {h.char_start}–{h.char_end}</span>
                <span className="ml-auto flex flex-wrap gap-2">{Object.entries(h.scores).map(([k, v]) => (
                  <span key={k} className="flex items-center gap-1"><span className="text-muted-foreground">{k}</span>
                    <span className="inline-block h-1.5 w-12 overflow-hidden rounded bg-muted"><span className="block h-full bg-primary" style={{ width: `${(v / (maxScores[k] || 1)) * 100}%` }} /></span>
                    <span className="tabular-nums">{v.toFixed(3)}</span></span>))}</span></div>
              <p className="mt-1.5 whitespace-pre-wrap text-xs">{h.content}</p>
              {h.compressed && h.compressed !== h.content && <p className="mt-1.5 rounded bg-accent/50 p-1.5 text-xs"><Badge variant="info">compressed</Badge> {h.compressed}</p>}
            </li>))}</ol>
        ) : <Empty title="No matching chunks" />)}
      </CardContent>
    </Card>
  );
}

function Chunks({ doc, onClose }: { doc: Doc; onClose: () => void }) {
  const q = useApi<{ chunks: { id: string; ordinal: number; content: string; token_count: number; char_start: number; char_end: number }[] }>(["chunks", doc.id], `/knowledge/documents/${doc.id}/chunks`);
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={doc.filename} description={`${doc.chunk_count} chunks · size ${doc.chunk_size} · overlap ${doc.chunk_overlap} · ${doc.embedding_model}`} className="max-w-4xl">
      {q.isLoading ? <Loading /> : <ol className="space-y-2">{q.data?.chunks.map((c) => (
        <li key={c.id} className="rounded border p-2 text-xs"><div className="mb-1 text-muted-foreground">#{c.ordinal} · chars {c.char_start}–{c.char_end} · ≈{c.token_count} tokens</div><p className="whitespace-pre-wrap">{c.content}</p></li>))}</ol>}
    </Dialog>
  );
}
