"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { CheckCircle2, Clapperboard, Loader2, RefreshCw, Scissors, ShieldAlert } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { ApiClientError } from "@/lib/api-client";
import { videoApi } from "@/lib/video-api";
import type { AssetRecord, ProjectRecord } from "@/lib/video-types";
import {
  studioApi,
  type ApprovalRecord,
  type CreativeVersionRecord,
} from "@/lib/studio-api";
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

export default function CloudStudioPage() {
  const { activeWorkspace } = useAuth();
  const [projects, setProjects] = useState<ProjectRecord[]>([]);
  const [videoAssets, setVideoAssets] = useState<AssetRecord[]>([]);
  const [versions, setVersions] = useState<CreativeVersionRecord[]>([]);
  const [approvals, setApprovals] = useState<ApprovalRecord[]>([]);

  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [newProjectName, setNewProjectName] = useState("");
  const [selectedAssetId, setSelectedAssetId] = useState("");
  const [clipDurationMs, setClipDurationMs] = useState(5000);
  const [captionText, setCaptionText] = useState("Hook caption · Safe margin verified");
  const [revisionNotes, setRevisionNotes] = useState("");

  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadBase = useCallback(async () => {
    if (!activeWorkspace) return;
    setLoading(true);
    setError(null);
    try {
      const [projList, assetList] = await Promise.all([
        studioApi.listProjects(activeWorkspace.workspace_id),
        videoApi.listAssets(activeWorkspace.workspace_id, "video"),
      ]);
      setProjects(projList);
      setVideoAssets(assetList);
      if (projList.length > 0) {
        setSelectedProjectId((prev) => prev || projList[0].id);
      }
      if (assetList.length > 0) {
        setSelectedAssetId((prev) => prev || assetList[0].id);
      }
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : "Failed to load Cloud Studio.");
    } finally {
      setLoading(false);
    }
  }, [activeWorkspace]);

  const loadProjectState = useCallback(async () => {
    if (!activeWorkspace || !selectedProjectId) return;
    try {
      const [verList, apprList] = await Promise.all([
        studioApi.listVersions(activeWorkspace.workspace_id, selectedProjectId),
        studioApi.listApprovals(activeWorkspace.workspace_id, selectedProjectId),
      ]);
      setVersions(verList);
      setApprovals(apprList);
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : "Failed to load project versions.");
    }
  }, [activeWorkspace, selectedProjectId]);

  useEffect(() => {
    void loadBase();
  }, [loadBase]);

  useEffect(() => {
    void loadProjectState();
  }, [loadProjectState]);

  async function handleCreateProject(e: React.FormEvent) {
    e.preventDefault();
    if (!activeWorkspace || !newProjectName.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const created = await studioApi.createProject(
        activeWorkspace.workspace_id,
        newProjectName.trim()
      );
      setProjects((prev) => [created, ...prev]);
      setSelectedProjectId(created.id);
      setNewProjectName("");
      setNotice(`Project "${created.name}" created.`);
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : "Could not create project.");
    } finally {
      setBusy(false);
    }
  }

  async function handleSavePlanAndRender(kind: "preview" | "final") {
    if (!activeWorkspace || !selectedProjectId || !selectedAssetId) {
      setError("Select a project and at least one video clip asset first.");
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const planPayload = {
        schema_version: "1",
        project_id: selectedProjectId,
        canvas: { width: 1080, height: 1920, fps: 30 },
        tracks: [
          {
            id: "video-main",
            type: "video",
            clips: [
              {
                asset_id: selectedAssetId,
                source_in_ms: 0,
                source_out_ms: clipDurationMs,
                timeline_start_ms: 0,
                fit: "cover",
              },
            ],
          },
          {
            id: "captions",
            type: "text",
            items: captionText.trim()
              ? [
                  {
                    text: captionText.trim(),
                    start_ms: 0,
                    end_ms: Math.min(clipDurationMs, 3000),
                    x_norm: 0.5,
                    y_norm: 0.75,
                    is_cta: false,
                  },
                ]
              : [],
          },
        ],
        export: { container: "mp4", video_codec: "h264", audio_codec: "aac" },
      };

      await studioApi.createEditingPlan(activeWorkspace.workspace_id, selectedProjectId, planPayload);
      const idemKey =
        typeof crypto !== "undefined" && "randomUUID" in crypto
          ? crypto.randomUUID()
          : `render-${Date.now()}`;
      const res =
        kind === "preview"
          ? await studioApi.renderPreview(activeWorkspace.workspace_id, selectedProjectId, idemKey)
          : await studioApi.renderFinal(activeWorkspace.workspace_id, selectedProjectId, idemKey);

      if (res.render_job.qa_passed) {
        setNotice(`Cloud ${kind} render passed automated Media QA (v${res.creative_version?.version_number}).`);
      } else {
        setError(res.render_job.error_message || "Automated Media QA rejected rendered output.");
      }
      await loadProjectState();
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : `Failed to render ${kind}.`);
    } finally {
      setBusy(false);
    }
  }

  async function handleRequestApproval(versionId: string) {
    if (!activeWorkspace) return;
    setBusy(true);
    setError(null);
    try {
      await studioApi.createApproval(activeWorkspace.workspace_id, versionId);
      setNotice("Version-bound approval request submitted.");
      await loadProjectState();
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : "Approval creation failed.");
    } finally {
      setBusy(false);
    }
  }

  async function handleApprove(approvalId: string) {
    if (!activeWorkspace) return;
    setBusy(true);
    setError(null);
    try {
      await studioApi.approve(activeWorkspace.workspace_id, approvalId, "Approved in Cloud Studio");
      setNotice("Creative version approved (hash-bound).");
      await loadProjectState();
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : "Could not approve version.");
    } finally {
      setBusy(false);
    }
  }

  async function handleRequestRevision(approvalId: string) {
    if (!activeWorkspace || !revisionNotes.trim()) {
      setError("Enter revision notes before requesting a revision.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await studioApi.requestRevision(
        activeWorkspace.workspace_id,
        approvalId,
        revisionNotes.trim()
      );
      setRevisionNotes("");
      setNotice("Revision requested; prior approval cleared.");
      await loadProjectState();
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : "Could not request revision.");
    } finally {
      setBusy(false);
    }
  }

  if (!activeWorkspace) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Create a workspace first</CardTitle>
          <CardDescription>
            Cloud editing, automated QA, and version-bound approvals run inside a workspace.
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
          <h1 className="text-2xl font-semibold tracking-tight">Cloud Studio & Approvals</h1>
          <p className="text-sm text-muted-foreground">
            Backend timeline editing, Ucuts/FFmpeg isolated rendering, automated Media QA, and version-bound sign-off.
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => void Promise.all([loadBase(), loadProjectState()])}
          disabled={loading}
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          Refresh
        </Button>
      </div>

      {error && <Alert variant="destructive">{error}</Alert>}
      {notice && <Alert variant="default">{notice}</Alert>}

      <div className="grid gap-6 lg:grid-cols-5">
        {/* Left: Project + Timeline + Render Controls */}
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Scissors className="h-4 w-4 text-indigo-400" />
              Project & Timeline Plan
            </CardTitle>
            <CardDescription>
              Editing a plan automatically invalidates prior approvals on the project.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <form onSubmit={handleCreateProject} className="flex gap-2">
              <Input
                value={newProjectName}
                onChange={(e) => setNewProjectName(e.target.value)}
                placeholder="New project name..."
              />
              <Button type="submit" size="sm" disabled={busy || !newProjectName.trim()}>
                Create
              </Button>
            </form>

            <div className="space-y-1.5">
              <Label htmlFor="studio-project">Active Project</Label>
              <select
                id="studio-project"
                value={selectedProjectId}
                onChange={(e) => setSelectedProjectId(e.target.value)}
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
              >
                <option value="">Select a project...</option>
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} ({p.slug})
                  </option>
                ))}
              </select>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="studio-asset">Source Video Asset (Upload or Generated)</Label>
              <select
                id="studio-asset"
                value={selectedAssetId}
                onChange={(e) => setSelectedAssetId(e.target.value)}
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
              >
                <option value="">Select a video clip...</option>
                {videoAssets.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.filename} ({a.source_type})
                  </option>
                ))}
              </select>
            </div>

            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <Label htmlFor="clip-dur" className="text-xs">
                  Duration (ms)
                </Label>
                <Input
                  id="clip-dur"
                  type="number"
                  min={1000}
                  max={60000}
                  step={500}
                  value={clipDurationMs}
                  onChange={(e) => setClipDurationMs(Number(e.target.value))}
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="caption-txt" className="text-xs">
                  Safe-Margin Caption
                </Label>
                <Input
                  id="caption-txt"
                  value={captionText}
                  onChange={(e) => setCaptionText(e.target.value)}
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-2 pt-2">
              <Button
                type="button"
                variant="outline"
                disabled={busy || !selectedProjectId || !selectedAssetId}
                onClick={() => void handleSavePlanAndRender("preview")}
              >
                <Clapperboard className="h-4 w-4" />
                Render Preview
              </Button>
              <Button
                type="button"
                disabled={busy || !selectedProjectId || !selectedAssetId}
                onClick={() => void handleSavePlanAndRender("final")}
              >
                <CheckCircle2 className="h-4 w-4" />
                Render Final MP4
              </Button>
            </div>
          </CardContent>
        </Card>

        {/* Right: Versions & Version-Bound Approvals */}
        <div className="space-y-6 lg:col-span-3">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Creative Versions ({versions.length})</CardTitle>
              <CardDescription>
                Every render runs Section 7.5 automated Media QA before becoming reviewable.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {versions.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No rendered versions yet for this project.
                </p>
              ) : (
                versions.map((ver) => (
                  <div
                    key={ver.id}
                    className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border p-3"
                  >
                    <div className="space-y-1">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium">Version v{ver.version_number}</span>
                        <Badge variant="secondary">{ver.render_kind}</Badge>
                        <Badge variant={ver.qa_passed ? "success" : "outline"}>
                          {ver.qa_passed ? "QA Passed" : "QA Failed"}
                        </Badge>
                        {ver.is_approved && <Badge variant="success">Approved</Badge>}
                      </div>
                      <p className="text-xs text-muted-foreground">
                        SHA-256: <code>{ver.content_sha256.slice(0, 16)}…</code> · Asset:{" "}
                        <code>{ver.output_asset_id.slice(0, 8)}</code>
                      </p>
                    </div>
                    {!ver.is_approved && ver.qa_passed && (
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={busy}
                        onClick={() => void handleRequestApproval(ver.id)}
                      >
                        Request Approval
                      </Button>
                    )}
                  </div>
                ))
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <ShieldAlert className="h-4 w-4 text-amber-400" />
                Version-Bound Approvals ({approvals.length})
              </CardTitle>
              <CardDescription>
                Approvals bind to exact SHA-256 content hashes and auto-invalidate on edits.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="space-y-1">
                <Label htmlFor="rev-notes" className="text-xs">
                  Revision Notes (used when requesting changes)
                </Label>
                <Input
                  id="rev-notes"
                  value={revisionNotes}
                  onChange={(e) => setRevisionNotes(e.target.value)}
                  placeholder="Tighten opening hook by 1.5s and brighten caption contrast..."
                />
              </div>

              {approvals.length === 0 ? (
                <p className="text-sm text-muted-foreground">No approval records yet.</p>
              ) : (
                approvals.map((appr) => (
                  <div key={appr.id} className="space-y-2 rounded-lg border border-border p-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <Badge
                          variant={
                            appr.status === "approved"
                              ? "success"
                              : appr.status === "pending"
                                ? "secondary"
                                : "outline"
                          }
                        >
                          {appr.status}
                        </Badge>
                        <span className="text-xs text-muted-foreground">
                          {appr.approval_type} · hash{" "}
                          <code>{appr.target_version_sha256.slice(0, 12)}</code>
                        </span>
                      </div>
                      {appr.status === "pending" && (
                        <div className="flex gap-2">
                          <Button
                            size="sm"
                            disabled={busy}
                            onClick={() => void handleApprove(appr.id)}
                          >
                            Approve
                          </Button>
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={busy}
                            onClick={() => void handleRequestRevision(appr.id)}
                          >
                            Request Revision
                          </Button>
                        </div>
                      )}
                    </div>
                    {appr.invalidated_reason && (
                      <p className="text-xs text-amber-400">
                        Invalidated: {appr.invalidated_reason}
                      </p>
                    )}
                    {appr.decision_notes && (
                      <p className="text-xs text-muted-foreground">Notes: {appr.decision_notes}</p>
                    )}
                  </div>
                ))
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
