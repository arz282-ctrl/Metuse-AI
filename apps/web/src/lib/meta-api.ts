import { webEnv } from "@/lib/env";
import { ApiClientError, getStoredAccessToken } from "@/lib/api-client";
import type { ApiErrorEnvelope } from "@/lib/types";

export type MetaConnectionType =
  | "agency_business"
  | "creator_professional"
  | "page_only"
  | "ads_only";

export interface PermissionMatrixEntry {
  capability: string;
  permission: string;
  description: string;
  required_for_creator_organic: boolean;
  required_for_agency_ads: boolean;
  app_review_tier: string;
}

export interface MetaConnectionRecord {
  id: string;
  workspace_id: string;
  client_id: string | null;
  connection_type: MetaConnectionType;
  meta_business_id: string | null;
  config_id: string;
  token_configured: boolean;
  token_type: string;
  granted_scopes: string[];
  instagram_account_ids: string[];
  page_ids: string[];
  ad_account_ids: string[];
  capabilities: Record<string, boolean>;
  status: "active" | "reconnect_required" | "revoked" | "disconnected";
  reconnect_reason: string | null;
  created_at: string;
}

export interface MetaConnectedAssetRecord {
  id: string;
  asset_type: string;
  external_asset_id: string;
  display_name: string;
  username: string | null;
  instagram_account_type: string | null;
  status: string;
}

export interface UnifiedLoginStartResponse {
  authorization_url: string;
  state: string;
  redirect_uri: string;
  connection_type: MetaConnectionType;
  requested_scopes: string[];
  config_id: string;
  mode: string;
}

async function metaRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
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
      `Unable to reach Metuse API at ${webEnv.apiBaseUrl}.`,
      0,
      "NETWORK_ERROR"
    );
  }

  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? ((await response.json()) as unknown)
    : null;

  if (!response.ok) {
    const env = (payload ?? {}) as ApiErrorEnvelope;
    const message = env.error?.message || env.detail || `Request failed (${response.status})`;
    const code = env.error?.code || `HTTP_${response.status}`;
    throw new ApiClientError(message, response.status, code);
  }
  return payload as T;
}

export const metaApi = {
  getPermissionMatrix: (workspaceId: string) =>
    metaRequest<PermissionMatrixEntry[]>(
      `/api/v1/workspaces/${workspaceId}/meta/permission-matrix`
    ),

  listConnections: (workspaceId: string) =>
    metaRequest<MetaConnectionRecord[]>(`/api/v1/workspaces/${workspaceId}/meta/connections`),

  listAssets: (workspaceId: string, connectionId: string) =>
    metaRequest<MetaConnectedAssetRecord[]>(
      `/api/v1/workspaces/${workspaceId}/meta/connections/${connectionId}/assets`
    ),

  startUnifiedLogin: (
    workspaceId: string,
    payload: {
      connection_type: MetaConnectionType;
      client_id?: string | null;
      include_ads?: boolean;
    }
  ) =>
    metaRequest<UnifiedLoginStartResponse>(
      `/api/v1/workspaces/${workspaceId}/meta/connections/unified-login/start`,
      { method: "POST", body: JSON.stringify(payload) }
    ),

  completeUnifiedLogin: (
    workspaceId: string,
    payload: { code: string; state: string; redirect_uri?: string }
  ) =>
    metaRequest<MetaConnectionRecord>(
      `/api/v1/workspaces/${workspaceId}/meta/connections/unified-login/callback`,
      { method: "POST", body: JSON.stringify(payload) }
    ),

  disconnect: (workspaceId: string, connectionId: string) =>
    metaRequest<MetaConnectionRecord>(
      `/api/v1/workspaces/${workspaceId}/meta/connections/${connectionId}`,
      { method: "DELETE" }
    ),
};
