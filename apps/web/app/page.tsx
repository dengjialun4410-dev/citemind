"use client";

import { ChangeEvent, FormEvent, useEffect, useRef, useState } from "react";
import { api, ChatResult, clearToken, DocumentItem, EvaluationDataset, EvaluationRun, KnowledgeBase, saveToken, User } from "@/lib/api";
import { ArrowIcon, CheckIcon, FileIcon, LibraryIcon, PlusIcon, QuoteIcon, SearchIcon, SparkIcon, UploadIcon } from "@/components/icons";

type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  result?: ChatResult;
};

const suggestions = [
  "总结文档的核心研究问题与贡献",
  "作者使用了哪些数据集和评价指标？",
  "这项工作的主要局限性是什么？",
];

const confidenceLabel = {
  high: "证据充分",
  medium: "证据一般",
  low: "证据不足",
};

function getConfidence(result: ChatResult) {
  return result.confidence ?? "medium";
}

function getEvidenceCoverage(result: ChatResult) {
  return Number.isFinite(result.evidence_coverage) ? result.evidence_coverage : 0;
}

export default function Home() {
  const [user, setUser] = useState<User | null>(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [authMode, setAuthMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("demo@citemind.dev");
  const [password, setPassword] = useState("CiteMind123!");
  const [displayName, setDisplayName] = useState("");
  const [authBusy, setAuthBusy] = useState(false);
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [selectedDocumentId, setSelectedDocumentId] = useState<number | "all" | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [question, setQuestion] = useState("");
  const [conversationId, setConversationId] = useState<number>();
  const [isUploading, setUploading] = useState(false);
  const [isAsking, setAsking] = useState(false);
  const [reindexingId, setReindexingId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [evaluationDatasets, setEvaluationDatasets] = useState<EvaluationDataset[]>([]);
  const [evaluationRun, setEvaluationRun] = useState<EvaluationRun | null>(null);
  const [evaluationBusy, setEvaluationBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.me().then(setUser).catch(() => clearToken()).finally(() => setAuthChecked(true));
  }, []);

  useEffect(() => {
    if (!user) return;
    api.listKnowledgeBases()
      .then((items) => {
        setKnowledgeBases(items);
        if (items[0]) setActiveId(items[0].id);
      })
      .catch((err: Error) => setError(`无法连接 API：${err.message}`));
  }, [user]);

  useEffect(() => {
    if (!activeId) return;
    api.listDocuments(activeId).then((items) => {
      setDocuments(items);
      setSelectedDocumentId((current) => {
        if (current === "all") return current;
        if (typeof current === "number" && items.some((item) => item.id === current && item.status === "ready")) return current;
        return items.find((item) => item.status === "ready")?.id ?? null;
      });
    }).catch((err: Error) => setError(err.message));
    api.listEvaluationDatasets(activeId).then(setEvaluationDatasets).catch(() => setEvaluationDatasets([]));
    setMessages([]);
    setConversationId(undefined);
    setSelectedDocumentId(null);
  }, [activeId]);

  useEffect(() => {
    if (!activeId || !documents.some((doc) => doc.status === "processing")) return;
    const timer = window.setInterval(() => {
      api.listDocuments(activeId).then((items) => {
        setDocuments(items);
        setSelectedDocumentId((current) => current ?? items.find((item) => item.status === "ready")?.id ?? null);
      }).catch(() => undefined);
    }, 1800);
    return () => window.clearInterval(timer);
  }, [activeId, documents]);

  const activeBase = knowledgeBases.find((item) => item.id === activeId);

  async function submitAuth(event: FormEvent) {
    event.preventDefault();
    setAuthBusy(true);
    setError("");
    try {
      const result = authMode === "login" ? await api.login(email, password) : await api.register(email, password, displayName);
      saveToken(result.access_token);
      setUser(result.user);
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败");
    } finally {
      setAuthBusy(false);
    }
  }

  function logout() {
    clearToken();
    setUser(null);
    setKnowledgeBases([]);
    setActiveId(null);
  }

  async function addCitationToEvaluation(questionText: string, chunkId: number) {
    if (!activeId || evaluationBusy) return;
    setEvaluationBusy(true);
    setError("");
    try {
      let dataset = evaluationDatasets[0];
      if (!dataset) {
        dataset = await api.createEvaluationDataset(activeId, "默认检索评测集");
        setEvaluationDatasets([dataset]);
      }
      await api.addEvaluationQuestion(dataset.id, questionText, chunkId);
      setEvaluationDatasets((items) => items.map((item) => item.id === dataset.id ? { ...item, question_count: item.question_count + 1 } : item));
      setNotice("已将问题与相关证据加入检索评测集");
      window.setTimeout(() => setNotice(""), 2600);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加入评测集失败");
    } finally {
      setEvaluationBusy(false);
    }
  }

  async function runEvaluation() {
    const dataset = evaluationDatasets[0];
    if (!dataset || dataset.question_count === 0) return;
    setEvaluationBusy(true);
    setError("");
    try { setEvaluationRun(await api.runEvaluation(dataset.id)); }
    catch (err) { setError(err instanceof Error ? err.message : "评测失败"); }
    finally { setEvaluationBusy(false); }
  }

  async function onFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file || !activeId) return;
    setError("");
    setUploading(true);
    try {
      const item = await api.uploadDocument(activeId, file);
      setDocuments((current) => [item, ...current]);
      setSelectedDocumentId(item.status === "ready" ? item.id : null);
      setKnowledgeBases((current) => current.map((kb) => kb.id === activeId ? { ...kb, document_count: kb.document_count + 1 } : kb));
    } catch (err) {
      setError(err instanceof Error ? err.message : "上传失败");
    } finally {
      setUploading(false);
      event.target.value = "";
    }
  }

  async function ask(value?: string) {
    const prompt = (value ?? question).trim();
    if (!prompt || !activeId || isAsking) return;
    setQuestion("");
    setError("");
    setMessages((current) => [...current, { role: "user", content: prompt }]);
    setAsking(true);
    try {
      const documentIds = typeof selectedDocumentId === "number" ? [selectedDocumentId] : undefined;
      const result = await api.ask(activeId, prompt, conversationId, documentIds);
      setConversationId(result.conversation_id);
      setMessages((current) => [...current, { role: "assistant", content: result.answer, result }]);
    } catch (err) {
      const message = err instanceof Error ? err.message : "问答失败";
      setError(message);
      setMessages((current) => [...current, { role: "assistant", content: `暂时无法回答：${message}` }]);
    } finally {
      setAsking(false);
    }
  }

  async function reindexSelectedDocument() {
    if (typeof selectedDocumentId !== "number" || reindexingId) return;
    setReindexingId(selectedDocumentId);
    setError("");
    try {
      const updated = await api.reindexDocument(selectedDocumentId);
      setDocuments((items) => items.map((item) => item.id === updated.id ? updated : item));
      setNotice("已使用当前 Embedding 模型重新建立索引");
      window.setTimeout(() => setNotice(""), 2600);
    } catch (err) {
      setError(err instanceof Error ? err.message : "重新索引失败");
    } finally {
      setReindexingId(null);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void ask();
  }

  if (!authChecked) return <div className="auth-loading"><span className="brand-mark"><SparkIcon /></span><p>正在连接可信知识库…</p></div>;

  if (!user) return (
    <main className="auth-page">
      <section className="auth-story">
        <div className="brand auth-brand"><span className="brand-mark"><SparkIcon /></span><span>CiteMind</span></div>
        <div><p className="eyebrow auth-eyebrow">EVIDENCE-FIRST RESEARCH</p><h1>让每一个答案，<br /><em>都有文献依据。</em></h1><p>面向科研阅读的可溯源 RAG 知识库。混合检索、原文引用和量化评测，都在一个工作台完成。</p></div>
        <div className="auth-proof"><span>01</span><p><strong>可核查</strong>页码、章节与原文证据完整关联</p><span>02</span><p><strong>可评测</strong>用 Recall@K 与 MRR 验证检索质量</p></div>
      </section>
      <section className="auth-form-wrap">
        <form className="auth-form" onSubmit={submitAuth}>
          <p className="eyebrow">PERSONAL RESEARCH SPACE</p><h2>{authMode === "login" ? "欢迎回来" : "创建研究空间"}</h2><p className="auth-subtitle">{authMode === "login" ? "登录后继续你的文献研究" : "建立属于你的可信知识库"}</p>
          {authMode === "register" && <label>你的称呼<input value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="研究者" required /></label>}
          <label>邮箱<input type="email" value={email} onChange={(event) => setEmail(event.target.value)} required /></label>
          <label>密码<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} minLength={8} required /></label>
          {error && <div className="auth-error">{error}</div>}
          <button className="auth-submit" disabled={authBusy}>{authBusy ? "请稍候…" : authMode === "login" ? "进入工作台" : "创建账户"}<ArrowIcon /></button>
          <button type="button" className="auth-switch" onClick={() => { setAuthMode(authMode === "login" ? "register" : "login"); setError(""); }}>{authMode === "login" ? "没有账户？立即注册" : "已有账户？返回登录"}</button>
          {authMode === "login" && <div className="demo-credential"><span>演示账户</span><code>demo@citemind.dev</code><code>CiteMind123!</code></div>}
        </form>
      </section>
    </main>
  );

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark"><SparkIcon /></span><span>CiteMind</span></div>
        <button className="new-chat" onClick={() => { setMessages([]); setConversationId(undefined); }}><PlusIcon /> 新建研究对话</button>

        <div className="side-section">
          <div className="side-label"><span>知识库</span><button aria-label="新建知识库"><PlusIcon /></button></div>
          <div className="knowledge-list">
            {knowledgeBases.map((kb) => (
              <button key={kb.id} className={`knowledge-item ${activeId === kb.id ? "active" : ""}`} onClick={() => setActiveId(kb.id)}>
                <span className="knowledge-icon"><LibraryIcon /></span>
                <span><strong>{kb.name}</strong><small>{kb.document_count} 篇文档</small></span>
              </button>
            ))}
          </div>
        </div>

        <div className="side-bottom">
          <div className="local-badge"><span className="pulse" /><span><strong>本地安全模式</strong><small>文档仅存储在你的环境</small></span></div>
          <button className="profile" onClick={logout} title="退出登录"><div className="avatar">{user.name.slice(0, 1)}</div><span><strong>{user.name}</strong><small>{user.email}</small></span><span className="more">退出</span></button>
        </div>
      </aside>

      <section className="workspace">
        {notice && <div className="notice-toast"><CheckIcon />{notice}</div>}
        <header className="topbar">
          <div><p className="eyebrow">当前知识库</p><h1>{activeBase?.name ?? "正在加载..."}</h1></div>
          <div className="top-actions"><button className="icon-button" aria-label="搜索"><SearchIcon /></button><button className="upload-top" onClick={() => fileInput.current?.click()} disabled={isUploading}><UploadIcon />{isUploading ? "正在解析..." : "上传文档"}</button></div>
        </header>

        <div className="content-grid">
          <section className="chat-panel">
            <div className="chat-scroll">
              {messages.length === 0 ? (
                <div className="welcome">
                  <div className="welcome-symbol"><SparkIcon /></div>
                  <p className="eyebrow accent">RESEARCH WITH EVIDENCE</p>
                  <h2>从文献出发，<br /><em>得到可信答案。</em></h2>
                  <p className="welcome-copy">CiteMind 会检索你的私人知识库，并为每个关键结论附上可核查的原文证据。</p>
                  <div className="suggestions">
                    {suggestions.map((item, index) => <button key={item} onClick={() => void ask(item)} disabled={!documents.length}><span>0{index + 1}</span>{item}<ArrowIcon /></button>)}
                  </div>
                </div>
              ) : (
                <div className="messages">
                  {messages.map((message, index) => (
                    <article className={`message ${message.role}`} key={`${message.role}-${index}`}>
                      <div className="message-avatar">{message.role === "user" ? "你" : <SparkIcon />}</div>
                      <div className="message-body">
                        <div className="message-meta">{message.role === "user" ? "你的问题" : "CiteMind"}</div>
                        <p>{message.content}</p>
                        {message.result && <div className="answer-meta"><span><CheckIcon /> 已核对 {message.result.citations.length} 条证据</span><span>语义 + BM25 · {message.result.retrieval_ms} ms</span><span className={`confidence ${getConfidence(message.result)}`}>{confidenceLabel[getConfidence(message.result)]} · {(getEvidenceCoverage(message.result) * 100).toFixed(0)}%</span><span>{message.result.generation_mode === "remote-llm" ? "模型综合" : message.result.generation_mode === "local-fallback" ? "模型降级" : "本地摘要"}</span></div>}
                        {message.result?.citations.map((citation, citationIndex) => (
                          <details className="citation" key={citation.chunk_id}>
                            <summary><QuoteIcon /><span>[{citationIndex + 1}] {citation.document_name}</span><b>相关度 {Math.round(Math.min(1, citation.score) * 100)}% · 第 {citation.page_number} 页</b></summary>
                            <p>{citation.quote}</p><button type="button" onClick={() => void addCitationToEvaluation(messages[index - 1]?.content ?? "", citation.chunk_id)}>+ 设为评测相关证据</button>
                          </details>
                        ))}
                      </div>
                    </article>
                  ))}
                  {isAsking && <article className="message assistant"><div className="message-avatar"><SparkIcon /></div><div className="thinking"><i /><i /><i />正在检索证据</div></article>}
                </div>
              )}
            </div>

            <form className="composer" onSubmit={submit}>
              <div className="composer-box">
                <textarea value={question} onChange={(event) => setQuestion(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void ask(); } }} placeholder={documents.length ? "向你的知识库提问…" : "请先上传一篇论文或文档…"} disabled={!documents.length || isAsking} rows={2} />
                <div className="composer-footer"><span><SparkIcon /> {typeof selectedDocumentId === "number" ? documents.find((item) => item.id === selectedDocumentId)?.name : "全部文档"} · 引用校验</span><button type="submit" disabled={!question.trim() || isAsking}><ArrowIcon /></button></div>
              </div>
              <small>回答由 AI 生成，请通过引用原文核查关键结论。</small>
            </form>
          </section>

          <aside className="document-panel">
            <div className="panel-heading"><div><p className="eyebrow">SOURCE LIBRARY</p><h3>文献资料</h3></div><span>{documents.length}</span></div>
            <button className="dropzone" onClick={() => fileInput.current?.click()} disabled={isUploading}>
              <span><UploadIcon /></span><strong>{isUploading ? "解析与建立索引中…" : "添加研究文档"}</strong><small>PDF、DOCX、MD 或 TXT · 最大 25MB</small>
            </button>
            <input ref={fileInput} type="file" accept=".pdf,.docx,.md,.txt" onChange={onFile} hidden />
            {error && <div className="error-banner">{error}</div>}
            {documents.length > 0 && <div className="scope-row"><span>检索范围</span><div className="scope-actions"><button className={selectedDocumentId === "all" ? "active" : ""} onClick={() => setSelectedDocumentId("all")}>全部文档</button>{typeof selectedDocumentId === "number" && <button onClick={() => void reindexSelectedDocument()} disabled={Boolean(reindexingId)}>{reindexingId ? "索引中…" : "重建索引"}</button>}</div></div>}
            <div className="document-list">
              {documents.length === 0 ? <div className="empty-docs"><FileIcon /><p>知识库还是空的</p><small>上传第一篇文档后即可开始提问</small></div> : documents.map((doc) => (
                <button type="button" className={`document-card ${selectedDocumentId === doc.id ? "selected" : ""}`} key={doc.id} onClick={() => doc.status === "ready" && setSelectedDocumentId(doc.id)} disabled={doc.status !== "ready"}>
                  <div className={`file-type ${doc.file_type}`}>{doc.file_type.toUpperCase()}</div>
                  <div className="file-info"><strong title={doc.name}>{doc.name}</strong><small>{doc.page_count} 页 · {doc.chunk_count} 个证据块</small><span className={`status ${doc.status}`}><i />{doc.status === "ready" ? "索引就绪" : doc.status === "failed" ? "解析失败" : "处理中"}</span></div>
                </button>
              ))}
            </div>
            <div className="quality-card evaluation-card"><div><QuoteIcon /><span><strong>检索质量评测</strong><small>{evaluationDatasets[0]?.question_count ?? 0} 个基准问题</small></span></div>{evaluationRun ? <div className="metric-grid"><span><b>{(evaluationRun.recall_at_k * 100).toFixed(0)}%</b>Recall@{evaluationRun.top_k}</span><span><b>{evaluationRun.mrr.toFixed(2)}</b>MRR</span><span><b>{evaluationRun.average_latency_ms.toFixed(0)}ms</b>延迟</span></div> : <div className="quality-bar"><i /><i /><i /><i /></div>}<button onClick={() => void runEvaluation()} disabled={!evaluationDatasets[0]?.question_count || evaluationBusy}>{evaluationBusy ? "处理中…" : "运行检索基准测试"}</button></div>
          </aside>
        </div>
      </section>
    </main>
  );
}
