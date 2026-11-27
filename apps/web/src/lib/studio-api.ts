import { webEnv } from "@/lib/env";
import { ApiClientError, getStoredAccessToken } from "@/lib/api-client";
import type { ApiErrorEnvelope } from "@/lib/types";
import type { ProjectRecord } from "@/lib/video-types";

export interface CreativeVersionRecord {
  id: string;
  workspace_id: string;
  project_id: string;
  editing_plan_id: string;
  render_job_id: string;
  output_asset_id: string;
  version_number: number;
  render_kind: "preview" | "final";
  content_sha256: string;
  qa_passed: boolean;
  qa_report: { passed?: boolean; failed_checks?: string[] };
  is_approved: boolean;
  created_at: string;
}

export interface RenderJobRecord {
  id: string;
  project_id: string;
  editing_plan_id: string;
  render_kind: "preview" | "final";
  status: string;
  renderer: string;
  output_asset_id: string | null;
  qa_passed: boolean | null;
  error_message: string | null;
  estimated_cost_usd: string | number | null;
  created_at: string;
}

export interface ApprovalRecord {
  id: string;
  workspace_id: string;
  project_id: string | null;
  creative_version_id: string | null;
  approval_type: string;
  status: "pending" | "approved" | "rejected" | "revision_requested" | "invalidated";
  target_version_sha256: string;
  decision_notes: string | null;
  invalidated_reason: string | null;
  created_at: string;
}

async function studioRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
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

export const studioApi = {
  createProject: (workspaceId: string, name: string, clientId?: string | null) =>
    studioRequest<ProjectRecord>(`/api/v1/workspaces/${workspaceId}/projects`, {
      method: "POST",
      body: JSON.stringify({ name, client_id: clientId || null }),
    }),

  listProjects: (workspaceId: string) =>
    studioRequest<ProjectRecord[]>(`/api/v1/workspaces/${workspaceId}/projects`),

  createEditingPlan: (
    workspaceId: string,
    projectId: string,
    plan: Record<string, unknown>
  ) =>
    studioRequest<{ id: string; version_number: number }>(
      `/api/v1/workspaces/${workspaceId}/projects/${projectId}/editing-plan`,
      { method: "POST", body: JSON.stringify(plan) }
    ),

  renderPreview: (workspaceId: string, projectId: string, idempotencyKey: string) =>
    studioRequest<{ render_job: RenderJobRecord; creative_version: CreativeVersionRecord | null }>(
      `/api/v1/workspaces/${workspaceId}/projects/${projectId}/render-preview`,
      {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey },
        body: JSON.stringify({}),
      }
    ),

  renderFinal: (workspaceId: string, projectId: string, idempotencyKey: string) =>
    studioRequest<{ render_job: RenderJobRecord; creative_version: CreativeVersionRecord | null }>(
      `/api/v1/workspaces/${workspaceId}/projects/${projectId}/render-final`,
      {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey },
        body: JSON.stringify({}),
      }
    ),

  listVersions: (workspaceId: string, projectId: string) =>
    studioRequest<CreativeVersionRecord[]>(
      `/api/v1/workspaces/${workspaceId}/projects/${projectId}/versions`
    ),

  listApprovals: (workspaceId: string, projectId?: string) => {
    const qs = projectId ? `?project_id=${encodeURIComponent(projectId)}` : "";
    return studioRequest<ApprovalRecord[]>(`/api/v1/workspaces/${workspaceId}/approvals${qs}`);
  },

  createApproval: (workspaceId: string, creativeVersionId: string) =>
    studioRequest<ApprovalRecord>(`/api/v1/workspaces/${workspaceId}/approvals`, {
      method: "POST",
      body: JSON.stringify({
        creative_version_id: creativeVersionId,
        approval_type: "creative",
      }),
    }),

  approve: (workspaceId: string, approvalId: string, notes?: string) =>
    studioRequest<ApprovalRecord>(
      `/api/v1/workspaces/${workspaceId}/approvals/${approvalId}/approve`,
      { method: "POST", body: JSON.stringify({ notes: notes || null }) }
    ),

  requestRevision: (workspaceId: string, approvalId: string, notes: string) =>
    studioRequest<{ approval: ApprovalRecord }>(
      `/api/v1/workspaces/${workspaceId}/approvals/${approvalId}/request-revision`,
      { method: "POST", body: JSON.stringify({ notes }) }
    ),
};
