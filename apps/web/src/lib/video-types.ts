/**
 * Phase 3 Unified Video Generation Gateway & Tenant Asset types.
 * Mirrors packages/video_gateway/contracts.py and packages/shared/enums.py.
 */

export type VideoGenerationMode = "text_to_video" | "image_to_video";

export type VideoJobStatus =
  | "queued"
  | "validating"
  | "submitted"
  | "processing"
  | "succeeded"
  | "failed"
  | "cancelled";

export type VideoFailureCategory =
  | "validation"
  | "capability"
  | "rate_limited"
  | "provider_error"
  | "timeout"
  | "cancelled"
  | "budget";

export type VideoQualityPreference = "lowest_cost" | "high_quality";

export type VideoQualityTier = "standard" | "high";

export type AssetKind = "image" | "video" | "audio" | "document";

export type AssetSourceType = "upload" | "video_generation" | "brand" | "render";

export interface VideoAudioConfig {
  generate: boolean;
  voiceover_asset_id?: string | null;
}

export interface VideoModelCapability {
  provider: string;
  model: string;
  modes: VideoGenerationMode[];
  aspect_ratios: string[];
  durations_seconds: number[];
  resolutions: string[];
  max_reference_images: number;
  audio_generation: boolean;
  webhook_support: boolean;
  regions: string[];
  cost_per_second_usd: string | number;
  quality_tier: VideoQualityTier;
  enabled: boolean;
}

export interface VideoProviderDescriptor {
  provider: string;
  display_name: string;
  enabled: boolean;
  webhook_support: boolean;
  models: VideoModelCapability[];
}

export interface VideoJobRecord {
  id: string;
  workspace_id: string;
  client_id: string | null;
  project_id: string | null;
  actor_user_id: string;
  mode: VideoGenerationMode;
  provider: string;
  model: string;
  provider_job_id: string | null;
  status: VideoJobStatus;
  progress_pct: number;
  idempotency_key: string | null;
  prompt: string;
  aspect_ratio: string;
  duration_seconds: number;
  resolution: string;
  provider_preference: string;
  input_asset_ids: string[];
  output_asset_ids: string[];
  failure_category: VideoFailureCategory | null;
  provider_error_code: string | null;
  error_message: string | null;
  poll_attempts: number;
  next_poll_at: string | null;
  estimated_cost_usd: string | number | null;
  actual_cost_usd: string | number | null;
  submitted_at: string | null;
  completed_at: string | null;
  cancelled_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface AssetRecord {
  id: string;
  workspace_id: string;
  client_id: string | null;
  project_id: string | null;
  asset_kind: AssetKind;
  source_type: AssetSourceType;
  filename: string;
  mime_type: string;
  size_bytes: number;
  storage_bucket: string;
  storage_key: string;
  duration_seconds: string | number | null;
  width: number | null;
  height: number | null;
  sha256: string | null;
  status: string;
  created_by_user_id: string;
  created_at: string;
  updated_at: string;
}

export interface ProjectRecord {
  id: string;
  workspace_id: string;
  client_id: string | null;
  name: string;
  slug: string;
  status: string;
  created_by_user_id: string;
  created_at: string;
  updated_at: string;
}

export interface CreateVideoJobInput {
  client_id?: string | null;
  project_id?: string | null;
  mode: VideoGenerationMode;
  prompt: string;
  reference_asset_ids?: string[];
  aspect_ratio?: string;
  duration_seconds?: number;
  resolution?: string;
  audio?: VideoAudioConfig;
  brand_constraints?: Record<string, unknown>;
  provider_preference?: string;
  model_preference?: string | null;
  quality_preference?: VideoQualityPreference;
}

export interface CreateAssetInput {
  client_id?: string | null;
  project_id?: string | null;
  asset_kind: AssetKind;
  filename: string;
  mime_type: string;
  content_base64: string;
  width?: number | null;
  height?: number | null;
  metadata?: Record<string, unknown>;
}
