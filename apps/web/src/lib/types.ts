export type WorkspaceType = "agency" | "creator";
export type ClientStatus = "active" | "paused" | "archived";
export type MemberStatus = "active" | "invited" | "suspended" | "removed";
export type RoleName =
  | "agency_owner"
  | "agency_admin"
  | "agency_member"
  | "creator_owner"
  | "client_reviewer"
  | "client_viewer";

export interface AuthUser {
  id: string;
  email: string;
  full_name: string | null;
  avatar_url: string | null;
  auth_source: string;
}

export interface AuthTokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: AuthUser;
}

export interface MembershipSummary {
  workspace_id: string;
  workspace_name: string;
  workspace_type: WorkspaceType;
  role_name: RoleName;
  status: MemberStatus;
}

export interface Workspace {
  id: string;
  name: string;
  slug: string;
  workspace_type: WorkspaceType;
  max_members: number;
  created_by_user_id: string;
  created_at: string;
  updated_at: string;
}

export interface ClientRecord {
  id: string;
  workspace_id: string;
  name: string;
  slug: string;
  status: ClientStatus;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface AuditLogRecord {
  id: string;
  workspace_id: string | null;
  client_id: string | null;
  actor_user_id: string | null;
  action: string;
  target_type: string;
  target_id: string | null;
  request_id: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface AuditLogListResponse {
  items: AuditLogRecord[];
  total: number;
}

export interface ApiErrorEnvelope {
  error?: {
    code?: string;
    message?: string;
    details?: Record<string, unknown>;
    request_id?: string;
  };
  detail?: string;
}
