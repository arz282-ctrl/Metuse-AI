"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { BarChart3, CalendarClock, Loader2, RefreshCw, Send } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { ApiClientError } from "@/lib/api-client";
import {
  publishingApi,
  type AnalyticsSummaryRecord,
  type ContentItemRecord,
} from "@/lib/publishing-api";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Alert, Badge } from "@/components/ui/badge";

export default function PublishingAndAnalyticsPage() {
  const { activeWorkspace } = useAuth();
  const [items, setItems] = useState<ContentItemRecord[]>([]);
  const [analytics, setAnalytics] = useState<AnalyticsSummaryRecord | null>(null);
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    if (!activeWorkspace) return;
    setLoading(true);
    setError(null);
    try {
      const [contentList, summary] = await Promise.all([
        publishingApi.listContent(activeWorkspace.workspace_id),
        publishingApi.getAnalytics(activeWorkspace.workspace_id),
      ]);
      setItems(contentList);
      setAnalytics(summary);
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : "Failed to load publishing data.");
    } finally {
      setLoading(false);
    }
  }, [activeWorkspace]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  async function handlePublish(contentId: string) {
    if (!activeWorkspace) return;
    setBusyId(contentId);
    setError(null);
    setNotice(null);
    try {
      const idemKey =
        typeof crypto !== "undefined" && "randomUUID" in crypto
          ? crypto.randomUUID()
          : `pub-${Date.now()}`;
      const job = await publishingApi.publishContent(
        activeWorkspace.workspace_id,
        contentId,
        idemKey
      );
      setNotice(
        job.remote_media_id
          ? `Published cleanly to Instagram (remote ID: ${job.remote_media_id}).`
          : `Publishing job status: ${job.status}.`
      );
      await loadData();
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : "Publishing failed.");
    } finally {
      setBusyId(null);
    }
  }

  async function handleSyncInsights() {
    if (!activeWorkspace) return;
    setSyncing(true);
    setError(null);
    try {
      await publishingApi.syncAnalytics(activeWorkspace.workspace_id);
      setNotice("Synced organic Instagram account & media insights.");
      await loadData();
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : "Insights sync failed.");
    } finally {
      setSyncing(false);
    }
  }

  if (!activeWorkspace) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Create a workspace first</CardTitle>
          <CardDescription>
            Approved publishing and insights attribution require an active workspace.
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
          <h1 className="text-2xl font-semibold tracking-tight">Publishing & Insights</h1>
          <p className="text-sm text-muted-foreground">
            Approval-gated Instagram publishing with zero-duplicate retry verification and synced organic insights.
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => void handleSyncInsights()} disabled={syncing}>
            {syncing ? <Loader2 className="h-4 w-4 animate-spin" /> : <BarChart3 className="h-4 w-4" />}
            Sync Insights
          </Button>
          <Button variant="outline" size="sm" onClick={() => void loadData()} disabled={loading}>
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            Refresh
          </Button>
        </div>
      </div>

      {error && <Alert variant="destructive">{error}</Alert>}
      {notice && <Alert variant="default">{notice}</Alert>}

      {/* Insights Summary Strip */}
      <div className="grid gap-3 sm:grid-cols-4">
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Total Reach</CardDescription>
            <CardTitle className="text-xl">{analytics?.total_reach ?? 0}</CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Impressions</CardDescription>
            <CardTitle className="text-xl">{analytics?.total_impressions ?? 0}</CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Plays / Views</CardDescription>
            <CardTitle className="text-xl">{analytics?.total_plays ?? 0}</CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Saves & Shares</CardDescription>
            <CardTitle className="text-xl">
              {(analytics?.total_saves ?? 0) + (analytics?.total_shares ?? 0)}
            </CardTitle>
          </CardHeader>
        </Card>
      </div>

      {/* Content Calendar & Publishing Queue */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <CalendarClock className="h-4 w-4 text-indigo-400" />
            Content Queue ({items.length})
          </CardTitle>
          <CardDescription>
            Only explicitly approved, QA-passed creative versions can be scheduled or published.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {items.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No content items queued yet. Approve a creative version in Cloud Studio and connect an Instagram Professional account to publish.
            </p>
          ) : (
            items.map((item) => (
              <div
                key={item.id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border p-3"
              >
                <div className="space-y-1">
                  <div className="flex items-center gap-2">
                    <Badge variant={item.status === "published" ? "success" : "secondary"}>
                      {item.status}
                    </Badge>
                    <Badge variant="outline">{item.media_type}</Badge>
                    {item.is_ai_generated && <Badge variant="outline">AI Disclosed</Badge>}
                  </div>
                  <p className="text-sm text-foreground">{item.caption || "(No caption)"}</p>
                  <p className="text-xs text-muted-foreground">
                    Account: <code>{item.external_account_id}</code>
                    {item.scheduled_for ? ` · Scheduled: ${item.scheduled_for}` : ""}
                  </p>
                </div>
                {item.status !== "published" && (
                  <Button
                    size="sm"
                    disabled={busyId === item.id}
                    onClick={() => void handlePublish(item.id)}
                  >
                    {busyId === item.id ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Send className="h-3.5 w-3.5" />
                    )}
                    Publish Now
                  </Button>
                )}
              </div>
            ))
          )}
        </CardContent>
      </Card>
    </div>
  );
}
