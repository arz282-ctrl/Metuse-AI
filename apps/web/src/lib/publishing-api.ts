import { webEnv } from "@/lib/env";
import { ApiClientError, getStoredAccessToken } from "@/lib/api-client";
import type { ApiErrorEnvelope } from "@/lib/types";

export interface ContentItemRecord {
  id: string;
  workspace_id: string;
  client_id: string | null;
  creative_version_id: string | null;
  meta_connection_id: string | null;
  external_account_id: string;
  media_type: string;
  caption: string;
  is_ai_generated: boolean;
  status: string;
  scheduled_for: string | null;
  published_at: string | null;
  created_at: string;
}

export interface PublishingJobRecord {
  id: string;
  content_item_id: string;
  status: string;
  remote_media_id: string | null;
  attempt_count: number;
  error_message: string | null;
  result: {
    remote_media_id: string;
    permalink: string | null;
    verified_via_remote_check: boolean;
  } | null;
}

export interface AnalyticsSummaryRecord {
  workspace_id: string;
  client_id: string | null;
  snapshot_count: number;
  total_impressions: number;
  total_reach: number;
  total_likes: number;
  total_comments: number;
  total_saves: number;
  total_shares: number;
  total_plays: number;
  latest_follower_count: number | null;
}

async function pubRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getStoredAccessToken();
  const headers = new Headers(init.headers);
  if (!headers.has("Content-Type") && init.body) {
    headers.set("Content-Type", "application/json");
  }
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  const response = await fetch(`${webEnv.apiBaseUrl}${path}`, {
    ...init,
    headers,
    credentials: "include",
  });
  const payload = response.headers.get("content-type")?.includes("application/json")
    ? ((await response.json()) as unknown)
    : null;
  if (!response.ok) {
    const env = (payload ?? {}) as ApiErrorEnvelope;
    throw new ApiClientError(
      env.error?.message || env.detail || `Request failed (${response.status})`,
      response.status,
      env.error?.code || `HTTP_${response.status}`
    );
  }
  return payload as T;
}

export const publishingApi = {
  listContent: (workspaceId: string) =>
    pubRequest<ContentItemRecord[]>(`/api/v1/workspaces/${workspaceId}/content`),

  publishContent: (workspaceId: string, contentId: string, idempotencyKey: string) =>
    pubRequest<PublishingJobRecord>(
      `/api/v1/workspaces/${workspaceId}/content/${contentId}/publish`,
      { method: "POST", headers: { "Idempotency-Key": idempotencyKey } }
    ),

  getAnalytics: (workspaceId: string) =>
    pubRequest<AnalyticsSummaryRecord>(`/api/v1/workspaces/${workspaceId}/analytics`),

  syncAnalytics: (workspaceId: string) =>
    pubRequest<unknown[]>(`/api/v1/workspaces/${workspaceId}/analytics/sync`, { method: "POST" }),
};
