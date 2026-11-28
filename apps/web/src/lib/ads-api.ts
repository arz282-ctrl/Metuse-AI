import { webEnv } from "@/lib/env";
import { ApiClientError, getStoredAccessToken } from "@/lib/api-client";
import type { ApiErrorEnvelope } from "@/lib/types";

export interface AdAccountRecord {
  id: string;
  workspace_id: string;
  client_id: string | null;
  meta_connection_id: string;
  external_account_id: string;
  name: string;
  currency: string;
  timezone_name: string;
  account_status: string;
  spend_cap_cents: number | null;
  amount_spent_cents: number;
  last_synced_at: string | null;
}

export interface AdSnapshotRecord {
  id: string;
  external_ad_id: string;
  name: string;
  status: string;
  spend_cents: number;
  impressions: number;
  clicks: number;
}

export interface AdSetSnapshotRecord {
  id: string;
  external_ad_set_id: string;
  name: string;
  status: string;
  optimization_goal: string;
  daily_budget_cents: number | null;
  spend_cents: number;
  impressions: number;
  clicks: number;
  ads: AdSnapshotRecord[];
}

export interface AdCampaignSnapshotRecord {
  id: string;
  ad_account_id: string;
  external_campaign_id: string;
  name: string;
  objective: string;
  status: string;
  daily_budget_cents: number | null;
  spend_cents: number;
  impressions: number;
  clicks: number;
  conversions: number;
  captured_at: string;
  ad_sets: AdSetSnapshotRecord[];
}

export interface AdDraftRecord {
  id: string;
  ad_account_id: string;
  name: string;
  objective: string;
  status: string;
  daily_budget_cents: number;
  targeting: Record<string, unknown>;
  creative_refs: string[];
  version_number: number;
  content_hash: string;
  validation_passed: boolean;
  validation_report: {
    passed?: boolean;
    errors?: string[];
    warnings?: string[];
  };
  approval_id: string | null;
  remote_campaign_id: string | null;
  executed_at: string | null;
}

async function adsRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
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

export const adsApi = {
  listAccounts: (workspaceId: string) =>
    adsRequest<AdAccountRecord[]>(`/api/v1/workspaces/${workspaceId}/ads/accounts`),

  syncAds: (workspaceId: string) =>
    adsRequest<{ accounts_synced: number; campaigns_synced: number }>(
      `/api/v1/workspaces/${workspaceId}/ads/sync`,
      { method: "POST" }
    ),

  listCampaigns: (workspaceId: string) =>
    adsRequest<AdCampaignSnapshotRecord[]>(`/api/v1/workspaces/${workspaceId}/ads/campaigns`),

  listDrafts: (workspaceId: string) =>
    adsRequest<AdDraftRecord[]>(`/api/v1/workspaces/${workspaceId}/ads/drafts`),

  createDraft: (
    workspaceId: string,
    payload: {
      ad_account_id: string;
      name: string;
      objective: string;
      daily_budget_cents: number;
      targeting: Record<string, unknown>;
      creative_refs: string[];
    }
  ) =>
    adsRequest<AdDraftRecord>(`/api/v1/workspaces/${workspaceId}/ads/drafts`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  validateDraft: (workspaceId: string, draftId: string) =>
    adsRequest<AdDraftRecord>(`/api/v1/workspaces/${workspaceId}/ads/drafts/${draftId}/validate`, {
      method: "POST",
    }),

  requestDraftApproval: (workspaceId: string, draftId: string) =>
    adsRequest<{ approval_id: string; status: string }>(
      `/api/v1/workspaces/${workspaceId}/ads/drafts/${draftId}/request-approval`,
      { method: "POST" }
    ),

  executeDraft: (workspaceId: string, draftId: string, idempotencyKey: string) =>
    adsRequest<{ draft: AdDraftRecord; verified_via_remote_check: boolean }>(
      `/api/v1/workspaces/${workspaceId}/ads/drafts/${draftId}/execute`,
      { method: "POST", headers: { "Idempotency-Key": idempotencyKey } }
    ),
};
