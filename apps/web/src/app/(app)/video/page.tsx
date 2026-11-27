"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Film, Image as ImageIcon, Loader2, RefreshCw, Upload, XCircle } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { ApiClientError, apiClient } from "@/lib/api-client";
import type { ClientRecord } from "@/lib/types";
import { videoApi } from "@/lib/video-api";
import type {
  AssetRecord,
  VideoGenerationMode,
  VideoJobRecord,
  VideoJobStatus,
  VideoProviderDescriptor,
  VideoQualityPreference,
} from "@/lib/video-types";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";
import { Alert, Badge } from "@/components/ui/badge";

const TERMINAL_STATUSES = new Set<VideoJobStatus>(["succeeded", "failed", "cancelled"]);

function statusBadgeVariant(status: VideoJobStatus): "success" | "default" | "secondary" | "outline" {
  if (status === "succeeded") return "success";
  if (status === "failed") return "default";
  if (status === "cancelled") return "outline";
  return "secondary";
}

function readFileAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = typeof reader.result === "string" ? reader.result : "";
      const commaIdx = result.indexOf(",");
      resolve(commaIdx >= 0 ? result.slice(commaIdx + 1) : result);
    };
    reader.onerror = () => reject(new Error("Failed to read image file"));
    reader.readAsDataURL(file);
  });
}

