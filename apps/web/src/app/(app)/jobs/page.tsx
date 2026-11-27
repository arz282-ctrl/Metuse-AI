"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Loader2, Play, Sparkles } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { ApiClientError, apiClient } from "@/lib/api-client";
import type {
  ClientRecord,
  UsageSummaryRecord,
  WorkerJobRecord,
  WorkerTaskType,
} from "@/lib/types";
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

const TASK_OPTIONS: { value: WorkerTaskType; label: string; skill: string }[] = [
  { value: "research", label: "Research Report", skill: "research-pack" },
  { value: "writing", label: "Copy & Scripts", skill: "marketing-seo-pack" },
  { value: "seo_aeo", label: "SEO & AEO Passages", skill: "marketing-seo-pack" },
  { value: "media_plan", label: "Media Plan & Storyboard", skill: "media-pack" },
];

export default function AiJobsPage() {
  const { activeWorkspace } = useAuth();
  const [clients, setClients] = useState<ClientRecord[]>([]);
  const [jobs, setJobs] = useState<WorkerJobRecord[]>([]);
  const [usage, setUsage] = useState<UsageSummaryRecord | null>(null);
  const [selectedClientId, setSelectedClientId] = useState("");
  const [taskType, setTaskType] = useState<WorkerTaskType>("research");
  const [topic, setTopic] = useState("");
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedJob, setSelectedJob] = useState<WorkerJobRecord | null>(null);

  const loadWorkspaceData = useCallback(async () => {
    if (!activeWorkspace) return;
    setLoading(true);
    setError(null);
    try {
      const [clientList, jobList, usageSummary] = await Promise.all([
        apiClient.listClients(activeWorkspace.workspace_id),
        apiClient.listJobs(activeWorkspace.workspace_id, 20),
        apiClient.getUsage(activeWorkspace.workspace_id),
      ]);
      setClients(clientList);
      setJobs(jobList);
      setUsage(usageSummary);
      if (clientList.length > 0) {
        setSelectedClientId((prev) => prev || clientList[0].id);
      }
      if (jobList.length > 0) {
        setSelectedJob((prev) => prev || jobList[0]);
      }
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : "Failed to load AI worker data.");
    } finally {
      setLoading(false);
    }
  }, [activeWorkspace]);

  useEffect(() => {
    void loadWorkspaceData();
  }, [loadWorkspaceData]);

  async function handleRunJob(e: React.FormEvent) {
    e.preventDefault();
    if (!activeWorkspace || !selectedClientId || !topic.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      const idemKey =
        typeof crypto !== "undefined" && "randomUUID" in crypto
          ? crypto.randomUUID()
          : `job-${Date.now()}`;
      const created = await apiClient.runWorkerJob(
        activeWorkspace.workspace_id,
        selectedClientId,
        taskType,
        { topic: topic.trim() },
        idemKey
      );
      setTopic("");
      setSelectedJob(created);
      await loadWorkspaceData();
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : "Worker job execution failed.");
    } finally {
      setSubmitting(false);
    }
  }

  if (!activeWorkspace) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Create a workspace first</CardTitle>
          <CardDescription>
            AI workers run strictly inside a tenant-isolated workspace and client context.
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
          <h1 className="text-2xl font-semibold tracking-tight">AI Specialist Workers</h1>
          <p className="text-sm text-muted-foreground">
            Run schema-validated Research, Writing/SEO/AEO, and Media-Plan jobs routed through
            Metuse Skill Runtime and Meta Muse.
          </p>
        </div>
        {usage && (
          <Badge variant="outline" className="text-xs">
            {usage.billing_period}: {(usage.total_input_units + usage.total_output_units).toLocaleString()} units · est. ${Number(usage.total_estimated_cost_usd).toFixed(4)}
          </Badge>
        )}
      </div>

      {error && <Alert variant="destructive">{error}</Alert>}

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Sparkles className="h-4 w-4 text-indigo-400" />
              Dispatch Specialist Job
            </CardTitle>
            <CardDescription>
              Outputs are schema-validated and attributed to the selected client.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {clients.length === 0 ? (
              <div className="space-y-3 text-sm text-muted-foreground">
                <p>Add a client before running AI specialist jobs.</p>
                <Link href="/onboarding/client">
                  <Button size="sm">Add client</Button>
                </Link>
              </div>
            ) : (
              <form onSubmit={handleRunJob} className="space-y-4">
                <div className="space-y-1.5">
                  <Label htmlFor="clientSelect">Client</Label>
                  <select
                    id="clientSelect"
                    value={selectedClientId}
                    onChange={(e) => setSelectedClientId(e.target.value)}
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground"
                  >
                    {clients.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.name}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="taskSelect">Specialist Worker</Label>
                  <select
                    id="taskSelect"
                    value={taskType}
                    onChange={(e) => setTaskType(e.target.value as WorkerTaskType)}
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground"
                  >
                    {TASK_OPTIONS.map((opt) => (
                      <option key={opt.value} value={opt.value}>
                        {opt.label} ({opt.skill})
                      </option>
                    ))}
                  </select>
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="topicInput">Brief / Topic</Label>
                  <Input
                    id="topicInput"
                    required
                    minLength={2}
                    placeholder="e.g., Q4 short-form reels strategy for specialty coffee"
                    value={topic}
                    onChange={(e) => setTopic(e.target.value)}
                  />
                </div>

                <Button type="submit" disabled={submitting} className="w-full">
                  {submitting ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin" /> Running worker...
                    </>
                  ) : (
                    <>
                      <Play className="h-4 w-4" /> Run Specialist Job
                    </>
                  )}
                </Button>
              </form>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Recent Jobs ({jobs.length})</CardTitle>
            <CardDescription>
              Click a job to inspect validated artifacts, citations, and usage.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {loading ? (
              <p className="text-sm text-muted-foreground">Loading jobs...</p>
            ) : jobs.length === 0 ? (
              <p className="text-sm text-muted-foreground">No jobs run in this workspace yet.</p>
            ) : (
              jobs.map((job) => (
                <button
                  key={job.id}
                  type="button"
                  onClick={() => setSelectedJob(job)}
                  className="flex w-full items-center justify-between rounded-md border border-border p-3 text-left transition-colors hover:bg-accent/50"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">
                      {String(job.input_payload.topic ?? job.task_type)}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {job.task_type} · {job.skill_id}@v{job.skill_version}
                    </p>
                  </div>
                  <Badge variant={job.status === "succeeded" ? "default" : "secondary"}>
                    {job.status}
                  </Badge>
                </button>
              ))
            )}
          </CardContent>
        </Card>
      </div>

      {selectedJob && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">
              Validated Output · {selectedJob.requested_output_schema}
            </CardTitle>
            <CardDescription>
              Job ID: {selectedJob.id} · Skill: {selectedJob.skill_id}@v{selectedJob.skill_version}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <pre className="max-h-96 overflow-auto rounded-md bg-muted/40 p-4 text-xs">
              {JSON.stringify(selectedJob.output_payload, null, 2)}
            </pre>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
