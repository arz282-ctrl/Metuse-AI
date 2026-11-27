import { webEnv } from "@/lib/env";
import { ApiClientError, getStoredAccessToken } from "@/lib/api-client";
import type { ApiErrorEnvelope } from "@/lib/types";
import type {
  AssetKind,
  AssetRecord,
  CreateAssetInput,
  CreateVideoJobInput,
  ProjectRecord,
  VideoJobRecord,
  VideoModelCapability,
  VideoProviderDescriptor,
} from "@/lib/video-types";

async function videoRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
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
      env.error?.message || env.detail || `Request failed with status ${response.status}`;
    const code = env.error?.code || `HTTP_${response.status}`;
    const requestId = env.error?.request_id || response.headers.get("X-Request-Id");
    throw new ApiClientError(message, response.status, code, requestId);
  }

  return payload as T;
}

export const videoApi = {
  listVideoProviders: (workspaceId: string) =>
    videoRequest<VideoProviderDescriptor[]>(`/api/v1/workspaces/${workspaceId}/video/providers`),

  listVideoModels: (workspaceId: string, enabledOnly = false) =>
    videoRequest<VideoModelCapability[]>(
      `/api/v1/workspaces/${workspaceId}/video/models?enabled_only=${enabledOnly ? "true" : "false"}`
    ),

  listVideoJobs: (workspaceId: string, limit = 30) =>
    videoRequest<VideoJobRecord[]>(`/api/v1/workspaces/${workspaceId}/video/jobs?limit=${limit}`),

  createVideoJob: (
    workspaceId: string,
    payload: CreateVideoJobInput,
    idempotencyKey: string
  ) =>
    videoRequest<VideoJobRecord>(`/api/v1/workspaces/${workspaceId}/video/jobs`, {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify(payload),
    }),

  pollVideoJob: (workspaceId: string, jobId: string) =>
    videoRequest<VideoJobRecord>(`/api/v1/workspaces/${workspaceId}/video/jobs/${jobId}/poll`, {
      method: "POST",
    }),

  cancelVideoJob: (workspaceId: string, jobId: string) =>
    videoRequest<VideoJobRecord>(`/api/v1/workspaces/${workspaceId}/video/jobs/${jobId}/cancel`, {
      method: "POST",
    }),

  listAssets: (workspaceId: string, assetKind?: AssetKind) => {
    const qs = assetKind ? `?asset_kind=${encodeURIComponent(assetKind)}` : "";
    return videoRequest<AssetRecord[]>(`/api/v1/workspaces/${workspaceId}/assets${qs}`);
  },

  createAsset: (workspaceId: string, payload: CreateAssetInput) =>
    videoRequest<AssetRecord>(`/api/v1/workspaces/${workspaceId}/assets`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  listProjects: (workspaceId: string) =>
    videoRequest<ProjectRecord[]>(`/api/v1/workspaces/${workspaceId}/projects`),
};