export default function VideoStudioPage() {
  const { activeWorkspace } = useAuth();
  const [clients, setClients] = useState<ClientRecord[]>([]);
  const [providers, setProviders] = useState<VideoProviderDescriptor[]>([]);
  const [jobs, setJobs] = useState<VideoJobRecord[]>([]);
  const [imageAssets, setImageAssets] = useState<AssetRecord[]>([]);

  const [mode, setMode] = useState<VideoGenerationMode>("text_to_video");
  const [selectedClientId, setSelectedClientId] = useState("");
  const [selectedRefAssetId, setSelectedRefAssetId] = useState("");
  const [prompt, setPrompt] = useState("");
  const [aspectRatio, setAspectRatio] = useState("9:16");
  const [durationSeconds, setDurationSeconds] = useState(5);
  const [resolution, setResolution] = useState("720p");
  const [generateAudio, setGenerateAudio] = useState(false);
  const [qualityPreference, setQualityPreference] = useState<VideoQualityPreference>("lowest_cost");
  const [providerPreference, setProviderPreference] = useState("auto");

  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [uploadingRef, setUploadingRef] = useState(false);
  const [actingJobId, setActingJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);

  const loadStudioData = useCallback(async () => {
    if (!activeWorkspace) return;
    setLoading(true);
    setError(null);
    try {
      const [clientList, providerList, jobList, assetList] = await Promise.all([
        apiClient.listClients(activeWorkspace.workspace_id),
        videoApi.listVideoProviders(activeWorkspace.workspace_id),
        videoApi.listVideoJobs(activeWorkspace.workspace_id, 30),
        videoApi.listAssets(activeWorkspace.workspace_id, "image"),
      ]);
      setClients(clientList);
      setProviders(providerList);
      setJobs(jobList);
      setImageAssets(assetList);
      if (assetList.length > 0) {
        setSelectedRefAssetId((prev) => prev || assetList[0].id);
      }
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : "Failed to load Video Studio data.");
    } finally {
      setLoading(false);
    }
  }, [activeWorkspace]);

  useEffect(() => {
    void loadStudioData();
  }, [loadStudioData]);

  async function handleUploadReferenceImage(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file || !activeWorkspace) return;
    setUploadingRef(true);
    setError(null);
    setStatusMessage(null);
    try {
      const contentBase64 = await readFileAsBase64(file);
      const created = await videoApi.createAsset(activeWorkspace.workspace_id, {
        client_id: selectedClientId || null,
        asset_kind: "image",
        filename: file.name,
        mime_type: file.type || "image/png",
        content_base64: contentBase64,
      });
      setImageAssets((prev) => [created, ...prev]);
      setSelectedRefAssetId(created.id);
      setStatusMessage(`Uploaded reference image "${created.filename}".`);
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : "Reference image upload failed.");
    } finally {
      setUploadingRef(false);
      e.target.value = "";
    }
  }

  async function handleSubmitJob(e: React.FormEvent) {
    e.preventDefault();
    if (!activeWorkspace || !prompt.trim()) return;
    if (mode === "image_to_video" && !selectedRefAssetId) {
      setError("Image-to-video requires at least one reference image asset.");
      return;
    }

    setSubmitting(true);
    setError(null);
    setStatusMessage(null);
    try {
      const idemKey =
        typeof crypto !== "undefined" && "randomUUID" in crypto
          ? crypto.randomUUID()
          : `vid-${Date.now()}`;
      const created = await videoApi.createVideoJob(
        activeWorkspace.workspace_id,
        {
          client_id: selectedClientId || null,
          mode,
          prompt: prompt.trim(),
          reference_asset_ids: mode === "image_to_video" ? [selectedRefAssetId] : [],
          aspect_ratio: aspectRatio,
          duration_seconds: durationSeconds,
          resolution,
          audio: { generate: generateAudio, voiceover_asset_id: null },
          provider_preference: providerPreference,
          quality_preference: qualityPreference,
        },
        idemKey
      );
      setJobs((prev) => [created, ...prev.filter((j) => j.id !== created.id)]);
      setPrompt("");
      setStatusMessage(`Video job submitted via ${created.provider}/${created.model}.`);
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : "Failed to submit video job.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handlePollJob(jobId: string) {
    if (!activeWorkspace) return;
    setActingJobId(jobId);
    setError(null);
    try {
      const updated = await videoApi.pollVideoJob(activeWorkspace.workspace_id, jobId);
      setJobs((prev) => prev.map((j) => (j.id === updated.id ? updated : j)));
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : "Failed to poll job status.");
    } finally {
      setActingJobId(null);
    }
  }

  async function handleCancelJob(jobId: string) {
    if (!activeWorkspace) return;
    setActingJobId(jobId);
    setError(null);
    try {
      const updated = await videoApi.cancelVideoJob(activeWorkspace.workspace_id, jobId);
      setJobs((prev) => prev.map((j) => (j.id === updated.id ? updated : j)));
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : "Failed to cancel video job.");
    } finally {
      setActingJobId(null);
    }
  }

  if (!activeWorkspace) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Create a workspace first</CardTitle>
          <CardDescription>
            Unified video generation runs strictly inside a tenant-isolated workspace.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Link href="/onboarding/workspace">
            <Button>Create workspace</Button>
          </Link>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Video Studio</h1>
          <p className="text-sm text-muted-foreground">
            Unified provider-independent gateway for text-to-video and image-to-video jobs.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => void loadStudioData()} disabled={loading}>
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          Refresh
        </Button>
      </div>

      {error && <Alert variant="destructive">{error}</Alert>}
      {statusMessage && <Alert variant="default">{statusMessage}</Alert>}

      {/* Provider Capability Strip */}
      <div className="grid gap-3 sm:grid-cols-2">
        {providers.map((prov) => (
          <Card key={prov.provider}>
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between gap-2">
                <CardTitle className="text-sm">{prov.display_name}</CardTitle>
                <Badge variant={prov.enabled ? "success" : "outline"}>
                  {prov.enabled ? "Enabled" : "Disabled"}
                </Badge>
              </div>
              <CardDescription className="text-xs">
                Provider ID: <code>{prov.provider}</code> · Webhooks:{" "}
                {prov.webhook_support ? "Supported" : "Poll-only"}
              </CardDescription>
            </CardHeader>
            <CardContent className="flex flex-wrap gap-1.5">
              {prov.models.map((m) => (
                <Badge key={`${m.provider}-${m.model}`} variant="secondary" className="text-[11px]">
                  {m.model} (${Number(m.cost_per_second_usd).toFixed(2)}/s)
                </Badge>
              ))}
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="grid gap-6 lg:grid-cols-5">
        {/* Creation Form */}
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Film className="h-4 w-4 text-indigo-400" />
              New Video Generation Job
            </CardTitle>
            <CardDescription>
              Automatic capability routing with idempotency and per-workspace budget enforcement.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmitJob} className="space-y-4">
              <div className="grid grid-cols-2 gap-2">
                <Button
                  type="button"
                  variant={mode === "text_to_video" ? "default" : "outline"}
                  size="sm"
                  onClick={() => setMode("text_to_video")}
                >
                  <Film className="h-3.5 w-3.5" />
                  Text to Video
                </Button>
                <Button
                  type="button"
                  variant={mode === "image_to_video" ? "default" : "outline"}
                  size="sm"
                  onClick={() => setMode("image_to_video")}
                >
                  <ImageIcon className="h-3.5 w-3.5" />
                  Image to Video
                </Button>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="video-client">Client (optional)</Label>
                <select
                  id="video-client"
                  value={selectedClientId}
                  onChange={(e) => setSelectedClientId(e.target.value)}
                  className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground"
                >
                  <option value="">Workspace-level (Creator / Internal)</option>
                  {clients.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </div>

              {mode === "image_to_video" && (
                <div className="space-y-2 rounded-md border border-border bg-secondary/20 p-3">
                  <Label htmlFor="ref-asset">Reference Image Asset</Label>
                  <select
                    id="ref-asset"
                    value={selectedRefAssetId}
                    onChange={(e) => setSelectedRefAssetId(e.target.value)}
                    className="w-full rounded-md border border-border bg-background px-2.5 py-2 text-xs text-foreground"
                  >
                    <option value="">Select an uploaded image...</option>
                    {imageAssets.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.filename} ({Math.max(1, Math.round(a.size_bytes / 1024))} KB)
                      </option>
                    ))}
                  </select>
                  <label className="flex cursor-pointer items-center justify-center gap-2 rounded-md border border-dashed border-border px-3 py-2 text-xs text-muted-foreground hover:border-primary hover:text-foreground">
                    {uploadingRef ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Upload className="h-3.5 w-3.5" />
                    )}
                    <span>{uploadingRef ? "Uploading..." : "Upload new PNG/JPEG/WEBP"}</span>
                    <input
                      type="file"
                      accept="image/png,image/jpeg,image/webp"
                      className="hidden"
                      onChange={handleUploadReferenceImage}
                      disabled={uploadingRef}
                    />
                  </label>
                </div>
              )}

              <div className="space-y-1.5">
                <Label htmlFor="video-prompt">Shot Prompt</Label>
                <textarea
                  id="video-prompt"
                  rows={3}
                  required
                  minLength={3}
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  placeholder="Slow push-in on matte espresso cup with morning steam, warm golden key light..."
                  className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                />
              </div>

              <div className="grid grid-cols-3 gap-2">
                <div className="space-y-1">
                  <Label htmlFor="aspect-ratio" className="text-xs">
                    Aspect
                  </Label>
                  <select
                    id="aspect-ratio"
                    value={aspectRatio}
                    onChange={(e) => setAspectRatio(e.target.value)}
                    className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-xs"
                  >
                    <option value="9:16">9:16</option>
                    <option value="16:9">16:9</option>
                    <option value="1:1">1:1</option>
                  </select>
                </div>
                <div className="space-y-1">
                  <Label htmlFor="duration" className="text-xs">
                    Duration
                  </Label>
                  <select
                    id="duration"
                    value={durationSeconds}
                    onChange={(e) => setDurationSeconds(Number(e.target.value))}
                    className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-xs"
                  >
                    <option value={5}>5s</option>
                    <option value={8}>8s</option>
                    <option value={10}>10s</option>
                  </select>
                </div>
                <div className="space-y-1">
                  <Label htmlFor="resolution" className="text-xs">
                    Resolution
                  </Label>
                  <select
                    id="resolution"
                    value={resolution}
                    onChange={(e) => setResolution(e.target.value)}
                    className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-xs"
                  >
                    <option value="720p">720p</option>
                    <option value="1080p">1080p</option>
                  </select>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-2">
                <div className="space-y-1">
                  <Label htmlFor="quality-pref" className="text-xs">
                    Routing Strategy
                  </Label>
                  <select
                    id="quality-pref"
                    value={qualityPreference}
                    onChange={(e) =>
                      setQualityPreference(e.target.value as VideoQualityPreference)
                    }
                    className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-xs"
                  >
                    <option value="lowest_cost">Lowest Cost</option>
                    <option value="high_quality">High Quality</option>
                  </select>
                </div>
                <div className="space-y-1">
                  <Label htmlFor="provider-pref" className="text-xs">
                    Provider Pin
                  </Label>
                  <Input
                    id="provider-pref"
                    value={providerPreference}
                    onChange={(e) => setProviderPreference(e.target.value || "auto")}
                    placeholder="auto"
                    className="h-8 text-xs"
                  />
                </div>
              </div>

              <label className="flex items-center gap-2 text-xs text-muted-foreground">
                <input
                  type="checkbox"
                  checked={generateAudio}
                  onChange={(e) => setGenerateAudio(e.target.checked)}
                />
                Generate synchronized audio track (requires provider support)
              </label>

              <Button type="submit" className="w-full" disabled={submitting || !prompt.trim()}>
                {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Film className="h-4 w-4" />}
                {submitting ? "Submitting..." : "Generate Video"}
              </Button>
            </form>
          </CardContent>
        </Card>

        {/* Jobs Queue */}
        <Card className="lg:col-span-3">
          <CardHeader>
            <CardTitle className="text-base">Generation Queue ({jobs.length})</CardTitle>
            <CardDescription>
              Durable provider-independent job states. Poll active jobs or cancel in-flight renders.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {jobs.length === 0 ? (
              <p className="rounded-md border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
                No video generation jobs yet. Submit a prompt on the left to test the unified gateway.
              </p>
            ) : (
              jobs.map((job) => {
                const isTerminal = TERMINAL_STATUSES.has(job.status);
                const cost = job.actual_cost_usd ?? job.estimated_cost_usd;
                return (
                  <div
                    key={job.id}
                    className="space-y-2 rounded-lg border border-border bg-card/60 p-3"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <Badge variant={statusBadgeVariant(job.status)}>{job.status}</Badge>
                        <Badge variant="outline" className="text-[11px]">
                          {job.mode}
                        </Badge>
                        <span className="text-xs text-muted-foreground">
                          {job.provider}/{job.model}
                        </span>
                      </div>
                      <span className="text-xs font-medium text-muted-foreground">
                        {job.aspect_ratio} · {job.duration_seconds}s · {job.resolution}
                        {cost !== null ? ` · $${Number(cost).toFixed(4)}` : ""}
                      </span>
                    </div>

                    <p className="text-sm text-foreground">{job.prompt}</p>

                    <div className="space-y-1">
                      <div className="flex justify-between text-[11px] text-muted-foreground">
                        <span>Progress</span>
                        <span>{job.progress_pct}%</span>
                      </div>
                      <div className="h-1.5 w-full overflow-hidden rounded-full bg-secondary">
                        <div
                          className="h-full bg-primary transition-all"
                          style={{ width: `${Math.min(100, Math.max(0, job.progress_pct))}%` }}
                        />
                      </div>
                    </div>

                    {job.output_asset_ids.length > 0 && (
                      <p className="text-xs text-emerald-400">
                        Owned output asset(s): {job.output_asset_ids.join(", ")}
                      </p>
                    )}

                    {job.error_message && (
                      <p className="text-xs text-destructive">
                        {job.failure_category ? `[${job.failure_category}] ` : ""}
                        {job.error_message}
                      </p>
                    )}

                    {!isTerminal && (
                      <div className="flex gap-2 pt-1">
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={actingJobId === job.id}
                          onClick={() => void handlePollJob(job.id)}
                        >
                          {actingJobId === job.id ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <RefreshCw className="h-3.5 w-3.5" />
                          )}
                          Poll Status
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={actingJobId === job.id}
                          onClick={() => void handleCancelJob(job.id)}
                        >
                          <XCircle className="h-3.5 w-3.5" />
                          Cancel
                        </Button>
                      </div>
                    )}
                  </div>
                );
              })
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
