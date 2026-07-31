export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export interface User { id: number; email: string; name: string; created_at: string }
export interface AuthResult { access_token: string; token_type: string; user: User }
export interface KnowledgeBase { id: number; name: string; description: string; document_count: number; created_at: string }
export interface DocumentItem { id: number; name: string; file_type: string; status: "processing" | "ready" | "failed"; page_count: number; chunk_count: number; error_message?: string; created_at: string }
export interface Citation { chunk_id: number; document_name: string; page_number: number; section: string; quote: string; score: number }
export interface ChatResult { conversation_id: number; answer: string; citations: Citation[]; retrieval_ms: number; generation_mode: string }
export interface EvaluationDataset { id: number; knowledge_base_id: number; name: string; description: string; question_count: number; created_at: string }
export interface EvaluationRun { id: number; dataset_id: number; top_k: number; recall_at_k: number; precision_at_k: number; mrr: number; hit_rate: number; average_latency_ms: number; created_at: string }

const TOKEN_KEY = "citemind_access_token";

export function getToken() { return typeof window === "undefined" ? "" : localStorage.getItem(TOKEN_KEY) ?? ""; }
export function saveToken(token: string) { localStorage.setItem(TOKEN_KEY, token); }
export function clearToken() { localStorage.removeItem(TOKEN_KEY); }

async function parseResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const data = await response.json().catch(() => ({ detail: "请求失败" }));
    if (response.status === 401) clearToken();
    throw new Error(data.detail ?? "请求失败");
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

function authFetch(path: string, init: RequestInit = {}) {
  const token = getToken();
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return fetch(`${API_URL}${path}`, { ...init, headers });
}

export const api = {
  login: (email: string, password: string) => fetch(`${API_URL}/api/auth/login`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email, password }) }).then(parseResponse<AuthResult>),
  register: (email: string, password: string, name: string) => fetch(`${API_URL}/api/auth/register`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email, password, name }) }).then(parseResponse<AuthResult>),
  me: () => authFetch("/api/auth/me").then(parseResponse<User>),
  listKnowledgeBases: () => authFetch("/api/knowledge-bases", { cache: "no-store" }).then(parseResponse<KnowledgeBase[]>),
  listDocuments: (knowledgeBaseId: number) => authFetch(`/api/knowledge-bases/${knowledgeBaseId}/documents`, { cache: "no-store" }).then(parseResponse<DocumentItem[]>),
  uploadDocument: (knowledgeBaseId: number, file: File) => { const form = new FormData(); form.append("file", file); return authFetch(`/api/knowledge-bases/${knowledgeBaseId}/documents`, { method: "POST", body: form }).then(parseResponse<DocumentItem>); },
  ask: (knowledgeBaseId: number, question: string, conversationId?: number, documentIds?: number[]) => authFetch(`/api/knowledge-bases/${knowledgeBaseId}/chat`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question, conversation_id: conversationId, document_ids: documentIds }) }).then(parseResponse<ChatResult>),
  listEvaluationDatasets: (knowledgeBaseId: number) => authFetch(`/api/knowledge-bases/${knowledgeBaseId}/evaluation-datasets`).then(parseResponse<EvaluationDataset[]>),
  createEvaluationDataset: (knowledgeBaseId: number, name: string) => authFetch(`/api/knowledge-bases/${knowledgeBaseId}/evaluation-datasets`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) }).then(parseResponse<EvaluationDataset>),
  addEvaluationQuestion: (datasetId: number, question: string, chunkId: number) => authFetch(`/api/evaluation-datasets/${datasetId}/questions`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question, relevant_chunk_ids: [chunkId] }) }).then(parseResponse<{id: number}>),
  runEvaluation: (datasetId: number, topK = 5) => authFetch(`/api/evaluation-datasets/${datasetId}/runs`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ top_k: topK }) }).then(parseResponse<EvaluationRun>),
};
