import { webEnv } from "@/lib/env";
import type {
  ApiErrorEnvelope,
  AuditLogListResponse,
  AuthTokenResponse,
  AuthUser,
  ClientRecord,
  ClientStatus,
  MembershipSummary,
  SkillCatalogItem,
  SocialAuthTokenResponse,
  SocialLoginStartResponse,
  SocialProvider,
  UsageSummaryRecord,
  WorkerJobRecord,
  WorkerTaskType,
  Workspace,
  WorkspaceType,
} from "@/lib/types";

const TOKEN_STORAGE_KEY = "metuse.access_token";

export class ApiClientError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;

  constructor(message: string, status: number, code = "API_ERROR", requestId: string | null = null) {
    super(message);
    this.name = "ApiClientError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

export function getStoredAccessToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage.getItem(TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function setStoredAccessToken(token: string | null): void {
  if (typeof window === "undefined") return;
  try {
    if (token) {
      window.sessionStorage.setItem(TOKEN_STORAGE_KEY, token);
    } else {
      window.sessionStorage.removeItem(TOKEN_STORAGE_KEY);
    }
  } catch {
    // Ignore storage quota errors in restricted browser contexts
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getStoredAccessToken();
  const headers = new Headers(init.headers);
  if (!headers.has("Content-Type") && init.body) {
    headers.set("Content-Type", "application/json");
  }
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  let response: Response;
  try {
    response = await fetch(`${webEnv.apiBaseUrl}${path}`, {
      ...init,
      headers,
      credentials: "include",
    });
  } catch {
    throw new ApiClientError(
      `Unable to reach Metuse API at ${webEnv.apiBaseUrl}. Ensure FastAPI is running.`,
      0,
      "NETWORK_ERROR"
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const contentType = response.headers.get("content-type") || "";
  const isJson = contentType.includes("application/json");
  const payload = isJson ? ((await response.json()) as unknown) : null;

  if (!response.ok) {
    const env = (payload ?? {}) as ApiErrorEnvelope;
    const message =
      env.error?.message ||
      env.detail ||
      `Request failed with status ${response.status}`;
    const code = env.error?.code || `HTTP_${response.status}`;
    const requestId = env.error?.request_id || response.headers.get("X-Request-Id");
    throw new ApiClientError(message, response.status, code, requestId);
  }

  return payload as T;
}

export const apiClient = {
  health: () => request<{ status: string; service: string }>("/healthz"),

  register: async (input: { email: string; password: string; full_name?: string }) => {
    const res = await request<AuthTokenResponse>("/api/v1/auth/register", {
      method: "POST",
      body: JSON.stringify(input),
    });
    setStoredAccessToken(res.access_token);
    return res;
  },

  login: async (input: { email: string; password: string }) => {
    const res = await request<AuthTokenResponse>("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify(input),
    });
    setStoredAccessToken(res.access_token);
    return res;
  },

  logout: async () => {
    try {
      await request<void>("/api/v1/auth/logout", { method: "POST" });
    } finally {
      setStoredAccessToken(null);
    }
  },

  me: () => request<AuthUser>("/api/v1/auth/me"),

  socialStart: (provider: SocialProvider) =>
    request<SocialLoginStartResponse>(`/api/v1/auth/${provider}/start`),

  socialCallback: async (
    provider: SocialProvider,
    input: { code: string; state: string }
  ) => {
    const res = await request<SocialAuthTokenResponse>(
      `/api/v1/auth/${provider}/callback`,
      { method: "POST", body: JSON.stringify(input) }
    );
    setStoredAccessToken(res.access_token);
    return res;
  },

  listWorkspaces: () => request<MembershipSummary[]>("/api/v1/workspaces"),

  createWorkspace: (input: {
    name: string;
    workspace_type: WorkspaceType;
    slug?: string;
    max_members?: number;
  }) =>
    request<Workspace>("/api/v1/workspaces", {
      method: "POST",
      body: JSON.stringify(input),
    }),

  listClients: (workspaceId: string) =>
    request<ClientRecord[]>(`/api/v1/workspaces/${workspaceId}/clients`),

  createClient: (
    workspaceId: string,
    input: { name: string; slug?: string; status?: ClientStatus }
  ) =>
    request<ClientRecord>(`/api/v1/workspaces/${workspaceId}/clients`, {
      method: "POST",
      body: JSON.stringify(input),
    }),

  listAuditLogs: (workspaceId: string, limit = 15) =>
    request<AuditLogListResponse>(
      `/api/v1/workspaces/${workspaceId}/audit-logs?limit=${limit}`
    ),

  listSkills: (workspaceId: string) =>
    request<SkillCatalogItem[]>(`/api/v1/workspaces/${workspaceId}/skills`),

  listJobs: (workspaceId: string, limit = 20) =>
    request<WorkerJobRecord[]>(`/api/v1/workspaces/${workspaceId}/jobs?limit=${limit}`),

  runWorkerJob: (
    workspaceId: string,
    clientId: string,
    taskType: WorkerTaskType,
    input: { topic: string; constraints?: Record<string, unknown> },
    idempotencyKey?: string
  ) => {
    const endpointMap: Record<WorkerTaskType, string> = {
      research: "research-jobs",
      writing: "writing-jobs",
      seo_aeo: "seo-aeo-jobs",
      media_plan: "media-plan-jobs",
    };
    const headers: Record<string, string> = {};
    if (idempotencyKey) {
      headers["Idempotency-Key"] = idempotencyKey;
    }
    return request<WorkerJobRecord>(
      `/api/v1/workspaces/${workspaceId}/clients/${clientId}/${endpointMap[taskType]}`,
      {
        method: "POST",
        headers,
        body: JSON.stringify(input),
      }
    );
  },

  getUsage: (workspaceId: string) =>
    request<UsageSummaryRecord>(`/api/v1/workspaces/${workspaceId}/usage`),
};
